"""
Slot Transfer Router - Chuyển nhượng chân hụi
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel as PydanticBaseModel
from typing import Optional
from datetime import datetime

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, Payment, PaymentStatus,
    RiskLevel, AuditLog, require_role, safe_json_dumps, logger
)

router = APIRouter(tags=["Slot Transfer"])


def generate_payment_code(group_id: str, member_id: str) -> str:
    """Generate unique payment code"""
    import hashlib
    hash_input = f"{group_id}{member_id}"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()[:6].upper()
    return f"HUI{hash_val}"


class SlotTransferRequest(PydanticBaseModel):
    from_member_id: str
    to_member_id: str
    slots_to_transfer: int = 1
    note: Optional[str] = None


@router.post("/hui-groups/{group_id}/transfer-slot")
async def transfer_slot(
    group_id: str,
    transfer_data: SlotTransferRequest,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Chuyển nhượng chân hụi từ member A sang member B"""
    try:
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        from_membership = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == group_id,
            HuiMembership.member_id == transfer_data.from_member_id,
            HuiMembership.is_active == True
        ).first()
        
        if not from_membership:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên chuyển nhượng")
        
        if from_membership.slot_count < transfer_data.slots_to_transfer:
            raise HTTPException(
                status_code=400,
                detail=f"Thành viên chỉ có {from_membership.slot_count} chân, không đủ để chuyển {transfer_data.slots_to_transfer} chân"
            )
        
        to_membership = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == group_id,
            HuiMembership.member_id == transfer_data.to_member_id,
            HuiMembership.is_active == True
        ).first()
        
        to_member = db.query(Member).filter(Member.id == transfer_data.to_member_id).first()
        if not to_member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên nhận")
        
        from_member = db.query(Member).filter(Member.id == transfer_data.from_member_id).first()
        
        if not to_membership:
            to_membership = HuiMembership(
                hui_group_id=group_id,
                member_id=transfer_data.to_member_id,
                slot_count=transfer_data.slots_to_transfer,
                payment_code=generate_payment_code(group_id, transfer_data.to_member_id),
                credit_score=100,
                risk_level=RiskLevel.LOW,
                total_late_count=0,
                total_late_amount=0,
                is_active=True,
                notes=f"Nhận chuyển nhượng {transfer_data.slots_to_transfer} chân từ {from_member.name}"
            )
            db.add(to_membership)
            db.flush()
        else:
            to_membership.slot_count += transfer_data.slots_to_transfer
            existing_notes = to_membership.notes or ""
            new_note = f"[{datetime.now().strftime('%d/%m/%Y')}] Nhận chuyển nhượng {transfer_data.slots_to_transfer} chân từ {from_member.name}"
            to_membership.notes = f"{new_note}\n{existing_notes}" if existing_notes else new_note
        
        from_membership.slot_count -= transfer_data.slots_to_transfer
        existing_notes = from_membership.notes or ""
        new_note = f"[{datetime.now().strftime('%d/%m/%Y')}] Chuyển nhượng {transfer_data.slots_to_transfer} chân cho {to_member.name}"
        if transfer_data.note:
            new_note += f" | Lý do: {transfer_data.note}"
        from_membership.notes = f"{new_note}\n{existing_notes}" if existing_notes else new_note
        
        if from_membership.slot_count <= 0:
            from_membership.is_active = False
        
        pending_payments = db.query(Payment).filter(
            Payment.membership_id == from_membership.id,
            Payment.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE])
        ).all()
        
        transferred_payments = 0
        for payment in pending_payments[:transfer_data.slots_to_transfer]:
            payment.membership_id = to_membership.id
            transferred_payments += 1
        
        db.commit()
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="transfer_slot",
            entity_type="membership",
            entity_id=from_membership.id,
            new_value=safe_json_dumps({
                "from_member": from_member.name,
                "to_member": to_member.name,
                "slots_transferred": transfer_data.slots_to_transfer,
                "payments_transferred": transferred_payments,
                "note": transfer_data.note
            })
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Slot transfer: {from_member.name} -> {to_member.name}, {transfer_data.slots_to_transfer} slot(s)")
        
        return {
            "success": True,
            "message": f"Đã chuyển nhượng {transfer_data.slots_to_transfer} chân từ {from_member.name} sang {to_member.name}",
            "from_member": {
                "id": from_member.id,
                "name": from_member.name,
                "remaining_slots": from_membership.slot_count,
                "is_active": from_membership.is_active
            },
            "to_member": {
                "id": to_member.id,
                "name": to_member.name,
                "total_slots": to_membership.slot_count
            },
            "payments_transferred": transferred_payments
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error transferring slot: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
