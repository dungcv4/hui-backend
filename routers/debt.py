"""
Debt Management Router - Quản lý nợ & phạt trễ hạn
Bao gồm: xem tổng nợ, chi tiết nợ, ghi nhận thanh toán, miễn nợ, tính lại phạt
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
import logging
import math

from models import (
    DebtRecord, DebtStatus, Payment, PaymentStatus, 
    HuiGroup, HuiMembership, HuiSchedule, Member, User, RiskLevel
)
from schemas import DebtRecordResponse, DebtSummaryResponse, DebtPayRequest, DebtWaiveRequest
from routers.dependencies import get_db, get_current_user, get_vietnam_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debt", tags=["Debt Management"])


def calculate_late_fee(group: HuiGroup, original_amount: float, days_overdue: int) -> float:
    """Tính tiền phạt trễ hạn dựa trên config dây hụi"""
    if group.late_fee_type == "none" or group.late_fee_value <= 0:
        return 0
    
    # Trừ grace days
    effective_days = max(0, days_overdue - (group.late_fee_grace_days or 0))
    if effective_days <= 0:
        return 0
    
    fee = 0
    if group.late_fee_type == "percentage":
        # Phạt % trên số tiền gốc (1 lần)
        fee = original_amount * group.late_fee_value / 100
    elif group.late_fee_type == "fixed":
        # Phạt cố định (1 lần)
        fee = group.late_fee_value
    elif group.late_fee_type == "daily_percentage":
        # Phạt % mỗi ngày
        fee = original_amount * group.late_fee_value / 100 * effective_days
    elif group.late_fee_type == "daily_fixed":
        # Phạt cố định mỗi ngày
        fee = group.late_fee_value * effective_days
    
    # Cap phạt tối đa
    if group.late_fee_max_amount and group.late_fee_max_amount > 0:
        fee = min(fee, group.late_fee_max_amount)
    
    return round(fee, 0)


def _debt_to_dict(debt: DebtRecord, member: Member = None, group: HuiGroup = None) -> dict:
    """Convert DebtRecord to response dict"""
    return {
        "id": debt.id,
        "owner_id": debt.owner_id,
        "member_id": debt.member_id,
        "membership_id": debt.membership_id,
        "payment_id": debt.payment_id,
        "hui_group_id": debt.hui_group_id,
        "original_amount": debt.original_amount,
        "late_fee": debt.late_fee,
        "total_amount": debt.total_amount,
        "paid_amount": debt.paid_amount,
        "remaining_amount": debt.remaining_amount,
        "due_date": debt.due_date.isoformat() if debt.due_date else None,
        "days_overdue": debt.days_overdue,
        "cycle_number": debt.cycle_number,
        "status": debt.status.value if debt.status else "outstanding",
        "notes": debt.notes,
        "last_reminder_at": debt.last_reminder_at.isoformat() if debt.last_reminder_at else None,
        "reminder_count": debt.reminder_count or 0,
        "resolved_at": debt.resolved_at.isoformat() if debt.resolved_at else None,
        "created_at": debt.created_at.isoformat() if debt.created_at else None,
        "member_name": member.name if member else None,
        "member_phone": member.phone if member else None,
        "hui_group_name": group.name if group else None,
    }


@router.get("/summary")
async def get_debt_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Tổng quan nợ: tổng nợ, số member nợ, nợ theo dây hụi"""
    try:
        # Tổng nợ outstanding
        outstanding_debts = db.query(DebtRecord).filter(
            DebtRecord.owner_id == current_user.id,
            DebtRecord.status.in_([DebtStatus.OUTSTANDING, DebtStatus.PARTIAL])
        ).all()
        
        total_outstanding = sum(d.remaining_amount for d in outstanding_debts)
        total_late_fees = sum(d.late_fee for d in outstanding_debts)
        
        # Số member có nợ (unique)
        member_ids = set(d.member_id for d in outstanding_debts)
        
        # Nợ theo dây hụi
        group_debts = {}
        for d in outstanding_debts:
            if d.hui_group_id not in group_debts:
                group = db.query(HuiGroup).filter(HuiGroup.id == d.hui_group_id).first()
                group_debts[d.hui_group_id] = {
                    "hui_group_id": d.hui_group_id,
                    "hui_group_name": group.name if group else "N/A",
                    "total_amount": 0,
                    "total_late_fees": 0,
                    "debt_count": 0,
                }
            group_debts[d.hui_group_id]["total_amount"] += d.remaining_amount
            group_debts[d.hui_group_id]["total_late_fees"] += d.late_fee
            group_debts[d.hui_group_id]["debt_count"] += 1
        
        # 5 khoản nợ gần nhất
        recent = db.query(DebtRecord).filter(
            DebtRecord.owner_id == current_user.id,
            DebtRecord.status.in_([DebtStatus.OUTSTANDING, DebtStatus.PARTIAL])
        ).order_by(DebtRecord.created_at.desc()).limit(5).all()
        
        recent_list = []
        for d in recent:
            member = db.query(Member).filter(Member.id == d.member_id).first()
            group = db.query(HuiGroup).filter(HuiGroup.id == d.hui_group_id).first()
            recent_list.append(_debt_to_dict(d, member, group))
        
        return {
            "total_outstanding": total_outstanding,
            "total_late_fees": total_late_fees,
            "total_debt_count": len(outstanding_debts),
            "members_with_debt": len(member_ids),
            "debts_by_group": list(group_debts.values()),
            "recent_debts": recent_list,
        }
        
    except Exception as e:
        logger.error(f"Error getting debt summary: {e}")
        raise HTTPException(status_code=500, detail="Lỗi khi lấy tổng quan nợ")


@router.get("/records")
async def get_debt_records(
    status: str = Query(None, description="Filter: outstanding, partial, paid, waived"),
    hui_group_id: str = Query(None),
    member_id: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Danh sách chi tiết nợ (có filter + pagination)"""
    try:
        query = db.query(DebtRecord).filter(DebtRecord.owner_id == current_user.id)
        
        if status:
            try:
                debt_status = DebtStatus(status)
                query = query.filter(DebtRecord.status == debt_status)
            except ValueError:
                pass
        
        if hui_group_id:
            query = query.filter(DebtRecord.hui_group_id == hui_group_id)
        
        if member_id:
            query = query.filter(DebtRecord.member_id == member_id)
        
        total = query.count()
        debts = query.order_by(DebtRecord.days_overdue.desc()).offset(skip).limit(limit).all()
        
        # Batch load members + groups
        member_ids = list(set(d.member_id for d in debts))
        group_ids = list(set(d.hui_group_id for d in debts))
        
        members_map = {}
        if member_ids:
            members = db.query(Member).filter(Member.id.in_(member_ids)).all()
            members_map = {m.id: m for m in members}
        
        groups_map = {}
        if group_ids:
            groups = db.query(HuiGroup).filter(HuiGroup.id.in_(group_ids)).all()
            groups_map = {g.id: g for g in groups}
        
        records = []
        for d in debts:
            member = members_map.get(d.member_id)
            group = groups_map.get(d.hui_group_id)
            records.append(_debt_to_dict(d, member, group))
        
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "records": records,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting debt records: {e}")
        raise HTTPException(status_code=500, detail="Lỗi khi lấy danh sách nợ")


@router.post("/records/{debt_id}/pay")
async def pay_debt(
    debt_id: str,
    request: DebtPayRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ghi nhận thanh toán nợ (toàn bộ hoặc 1 phần)"""
    try:
        debt = db.query(DebtRecord).filter(
            DebtRecord.id == debt_id,
            DebtRecord.owner_id == current_user.id
        ).first()
        
        if not debt:
            raise HTTPException(status_code=404, detail="Không tìm thấy khoản nợ")
        
        if debt.status in [DebtStatus.PAID, DebtStatus.WAIVED]:
            raise HTTPException(status_code=400, detail="Khoản nợ đã được xử lý")
        
        pay_amount = request.amount
        if pay_amount > debt.remaining_amount:
            pay_amount = debt.remaining_amount
        
        debt.paid_amount += pay_amount
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        
        now = get_vietnam_now()
        
        if debt.remaining_amount <= 0:
            debt.remaining_amount = 0
            debt.status = DebtStatus.PAID
            debt.resolved_at = now
            debt.resolved_by = current_user.id
            
            # Cập nhật payment gốc → verified
            payment = db.query(Payment).filter(Payment.id == debt.payment_id).first()
            if payment:
                payment.payment_status = PaymentStatus.VERIFIED
                payment.paid_at = now
                payment.verified_at = now
        else:
            debt.status = DebtStatus.PARTIAL
        
        if request.notes:
            debt.notes = (debt.notes + "\n" if debt.notes else "") + f"[{now.strftime('%d/%m/%Y')}] Thanh toán {pay_amount:,.0f}đ - {request.notes}"
        else:
            debt.notes = (debt.notes + "\n" if debt.notes else "") + f"[{now.strftime('%d/%m/%Y')}] Thanh toán {pay_amount:,.0f}đ"
        
        debt.updated_at = now
        db.commit()
        
        member = db.query(Member).filter(Member.id == debt.member_id).first()
        group = db.query(HuiGroup).filter(HuiGroup.id == debt.hui_group_id).first()
        
        return {
            "message": f"Đã ghi nhận thanh toán {pay_amount:,.0f}đ",
            "record": _debt_to_dict(debt, member, group),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error paying debt: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Lỗi khi ghi nhận thanh toán nợ")


@router.post("/records/{debt_id}/waive")
async def waive_debt(
    debt_id: str,
    request: DebtWaiveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Miễn nợ / bỏ qua khoản nợ"""
    try:
        debt = db.query(DebtRecord).filter(
            DebtRecord.id == debt_id,
            DebtRecord.owner_id == current_user.id
        ).first()
        
        if not debt:
            raise HTTPException(status_code=404, detail="Không tìm thấy khoản nợ")
        
        if debt.status in [DebtStatus.PAID, DebtStatus.WAIVED]:
            raise HTTPException(status_code=400, detail="Khoản nợ đã được xử lý")
        
        now = get_vietnam_now()
        
        debt.status = DebtStatus.WAIVED
        debt.remaining_amount = 0
        debt.resolved_at = now
        debt.resolved_by = current_user.id
        debt.notes = (debt.notes + "\n" if debt.notes else "") + f"[{now.strftime('%d/%m/%Y')}] Miễn nợ bởi chủ hụi" + (f" - {request.notes}" if request.notes else "")
        debt.updated_at = now
        
        # Cập nhật payment gốc
        payment = db.query(Payment).filter(Payment.id == debt.payment_id).first()
        if payment:
            payment.payment_status = PaymentStatus.VERIFIED
            payment.verified_at = now
            payment.notes = (payment.notes or "") + " [Miễn nợ]"
        
        db.commit()
        
        member = db.query(Member).filter(Member.id == debt.member_id).first()
        group = db.query(HuiGroup).filter(HuiGroup.id == debt.hui_group_id).first()
        
        return {
            "message": "Đã miễn nợ thành công",
            "record": _debt_to_dict(debt, member, group),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waiving debt: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Lỗi khi miễn nợ")


@router.post("/recalculate-fees")
async def recalculate_late_fees(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Tính lại late fee cho tất cả nợ outstanding (dùng khi thay đổi config phạt)"""
    try:
        debts = db.query(DebtRecord).filter(
            DebtRecord.owner_id == current_user.id,
            DebtRecord.status.in_([DebtStatus.OUTSTANDING, DebtStatus.PARTIAL])
        ).all()
        
        now = get_vietnam_now()
        updated = 0
        
        # Batch load groups
        group_ids = list(set(d.hui_group_id for d in debts))
        groups = db.query(HuiGroup).filter(HuiGroup.id.in_(group_ids)).all() if group_ids else []
        groups_map = {g.id: g for g in groups}
        
        for debt in debts:
            group = groups_map.get(debt.hui_group_id)
            if not group:
                continue
            
            # Tính lại số ngày overdue
            if debt.due_date:
                due_naive = debt.due_date.replace(tzinfo=None) if debt.due_date.tzinfo else debt.due_date
                now_naive = now.replace(tzinfo=None)
                days = (now_naive - due_naive).days
                debt.days_overdue = max(0, days)
            
            # Tính lại phạt
            old_fee = debt.late_fee
            new_fee = calculate_late_fee(group, debt.original_amount, debt.days_overdue)
            
            if new_fee != old_fee:
                debt.late_fee = new_fee
                debt.total_amount = debt.original_amount + new_fee
                debt.remaining_amount = debt.total_amount - debt.paid_amount
                if debt.remaining_amount < 0:
                    debt.remaining_amount = 0
                debt.updated_at = now
                updated += 1
        
        db.commit()
        
        return {
            "message": f"Đã tính lại phạt cho {updated}/{len(debts)} khoản nợ",
            "total_processed": len(debts),
            "total_updated": updated,
        }
        
    except Exception as e:
        logger.error(f"Error recalculating fees: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Lỗi khi tính lại phạt trễ hạn")


@router.post("/check-overdue")
async def trigger_check_overdue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Manually trigger kiểm tra và tạo debt records cho payments quá hạn"""
    try:
        result = _process_overdue_payments(db, current_user.id)
        return result
    except Exception as e:
        logger.error(f"Error checking overdue: {e}")
        raise HTTPException(status_code=500, detail="Lỗi khi kiểm tra quá hạn")


def _process_overdue_payments(db: Session, owner_id: str) -> dict:
    """Core logic: quét payments quá hạn → tạo DebtRecord + tính phạt"""
    now = get_vietnam_now()
    now_naive = now.replace(tzinfo=None)
    
    # Lấy tất cả payments PENDING quá hạn cho owner này
    groups = db.query(HuiGroup).filter(
        HuiGroup.owner_id == owner_id,
        HuiGroup.is_active == True
    ).all()
    group_ids = [g.id for g in groups]
    groups_map = {g.id: g for g in groups}
    
    if not group_ids:
        return {"new_debts": 0, "updated_debts": 0, "overdue_marked": 0}
    
    # Tìm payments PENDING có due_date đã qua
    overdue_payments = db.query(Payment).filter(
        Payment.hui_group_id.in_(group_ids),
        Payment.payment_status == PaymentStatus.PENDING,
        Payment.due_date != None,
        Payment.due_date < now_naive
    ).all()
    
    new_debts = 0
    overdue_marked = 0
    
    for payment in overdue_payments:
        group = groups_map.get(payment.hui_group_id)
        if not group:
            continue
        
        # Tính số ngày quá hạn
        due_naive = payment.due_date.replace(tzinfo=None) if payment.due_date.tzinfo else payment.due_date
        days_overdue = (now_naive - due_naive).days
        
        # Kiểm tra grace days
        grace = group.late_fee_grace_days or 0
        if days_overdue <= grace:
            continue
        
        # Mark payment as OVERDUE
        payment.payment_status = PaymentStatus.OVERDUE
        overdue_marked += 1
        
        # Kiểm tra đã có DebtRecord chưa
        existing = db.query(DebtRecord).filter(DebtRecord.payment_id == payment.id).first()
        if existing:
            # Cập nhật days_overdue và late fee
            existing.days_overdue = days_overdue
            existing.late_fee = calculate_late_fee(group, existing.original_amount, days_overdue)
            existing.total_amount = existing.original_amount + existing.late_fee
            existing.remaining_amount = existing.total_amount - existing.paid_amount
            existing.updated_at = now
            continue
        
        # Lấy membership info
        membership = db.query(HuiMembership).filter(HuiMembership.id == payment.membership_id).first()
        if not membership:
            continue
        
        # Lấy schedule info cho cycle_number
        schedule = db.query(HuiSchedule).filter(HuiSchedule.id == payment.schedule_id).first() if payment.schedule_id else None
        
        # Tính late fee
        late_fee = calculate_late_fee(group, payment.amount, days_overdue)
        total = payment.amount + late_fee
        
        # Tạo DebtRecord mới
        debt = DebtRecord(
            owner_id=owner_id,
            member_id=membership.member_id,
            membership_id=payment.membership_id,
            payment_id=payment.id,
            hui_group_id=payment.hui_group_id,
            original_amount=payment.amount,
            late_fee=late_fee,
            total_amount=total,
            paid_amount=0,
            remaining_amount=total,
            due_date=payment.due_date,
            days_overdue=days_overdue,
            cycle_number=schedule.cycle_number if schedule else None,
            status=DebtStatus.OUTSTANDING,
        )
        db.add(debt)
        new_debts += 1
        
        # Cập nhật credit score trên membership
        membership.total_late_count = (membership.total_late_count or 0) + 1
        membership.total_late_amount = (membership.total_late_amount or 0) + payment.amount
        membership.credit_score = max(0, (membership.credit_score or 100) - 5)
        
        # Cập nhật risk level
        score = membership.credit_score
        if score >= 80:
            membership.risk_level = RiskLevel.LOW
        elif score >= 60:
            membership.risk_level = RiskLevel.MEDIUM
        elif score >= 40:
            membership.risk_level = RiskLevel.HIGH
        else:
            membership.risk_level = RiskLevel.CRITICAL
    
    # Cập nhật debt records đã có nhưng payment đã trả (edge case)
    updated_debts = 0
    existing_outstanding = db.query(DebtRecord).filter(
        DebtRecord.owner_id == owner_id,
        DebtRecord.status == DebtStatus.OUTSTANDING
    ).all()
    
    for debt in existing_outstanding:
        # Tính lại days_overdue
        if debt.due_date:
            due_naive = debt.due_date.replace(tzinfo=None) if debt.due_date.tzinfo else debt.due_date
            days = (now_naive - due_naive).days
            if days != debt.days_overdue:
                group = groups_map.get(debt.hui_group_id)
                if group:
                    debt.days_overdue = max(0, days)
                    debt.late_fee = calculate_late_fee(group, debt.original_amount, debt.days_overdue)
                    debt.total_amount = debt.original_amount + debt.late_fee
                    debt.remaining_amount = debt.total_amount - debt.paid_amount
                    debt.updated_at = now
                    updated_debts += 1
    
    db.commit()
    
    return {
        "new_debts": new_debts,
        "updated_debts": updated_debts,
        "overdue_marked": overdue_marked,
    }
