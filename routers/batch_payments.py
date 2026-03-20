"""
Batch Payments Router - Batch payment management endpoints
Gộp nhiều payments thành 1 QR để thanh toán
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel as PydanticBaseModel
from typing import Optional, List
from datetime import datetime
import hashlib
from urllib.parse import quote

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, Payment, PaymentStatus,
    PaymentMethod, HuiSchedule, PaymentBatch, BatchPayment, BatchStatus,
    require_role, get_current_user, get_vietnam_now, get_vietnam_today_range,
    logger, generate_reference_code
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/batches", tags=["Batch Payments"])


def generate_batch_code(member_id: str, date: datetime) -> str:
    """Generate unique batch code: BATCH_YYYYMMDD_<short_uuid>"""
    date_str = date.strftime("%Y%m%d")
    unique_part = hashlib.md5(f"{member_id}_{date_str}_{datetime.now().timestamp()}".encode()).hexdigest()[:8]
    return f"BATCH_{date_str}_{unique_part}"


def get_sepay_bank_code(bank_name: str) -> str:
    """Map tên ngân hàng sang mã Sepay"""
    bank_mapping = {
        "vietcombank": "VCB", "vcb": "VCB", "techcombank": "TCB", "tcb": "TCB",
        "mbbank": "MB", "mb": "MB", "vietinbank": "CTG", "bidv": "BIDV",
        "agribank": "AGR", "vpbank": "VPB", "acb": "ACB", "sacombank": "STB",
        "hdbank": "HDB", "tpbank": "TPB", "ocb": "OCB", "msb": "MSB",
        "vib": "VIB", "shb": "SHB", "eximbank": "EIB", "seabank": "SSB",
    }
    return bank_mapping.get(bank_name.lower().replace(" ", ""), bank_name.upper()[:3])


async def _get_batch_detail(batch: PaymentBatch, db: Session) -> dict:
    """Helper to get batch detail with all items"""
    member = db.query(Member).filter(Member.id == batch.member_id).first()
    items = db.query(BatchPayment).filter(BatchPayment.batch_id == batch.id).all()
    
    items_detail = []
    for item in items:
        items_detail.append({
            "id": item.id,
            "payment_id": item.payment_id,
            "hui_group_id": item.hui_group_id,
            "hui_group_name": item.hui_group_name,
            "cycle_number": item.cycle_number,
            "amount": item.amount,
            "is_verified": item.is_verified,
            "verified_at": item.verified_at.isoformat() if item.verified_at else None
        })
    
    return {
        "id": batch.id,
        "batch_code": batch.batch_code,
        "batch_date": batch.batch_date.isoformat() if batch.batch_date else None,
        "member_id": batch.member_id,
        "member_name": member.name if member else "N/A",
        "member_phone": member.phone if member else "",
        "total_amount": batch.total_amount,
        "received_amount": batch.received_amount or 0,
        "difference": batch.difference or 0,
        "status": batch.status.value,
        "qr_url": batch.qr_data,
        "transaction_id": batch.transaction_id,
        "transaction_content": batch.transaction_content,
        "transaction_bank": batch.transaction_bank,
        "received_at": batch.received_at.isoformat() if batch.received_at else None,
        "resolution_note": batch.resolution_note,
        "resolved_at": batch.resolved_at.isoformat() if batch.resolved_at else None,
        "items": items_detail,
        "items_count": len(items_detail),
        "created_at": batch.created_at.isoformat() if batch.created_at else None
    }


@router.get("/member/{member_id}/today")
async def get_or_create_daily_batch(
    member_id: str,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy hoặc tạo batch cho member trong ngày hôm nay"""
    try:
        today_start, today_end = get_vietnam_today_range()
        
        existing_batch = db.query(PaymentBatch).filter(
            PaymentBatch.member_id == member_id,
            PaymentBatch.owner_id == current_user.id,
            PaymentBatch.batch_date >= today_start,
            PaymentBatch.batch_date < today_end
        ).first()
        
        if existing_batch:
            return await _get_batch_detail(existing_batch, db)
        
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        memberships = db.query(HuiMembership).join(HuiGroup).filter(
            HuiMembership.member_id == member_id,
            HuiMembership.is_active == True,
            HuiGroup.owner_id == current_user.id
        ).all()
        
        membership_ids = [m.id for m in memberships]
        
        if not membership_ids:
            raise HTTPException(status_code=404, detail="Thành viên không có dây hụi nào cần đóng")
        
        schedules_today = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id.in_([m.hui_group_id for m in memberships]),
            HuiSchedule.due_date >= today_start,
            HuiSchedule.due_date < today_end
        ).all()
        
        if not schedules_today:
            raise HTTPException(status_code=404, detail="Không có kỳ nào đến hạn hôm nay")
        
        payments_to_batch = []
        total_amount = 0
        
        for schedule in schedules_today:
            pot_receiver_id = str(schedule.receiver_membership_id) if schedule.receiver_membership_id else None
            
            for membership in memberships:
                if str(membership.hui_group_id) != str(schedule.hui_group_id):
                    continue
                if pot_receiver_id and str(membership.id) == pot_receiver_id:
                    continue
                
                existing_payment = db.query(Payment).filter(
                    Payment.membership_id == membership.id,
                    Payment.schedule_id == schedule.id
                ).first()
                
                if existing_payment and existing_payment.payment_status == PaymentStatus.VERIFIED:
                    continue
                
                group = db.query(HuiGroup).filter(HuiGroup.id == schedule.hui_group_id).first()
                if not group:
                    continue
                
                # Tính tiền đúng theo logic hụi sống
                amount = calculate_member_payment_amount(group, membership, schedule.cycle_number)
                
                if not existing_payment:
                    ref_code = generate_reference_code(schedule.hui_group_id, membership.member_id, schedule.cycle_number)
                    existing_payment = Payment(
                        hui_group_id=schedule.hui_group_id,
                        membership_id=membership.id,
                        schedule_id=schedule.id,
                        amount=amount,
                        payment_method=PaymentMethod.QR_CODE,
                        payment_status=PaymentStatus.PENDING,
                        reference_code=ref_code,
                        due_date=schedule.due_date
                    )
                    db.add(existing_payment)
                    db.flush()
                
                payments_to_batch.append({
                    "payment": existing_payment,
                    "group": group,
                    "schedule": schedule,
                    "amount": amount
                })
                total_amount += amount
        
        if not payments_to_batch:
            raise HTTPException(status_code=404, detail="Không có khoản thanh toán nào cần đóng hôm nay")
        
        batch_code = generate_batch_code(member_id, today_start)
        
        batch = PaymentBatch(
            member_id=member_id,
            owner_id=current_user.id,
            batch_date=today_start,
            batch_code=batch_code,
            total_amount=total_amount,
            status=BatchStatus.PENDING
        )
        db.add(batch)
        db.flush()
        
        for item in payments_to_batch:
            batch_item = BatchPayment(
                batch_id=batch.id,
                payment_id=item["payment"].id,
                hui_group_id=item["group"].id,
                hui_group_name=item["group"].name,
                cycle_number=item["schedule"].cycle_number,
                amount=item["amount"]
            )
            db.add(batch_item)
        
        owner_group = db.query(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiGroup.bank_account_number != None
        ).first()
        
        if owner_group and owner_group.bank_account_number and owner_group.bank_name:
            account_no = owner_group.bank_account_number
            bank_code = get_sepay_bank_code(owner_group.bank_name)
            
            if account_no and bank_code:
                encoded_content = quote(batch_code)
                qr_url = f"https://qr.sepay.vn/img?acc={account_no}&bank={bank_code}&amount={int(total_amount)}&des={encoded_content}&template=compact"
                batch.qr_data = qr_url
        
        db.commit()
        return await _get_batch_detail(batch, db)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating daily batch: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/today")
async def get_today_batches(
    status: Optional[str] = None,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy danh sách tất cả batches hôm nay của chủ hụi"""
    try:
        today_start, today_end = get_vietnam_today_range()
        
        query = db.query(PaymentBatch).filter(
            PaymentBatch.owner_id == current_user.id,
            PaymentBatch.batch_date >= today_start,
            PaymentBatch.batch_date < today_end
        )
        
        if status:
            query = query.filter(PaymentBatch.status == status)
        
        batches = query.order_by(PaymentBatch.created_at.desc()).all()
        
        result = []
        for batch in batches:
            detail = await _get_batch_detail(batch, db)
            result.append(detail)
        
        return {
            "batches": result,
            "stats": {
                "total": len(batches),
                "pending": len([b for b in batches if b.status == BatchStatus.PENDING]),
                "paid": len([b for b in batches if b.status == BatchStatus.PAID]),
                "review": len([b for b in batches if b.status == BatchStatus.REVIEW])
            }
        }
    
    except Exception as e:
        logger.error(f"Error getting today batches: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review")
async def get_batches_need_review(
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy danh sách batches cần xử lý (REVIEW status)"""
    try:
        batches = db.query(PaymentBatch).filter(
            PaymentBatch.owner_id == current_user.id,
            PaymentBatch.status == BatchStatus.REVIEW
        ).order_by(PaymentBatch.received_at.desc()).all()
        
        result = []
        for batch in batches:
            member = db.query(Member).filter(Member.id == batch.member_id).first()
            items = db.query(BatchPayment).filter(BatchPayment.batch_id == batch.id).all()
            
            verified_amount = sum(item.amount for item in items if item.is_verified)
            unverified_amount = sum(item.amount for item in items if not item.is_verified)
            
            diff = (batch.received_amount or 0) - batch.total_amount
            
            if diff < 0:
                situation = "THIẾU"
                situation_detail = f"Thiếu {abs(diff):,.0f}đ"
            elif diff > 0:
                situation = "DƯ"
                situation_detail = f"Dư {diff:,.0f}đ"
            else:
                situation = "KHỚP"
                situation_detail = "Số tiền khớp"
            
            items_detail = [{
                "id": item.id,
                "payment_id": item.payment_id,
                "hui_group_name": item.hui_group_name,
                "cycle_number": item.cycle_number,
                "amount": item.amount,
                "is_verified": item.is_verified,
            } for item in items]
            
            result.append({
                "id": batch.id,
                "batch_code": batch.batch_code,
                "batch_date": batch.batch_date.isoformat() if batch.batch_date else None,
                "member_name": member.name if member else "N/A",
                "member_phone": member.phone if member else "",
                "total_amount": batch.total_amount,
                "received_amount": batch.received_amount or 0,
                "difference": diff,
                "situation": situation,
                "situation_detail": situation_detail,
                "transaction_bank": batch.transaction_bank,
                "received_at": batch.received_at.isoformat() if batch.received_at else None,
                "items": items_detail,
                "items_count": len(items_detail),
                "verified_count": len([i for i in items if i.is_verified])
            })
        
        return {"batches": result, "total_review": len(result)}
    
    except Exception as e:
        logger.error(f"Error getting review batches: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BatchResolveRequest(PydanticBaseModel):
    action: str  # "verify_all", "verify_partial", "reject"
    verified_item_ids: Optional[List[str]] = None
    note: Optional[str] = ""


@router.post("/{batch_id}/resolve")
async def resolve_batch(
    batch_id: str,
    request: BatchResolveRequest,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Chủ hụi xử lý batch cần review"""
    try:
        batch = db.query(PaymentBatch).filter(
            PaymentBatch.id == batch_id,
            PaymentBatch.owner_id == current_user.id
        ).first()
        
        if not batch:
            raise HTTPException(status_code=404, detail="Không tìm thấy batch")
        
        items = db.query(BatchPayment).filter(BatchPayment.batch_id == batch_id).all()
        now = get_vietnam_now()
        
        if request.action == "verify_all":
            for item in items:
                item.is_verified = True
                item.verified_at = now
                
                payment = db.query(Payment).filter(Payment.id == item.payment_id).first()
                if payment:
                    payment.payment_status = PaymentStatus.VERIFIED
                    payment.verified_at = now
                    payment.paid_at = batch.received_at or now
            
            batch.status = BatchStatus.RESOLVED
            batch.resolved_by = current_user.id
            batch.resolved_at = now
            batch.resolution_note = request.note or "Xác nhận tất cả"
            
            db.commit()
            return {"success": True, "message": f"Đã xác nhận {len(items)} khoản thanh toán"}
        
        elif request.action == "verify_partial":
            if not request.verified_item_ids:
                raise HTTPException(status_code=400, detail="Cần chọn ít nhất 1 khoản")
            
            verified_count = 0
            for item in items:
                if item.id in request.verified_item_ids:
                    item.is_verified = True
                    item.verified_at = now
                    verified_count += 1
                    
                    payment = db.query(Payment).filter(Payment.id == item.payment_id).first()
                    if payment:
                        payment.payment_status = PaymentStatus.VERIFIED
                        payment.verified_at = now
            
            all_verified = all(item.is_verified for item in items)
            batch.status = BatchStatus.RESOLVED if all_verified else BatchStatus.PARTIAL
            batch.resolved_by = current_user.id
            batch.resolved_at = now
            batch.resolution_note = request.note or f"Xác nhận một phần: {verified_count}/{len(items)}"
            
            db.commit()
            return {"success": True, "message": f"Đã xác nhận {verified_count}/{len(items)} khoản"}
        
        elif request.action == "reject":
            batch.status = BatchStatus.RESOLVED
            batch.resolved_by = current_user.id
            batch.resolved_at = now
            batch.resolution_note = request.note or "Từ chối"
            
            db.commit()
            return {"success": True, "message": "Đã từ chối batch"}
        
        else:
            raise HTTPException(status_code=400, detail="Action không hợp lệ")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving batch: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
