"""
System Admin Router - Super Admin management endpoints
Manage Owners (create, active/inactive)
"""
from fastapi import APIRouter, HTTPException, Depends, status, Body
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from pydantic import BaseModel

from routers.dependencies import (
    get_db, User, UserRole, UserResponse, UserCreate,
    get_current_user, pwd_context, logger, safe_json_dumps
)

router = APIRouter(prefix="/admin", tags=["System Admin"])


# Dependency to check for system admin
def require_system_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != UserRole.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Chỉ System Admin mới có quyền truy cập"
        )
    return current_user


class OwnerCreate(BaseModel):
    phone: str
    name: str
    password: str
    email: Optional[str] = None


@router.get("/owners", response_model=List[UserResponse])
async def list_owners(
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db)
):
    """List all Hui Owners"""
    try:
        owners = db.query(User).filter(
            User.role == UserRole.OWNER
        ).order_by(desc(User.created_at)).all()
        return [UserResponse.model_validate(u) for u in owners]
    except Exception as e:
        logger.error(f"Error listing owners: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/owners", response_model=UserResponse)
async def create_owner(
    owner_data: OwnerCreate,
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db)
):
    """Create a new Hui Owner account"""
    try:
        # Check existing phone
        existing = db.query(User).filter(User.phone == owner_data.phone).first()
        if existing:
            raise HTTPException(status_code=400, detail="Số điện thoại đã tồn tại")
        
        # Create user
        new_owner = User(
            phone=owner_data.phone,
            name=owner_data.name,
            email=owner_data.email,
            password_hash=pwd_context.hash(owner_data.password),
            role=UserRole.OWNER,
            is_active=True
        )
        
        db.add(new_owner)
        db.commit()
        db.refresh(new_owner)
        
        logger.info(f"New Owner created: {new_owner.phone} by Admin {current_user.phone}")
        return UserResponse.model_validate(new_owner)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating owner: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/owners/{user_id}/status")
async def toggle_owner_status(
    user_id: str,
    active: bool = Body(..., embed=True),
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db)
):
    """Activate or Deactivate an Owner account"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy user")
            
        if user.role != UserRole.OWNER:
            raise HTTPException(status_code=400, detail="User này không phải là Owner")
            
        user.is_active = active
        db.commit()
        db.refresh(user)
        
        status_str = "Active" if active else "Inactive"
        logger.info(f"Owner {user.phone} status changed to {status_str} by Admin")
        
        return UserResponse.model_validate(user)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/owners/{user_id}/reset-password")
async def reset_owner_password(
    user_id: str,
    new_password: str = Body(..., embed=True),
    current_user: User = Depends(require_system_admin),
    db: Session = Depends(get_db)
):
    """Force reset password for an Owner"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy user")
            
        user.password_hash = pwd_context.hash(new_password)
        db.commit()
        
        logger.info(f"Password reset for Owner {user.phone} by Admin")
        return {"success": True, "message": "Đã đổi mật khẩu thành công"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting password: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
