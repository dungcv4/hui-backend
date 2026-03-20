"""
Bills Router - Daily bill management endpoints
Today bills, history, send bill to members
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Optional

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    PaymentStatus, BillSendHistory, BillSendStatus, GlobalBankConfig,
    require_role, get_vietnam_today_range, logger
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/bills", tags=["Bills"])


@router.get("/today")
async def get_today_bills(
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy danh sách TẤT CẢ members cần đóng hôm nay"""
    try:
        today_start, today_end = get_vietnam_today_range()
        
        schedules_today = db.query(HuiSchedule).join(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiSchedule.due_date >= today_start,
            HuiSchedule.due_date < today_end
        ).all()
        
        if not schedules_today:
            return {
                "bill_date": today_start.strftime("%Y-%m-%d"),
                "members": [],
                "summary": {
                    "total_members": 0, "total_amount": 0,
                    "with_telegram": 0, "without_telegram": 0,
                    "bills_sent": 0, "bills_pending": 0
                }
            }
        
        member_bills = {}
        
        for schedule in schedules_today:
            group = db.query(HuiGroup).filter(HuiGroup.id == schedule.hui_group_id).first()
            if not group:
                continue
            
            memberships = db.query(HuiMembership).filter(
                HuiMembership.hui_group_id == schedule.hui_group_id,
                HuiMembership.is_active == True
            ).all()
            
            for membership in memberships:
                if schedule.receiver_membership_id and str(schedule.receiver_membership_id) == str(membership.id):
                    continue
                
                existing_payment = db.query(Payment).filter(
                    Payment.membership_id == membership.id,
                    Payment.schedule_id == schedule.id,
                    Payment.payment_status == PaymentStatus.VERIFIED
                ).first()
                
                if existing_payment:
                    continue
                
                member = db.query(Member).filter(Member.id == membership.member_id).first()
                if not member:
                    continue
                
                # Tính tiền đúng theo logic hụi sống (gốc hoặc gốc+lãi)
                amount = calculate_member_payment_amount(group, membership, schedule.cycle_number)
                member_id = str(member.id)
                
                if member_id not in member_bills:
                    member_bills[member_id] = {
                        "member_id": member_id,
                        "member_name": member.name,
                        "member_phone": member.phone,
                        "telegram_linked": member.telegram_chat_id is not None,
                        "telegram_chat_id": member.telegram_chat_id,
                        "total_amount": 0,
                        "items": [],
                        "items_count": 0,
                        "bill_type": "single",
                        "bill_sent": False,
                        "bill_status": "pending",
                        "last_sent_at": None
                    }
                
                member_bills[member_id]["total_amount"] += amount
                member_bills[member_id]["items"].append({
                    "hui_group_id": str(group.id),
                    "hui_group_name": group.name,
                    "cycle_number": schedule.cycle_number,
                    "total_cycles": group.total_cycles,
                    "amount": amount,
                    "slot_count": membership.slot_count or 1,
                    "payment_code": membership.payment_code,
                    "schedule_id": str(schedule.id),
                    "membership_id": str(membership.id)
                })
                member_bills[member_id]["items_count"] = len(member_bills[member_id]["items"])
                
                if member_bills[member_id]["items_count"] > 1:
                    member_bills[member_id]["bill_type"] = "batch"
        
        for member_id, info in member_bills.items():
            history = db.query(BillSendHistory).filter(
                BillSendHistory.owner_id == current_user.id,
                BillSendHistory.member_id == member_id,
                BillSendHistory.bill_date >= today_start,
                BillSendHistory.bill_date < today_end,
                BillSendHistory.status == BillSendStatus.SENT
            ).first()
            
            if history:
                info["bill_sent"] = True
                info["bill_status"] = "sent"
                info["last_sent_at"] = history.sent_at.isoformat() if history.sent_at else None
        
        members_list = sorted(
            member_bills.values(),
            key=lambda x: (x["bill_sent"], -x["total_amount"])
        )
        
        with_telegram = len([m for m in members_list if m["telegram_linked"]])
        without_telegram = len([m for m in members_list if not m["telegram_linked"]])
        bills_sent = len([m for m in members_list if m["bill_sent"]])
        bills_pending = len([m for m in members_list if not m["bill_sent"]])
        
        return {
            "bill_date": today_start.strftime("%Y-%m-%d"),
            "members": members_list,
            "summary": {
                "total_members": len(members_list),
                "total_amount": sum(m["total_amount"] for m in members_list),
                "with_telegram": with_telegram,
                "without_telegram": without_telegram,
                "bills_sent": bills_sent,
                "bills_pending": bills_pending
            }
        }
    
    except Exception as e:
        logger.error(f"Error getting today bills: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_bill_history(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    member_id: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy lịch sử gửi bill"""
    try:
        query = db.query(BillSendHistory).filter(
            BillSendHistory.owner_id == current_user.id
        )
        
        if date_from:
            query = query.filter(BillSendHistory.bill_date >= date_from)
        if date_to:
            query = query.filter(BillSendHistory.bill_date <= date_to)
        if member_id:
            query = query.filter(BillSendHistory.member_id == member_id)
        if status:
            query = query.filter(BillSendHistory.status == status)
        
        history = query.order_by(BillSendHistory.sent_at.desc()).limit(100).all()
        
        result = []
        for h in history:
            member = db.query(Member).filter(Member.id == h.member_id).first()
            result.append({
                "id": h.id,
                "member_id": h.member_id,
                "member_name": member.name if member else "N/A",
                "member_phone": member.phone if member else "",
                "bill_date": h.bill_date.strftime("%Y-%m-%d") if h.bill_date else None,
                "bill_type": h.bill_type,
                "total_amount": h.total_amount,
                "items_count": h.items_count,
                "status": h.status.value,
                "sent_via": h.sent_via,
                "sent_at": h.sent_at.isoformat() if h.sent_at else None,
                "error_message": h.error_message
            })
        
        return {"history": result, "total": len(result)}
    
    except Exception as e:
        logger.error(f"Error getting bill history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
