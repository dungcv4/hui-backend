"""
Users Router - Users/Members management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from typing import List

from routers.dependencies import (
    get_db, User, UserRole, UserResponse, UserCreate, HuiGroup, HuiMembership, 
    Payment, PaymentStatus, AuditLog, require_role, get_current_user,
    logger, safe_json_dumps, pwd_context
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=List[UserResponse])
async def list_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100
):
    """Danh sách tất cả system users (staff/admin)"""
    try:
        # List all active system users
        users = db.query(User).filter(User.is_active == True).offset(skip).limit(limit).all()
        return [UserResponse.model_validate(u) for u in users]
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Tạo system user mới (staff/admin)"""
    try:
        # Check duplicate phone globally (users table has unique phone constraint)
        existing = db.query(User).filter(User.phone == user_data.phone).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="Số điện thoại đã tồn tại")
        
        user = User(
            **user_data.model_dump(),
            password_hash=pwd_context.hash("123456")
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="create_user",
            entity_type="user",
            entity_id=user.id,
            new_value=safe_json_dumps(user_data.model_dump())
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"User created: {user.phone} by {current_user.phone}")
        return UserResponse.model_validate(user)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating user: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Chi tiết user/member"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
            
        # System users can be viewed by any authenticated owner/staff
        return UserResponse.model_validate(user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_data: UserCreate,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Cập nhật thông tin system user"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        
        # Check duplicate phone globally
        if user_data.phone != user.phone:
            existing = db.query(User).filter(
                User.phone == user_data.phone, 
                User.id != user_id
            ).first()
            if existing:
                raise HTTPException(status_code=400, detail="Số điện thoại đã tồn tại")
        
        user.phone = user_data.phone
        user.name = user_data.name
        user.email = user_data.email
        
        db.commit()
        db.refresh(user)
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="update_user",
            entity_type="user",
            entity_id=user.id,
            new_value=safe_json_dumps({"name": user.name, "phone": user.phone})
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"User updated: {user.phone}")
        return UserResponse.model_validate(user)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user: User = Depends(require_role([UserRole.OWNER])),
    db: Session = Depends(get_db)
):
    """Xóa user/member (soft delete)"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        
        if user.id == current_user.id:
            raise HTTPException(status_code=400, detail="Không thể xóa chính mình")
        
        active_memberships = db.query(HuiMembership).filter(
            HuiMembership.member_id == user_id,
            HuiMembership.is_active == True
        ).count()
        
        if active_memberships > 0:
            raise HTTPException(status_code=400, detail=f"Không thể xóa: Thành viên đang tham gia {active_memberships} dây hụi")
        
        user.is_active = False
        db.commit()
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="delete_user",
            entity_type="user",
            entity_id=user.id,
            new_value=safe_json_dumps({"name": user.name, "phone": user.phone, "deleted": True})
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"User soft deleted: {user.phone}")
        return {"success": True, "message": "Xóa thành viên thành công"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{user_id}/detail")
async def get_user_detail(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Chi tiết đầy đủ của system user"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        
        # System users can be viewed by any authenticated user
        
        memberships = db.query(HuiMembership).filter(
            HuiMembership.member_id == user_id
        ).all()
        
        membership_details = []
        total_paid = 0
        total_pending = 0
        
        for membership in memberships:
            hui_group = db.query(HuiGroup).filter(HuiGroup.id == membership.hui_group_id).first()
            
            payments = db.query(Payment).filter(
                Payment.membership_id == membership.id
            ).order_by(Payment.due_date.desc()).all()
            
            paid = sum(p.amount for p in payments if p.payment_status == PaymentStatus.VERIFIED)
            pending = sum(p.amount for p in payments if p.payment_status in [PaymentStatus.PENDING, PaymentStatus.OVERDUE])
            
            total_paid += paid
            total_pending += pending
            
            membership_details.append({
                "id": membership.id,
                "hui_group_id": membership.hui_group_id,
                "hui_group_name": hui_group.name if hui_group else "N/A",
                "payment_code": membership.payment_code,
                "slot_count": membership.slot_count or 1,
                "credit_score": membership.credit_score,
                "risk_level": membership.risk_level.value,
                "total_late_count": membership.total_late_count or 0,
                "total_late_amount": membership.total_late_amount or 0,
                "has_received": membership.has_received,
                "received_cycle": membership.received_cycle,
                "joined_at": membership.joined_at.isoformat(),
                "is_active": membership.is_active,
                "notes": membership.notes,
                "total_paid": paid,
                "total_pending": pending,
                "payments": [
                    {
                        "id": p.id,
                        "amount": p.amount,
                        "status": p.payment_status.value,
                        "due_date": p.due_date.isoformat() if p.due_date else None,
                        "paid_at": p.paid_at.isoformat() if p.paid_at else None
                    } for p in payments[:10]
                ]
            })
        
        overall_late_count = sum(m.total_late_count or 0 for m in memberships)
        overall_late_amount = sum(m.total_late_amount or 0 for m in memberships)
        avg_credit_score = sum(m.credit_score or 100 for m in memberships) / len(memberships) if memberships else 100
        
        all_notes = []
        for m in memberships:
            if m.notes:
                hui_group = db.query(HuiGroup).filter(HuiGroup.id == m.hui_group_id).first()
                all_notes.append({
                    "hui_group_name": hui_group.name if hui_group else "N/A",
                    "notes": m.notes
                })
        
        if avg_credit_score < 40:
            overall_risk = "critical"
        elif avg_credit_score < 60:
            overall_risk = "high"
        elif avg_credit_score < 80:
            overall_risk = "medium"
        else:
            overall_risk = "low"
        
        return {
            "id": user.id,
            "phone": user.phone,
            "name": user.name,
            "email": user.email,
            "role": user.role.value,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
            "total_memberships": len(memberships),
            "active_memberships": len([m for m in memberships if m.is_active]),
            "total_paid": total_paid,
            "total_pending": total_pending,
            "avg_credit_score": round(avg_credit_score),
            "overall_risk_level": overall_risk,
            "total_late_count": overall_late_count,
            "total_late_amount": overall_late_amount,
            "payment_history_notes": all_notes,
            "memberships": membership_details
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user detail: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
