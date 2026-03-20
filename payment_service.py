"""
Payment Service - Xử lý logic liên quan đến thanh toán
Tách từ server.py để giảm code duplication và tăng maintainability
"""

from sqlalchemy.orm import Session
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

from models import (
    HuiMembership, Payment, PaymentStatus, HuiGroup, HuiSchedule,
    PaymentBatch, BatchPayment, BatchStatus, RiskLevel, AuditLog
)
from utils import safe_json_dumps

logger = logging.getLogger(__name__)
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def get_vietnam_now():
    """Get current time in Vietnam timezone"""
    return datetime.now(VIETNAM_TZ)


def handle_late_payment(
    db: Session,
    membership: HuiMembership,
    payment: Payment,
    days_late: int,
    hui_group: HuiGroup = None,
    schedule: HuiSchedule = None,
    note: str = ""
) -> None:
    """
    Xử lý khi thanh toán trễ hạn:
    - Cập nhật thống kê trễ hạn
    - Giảm điểm tín dụng
    - Cập nhật risk level
    - Thêm ghi chú vào membership
    """
    if days_late <= 0:
        return
    
    # Update late stats
    membership.total_late_count = (membership.total_late_count or 0) + 1
    membership.total_late_amount = (membership.total_late_amount or 0) + payment.amount
    
    # Decrease credit score based on days late (max 20 points penalty)
    score_penalty = min(days_late * 2, 20)
    membership.credit_score = max(0, (membership.credit_score or 100) - score_penalty)
    
    # Update risk level based on credit score
    if membership.credit_score < 40:
        membership.risk_level = RiskLevel.CRITICAL
    elif membership.credit_score < 60:
        membership.risk_level = RiskLevel.HIGH
    elif membership.credit_score < 80:
        membership.risk_level = RiskLevel.MEDIUM
    else:
        membership.risk_level = RiskLevel.LOW
    
    # Build note
    today = get_vietnam_now().replace(tzinfo=None)
    hui_name = hui_group.name if hui_group else "N/A"
    cycle_num = schedule.cycle_number if schedule else "N/A"
    
    new_note = f"[{today.strftime('%d/%m/%Y')}] Trễ {days_late} ngày - Kỳ {cycle_num} dây {hui_name} - {payment.amount:,.0f}đ"
    if note:
        new_note += f" | Ghi chú: {note}"
    
    existing_notes = membership.notes or ""
    if existing_notes:
        membership.notes = f"{new_note}\n{existing_notes}"
    else:
        membership.notes = new_note
    
    logger.info(f"Updated membership {membership.id}: late_count={membership.total_late_count}, credit_score={membership.credit_score}")


def sync_batch_after_payment_verify(
    db: Session,
    payment: Payment,
    current_user_id: str = None
) -> bool:
    """
    Đồng bộ batch sau khi verify payment:
    - Mark batch item as verified
    - Update batch total amount
    - Check if all items verified -> update batch status
    
    Returns: True if batch was updated
    """
    batch_item = db.query(BatchPayment).filter(
        BatchPayment.payment_id == payment.id
    ).first()
    
    if not batch_item:
        return False
    
    batch = db.query(PaymentBatch).filter(PaymentBatch.id == batch_item.batch_id).first()
    if not batch:
        return False
    
    # Mark this item as verified
    batch_item.is_verified = True
    batch_item.verified_at = get_vietnam_now()
    
    # Update batch total
    batch.total_amount = (batch.total_amount or 0) - (batch_item.amount or 0)
    
    # Check if all items verified or batch should be updated
    remaining_items = db.query(BatchPayment).filter(
        BatchPayment.batch_id == batch.id,
        BatchPayment.is_verified.is_(False)
    ).count()
    
    if remaining_items == 0:
        # All items verified
        batch.status = BatchStatus.PAID
        logger.info(f"Batch {batch.batch_code} fully paid via individual payments")
    elif batch.status == BatchStatus.PENDING and batch.total_amount <= 0:
        # No more amount to pay
        batch.status = BatchStatus.PAID
    
    db.commit()
    logger.info(f"Updated batch {batch.batch_code}: remaining items={remaining_items}, new total={batch.total_amount}")
    
    return True


def calculate_days_late(payment: Payment) -> tuple[bool, int]:
    """
    Tính số ngày trễ hạn của payment
    Returns: (was_overdue, days_late)
    """
    was_overdue = payment.payment_status == PaymentStatus.OVERDUE
    days_late = 0
    
    today = get_vietnam_now().replace(tzinfo=None)
    if payment.due_date:
        due_date = payment.due_date
        if due_date.tzinfo:
            due_date = due_date.replace(tzinfo=None)
        days_late = (today - due_date).days
        if days_late > 0:
            was_overdue = True
    
    return was_overdue, days_late


def create_audit_log(
    db: Session,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    data: dict
) -> None:
    """Tạo audit log entry"""
    audit_log = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        new_value=safe_json_dumps(data)
    )
    db.add(audit_log)
    db.commit()
