"""
Exports Router - PDF Bill and Excel export endpoints
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import urllib.parse
import unicodedata

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    PaymentStatus, WebhookEvent, require_role, get_current_user,
    get_vietnam_today_range, logger
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/export", tags=["Exports"])


@router.get("/members/{hui_group_id}")
async def export_members_excel(
    hui_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Export members list to Excel"""
    try:
        from excel_service import generate_members_excel
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        memberships = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == hui_group_id
        ).all()
        
        members_data = []
        for membership in memberships:
            member = db.query(Member).filter(Member.id == membership.member_id).first()
            if member:
                members_data.append({
                    'name': member.name,
                    'phone': member.phone,
                    'slot_count': membership.slot_count,
                    'credit_score': membership.credit_score,
                    'risk_level': membership.risk_level.value if membership.risk_level else 'low',
                    'is_active': membership.is_active,
                    'notes': membership.notes
                })
        
        excel_buffer = generate_members_excel(members_data, hui_group.name)
        
        filename = f"ThanhVien_{hui_group.name}_{datetime.now().strftime('%Y%m%d')}.xlsx".replace(" ", "_")
        
        return StreamingResponse(
            excel_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting members: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/payments/{hui_group_id}")
async def export_payments_excel(
    hui_group_id: str,
    cycle_number: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Export payment history to Excel"""
    try:
        from excel_service import generate_payments_excel
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        query = db.query(Payment).filter(Payment.hui_group_id == hui_group_id)
        
        if cycle_number:
            schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == hui_group_id,
                HuiSchedule.cycle_number == cycle_number
            ).first()
            if schedule:
                query = query.filter(Payment.schedule_id == schedule.id)
        
        payments = query.all()
        
        payments_data = []
        for payment in payments:
            membership = db.query(HuiMembership).filter(
                HuiMembership.id == payment.membership_id
            ).first()
            member = db.query(Member).filter(Member.id == membership.member_id).first() if membership else None
            
            payments_data.append({
                'member_name': member.name if member else 'N/A',
                'amount': payment.amount,
                'reference_code': payment.reference_code,
                'payment_method': payment.payment_method.value if payment.payment_method else 'transfer',
                'payment_status': payment.payment_status.value if payment.payment_status else 'pending',
                'due_date': payment.due_date.isoformat() if payment.due_date else None,
                'paid_at': payment.paid_at.isoformat() if payment.paid_at else None,
                'notes': payment.notes
            })
        
        excel_buffer = generate_payments_excel(payments_data, hui_group.name, cycle_number)
        
        filename = f"ThanhToan_{hui_group.name}"
        if cycle_number:
            filename += f"_Ky{cycle_number}"
        filename += f"_{datetime.now().strftime('%Y%m%d')}.xlsx".replace(" ", "_")
        
        return StreamingResponse(
            excel_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting payments: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transactions")
async def export_transactions_excel(
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Export webhook transactions to Excel"""
    try:
        from excel_service import generate_transactions_excel
        import json
        
        query = db.query(WebhookEvent)
        
        if status:
            query = query.filter(WebhookEvent.status == status)
        
        if from_date:
            query = query.filter(WebhookEvent.created_at >= from_date)
        if to_date:
            query = query.filter(WebhookEvent.created_at <= to_date)
        
        events = query.order_by(WebhookEvent.created_at.desc()).limit(500).all()
        
        transactions_data = []
        for event in events:
            try:
                payload = json.loads(event.payload) if event.payload else {}
            except:
                payload = {}
            
            transactions_data.append({
                'id': event.id,
                'external_id': event.external_id,
                'amount': payload.get('transferAmount', 0),
                'content': payload.get('content', ''),
                'bank': payload.get('gateway', ''),
                'account': payload.get('accountNumber', ''),
                'status': event.status,
                'created_at': event.created_at.isoformat() if event.created_at else None
            })
        
        excel_buffer = generate_transactions_excel(transactions_data)
        
        filename = f"GiaoDich_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return StreamingResponse(
            excel_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    except Exception as e:
        logger.error(f"Error exporting transactions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bill/{member_id}")
async def export_member_bill_pdf(
    member_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF]))
):
    """Export consolidated bill PDF for a member"""
    try:
        from pdf_service import generate_consolidated_bill_pdf
        
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        today_start, today_end = get_vietnam_today_range()
        
        memberships = db.query(HuiMembership).join(HuiGroup).filter(
            HuiMembership.member_id == member_id,
            HuiMembership.is_active == True,
            HuiGroup.owner_id == current_user.id
        ).all()
        
        if not memberships:
            raise HTTPException(status_code=404, detail="Thành viên không có trong dây hụi nào")
        
        bill_items = []
        total_amount = 0
        
        for membership in memberships:
            group = db.query(HuiGroup).filter(HuiGroup.id == membership.hui_group_id).first()
            schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.due_date >= today_start,
                HuiSchedule.due_date < today_end
            ).first()
            
            if not schedule:
                continue
            
            if schedule.receiver_membership_id and str(schedule.receiver_membership_id) == str(membership.id):
                continue
            
            existing = db.query(Payment).filter(
                Payment.membership_id == membership.id,
                Payment.schedule_id == schedule.id,
                Payment.payment_status == PaymentStatus.VERIFIED
            ).first()
            
            if existing:
                continue
            
            amount = float(group.amount_per_cycle) * (membership.slot_count or 1)
            total_amount += amount
            
            bill_items.append({
                'hui_group_name': group.name,
                'cycle_number': schedule.cycle_number,
                'total_cycles': group.total_cycles,
                'slot_count': membership.slot_count or 1,
                'amount': amount,
                'payment_code': membership.payment_code,
                'due_date': schedule.due_date,
                'bank_name': group.bank_name,
                'bank_account_number': group.bank_account_number,
                'bank_account_name': group.bank_account_name,
            })
        
        if not bill_items:
            raise HTTPException(status_code=404, detail="Không có khoản nào cần đóng hôm nay")
        
        owner = db.query(User).filter(User.id == current_user.id).first()
        
        pdf_buffer = generate_consolidated_bill_pdf(
            member_name=member.name,
            member_phone=member.phone,
            bill_items=bill_items,
            total_amount=total_amount,
            bill_date=today_start,
            owner_name=owner.name if owner else None,
            owner_phone=owner.phone if owner else None
        )
        
        safe_name = unicodedata.normalize('NFKD', member.name).encode('ascii', 'ignore').decode('ascii')
        date_str = today_start.strftime('%Y%m%d')
        filename = f"Bill_{date_str}_{safe_name}.pdf".replace(" ", "_")
        
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""}
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating bill PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))
