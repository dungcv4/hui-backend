"""
Memberships Router - Membership management endpoints
Bao gồm: create membership
"""
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, MembershipCreate,
    AuditLog, RiskLevel, require_role, logger, safe_json_dumps,
    generate_payment_code
)

router = APIRouter(prefix="/memberships", tags=["Memberships"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_membership(
    membership_data: MembershipCreate,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Thêm thành viên vào dây hụi (hỗ trợ nhiều chân)"""
    try:
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == membership_data.hui_group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")

        if current_user.role == UserRole.OWNER and hui_group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        # Query from Member table instead of User
        member = db.query(Member).filter(Member.id == membership_data.member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")

        # Check if member belongs to current owner
        if member.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Bạn chỉ có thể thêm thành viên do mình quản lý")
        
        existing = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == membership_data.hui_group_id,
            HuiMembership.member_id == membership_data.member_id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="Thành viên đã tham gia dây hụi này")
        
        slot_count = membership_data.slot_count if membership_data.slot_count else 1
        if slot_count < 1:
            slot_count = 1
        
        payment_code = generate_payment_code(hui_group.id, member.id)
        
        membership = HuiMembership(
            hui_group_id=membership_data.hui_group_id,
            member_id=membership_data.member_id,
            slot_count=slot_count,
            payment_code=payment_code,
            credit_score=100,
            risk_level=RiskLevel.LOW,
            has_received=False,
            received_count=0,
            received_cycles="[]",
            received_cycle=None,
            rebate_percentage=membership_data.rebate_percentage,
            guarantor_name=membership_data.guarantor_name,
            guarantor_phone=membership_data.guarantor_phone,
            notes=membership_data.notes,
            tags=membership_data.tags,
        )
        db.add(membership)
        db.commit()
        db.refresh(membership)
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="create_membership",
            entity_type="membership",
            entity_id=membership.id,
            new_value=safe_json_dumps({
                "hui_group_id": membership_data.hui_group_id, 
                "member_id": membership_data.member_id,
                "slot_count": slot_count
            })
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Membership created: {member.name} joined {hui_group.name} with {slot_count} slot(s)")
        
        return {
            "id": membership.id,
            "hui_group_id": membership.hui_group_id,
            "member_id": membership.member_id,
            "slot_count": membership.slot_count,
            "payment_code": membership.payment_code,
            "credit_score": membership.credit_score,
            "risk_level": membership.risk_level.value,
            "joined_at": membership.joined_at.isoformat()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating membership: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
