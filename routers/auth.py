"""
Auth Router - Authentication endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request
from sqlalchemy.orm import Session

from routers.dependencies import (
    get_db, User, UserResponse, LoginRequest, TokenResponse,
    authenticate_user, create_access_token, get_current_user,
    get_vietnam_now, logger, pwd_context
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(
    login_data: LoginRequest,
    db: Session = Depends(get_db)
):
    """Đăng nhập"""
    try:
        user = authenticate_user(db, login_data.phone, login_data.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Số điện thoại hoặc mật khẩu không đúng"
            )
        
        access_token = create_access_token(data={"sub": user.id})
        
        return TokenResponse(
            access_token=access_token,
            user=UserResponse.model_validate(user)
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """Thông tin user hiện tại"""
    return UserResponse.model_validate(current_user)


@router.put("/profile")
async def update_profile(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cập nhật thông tin profile chủ hụi (tên, số điện thoại)"""
    try:
        body = await request.json()
        name = body.get("name")
        phone = body.get("phone")
        
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy user")
        
        if name is not None and name.strip():
            user.name = name.strip()
        
        if phone is not None and phone.strip():
            # Check if phone already exists for another system user
            existing = db.query(User).filter(
                User.phone == phone,
                User.id != current_user.id
            ).first()
                
            if existing:
                raise HTTPException(status_code=400, detail="Số điện thoại đã được sử dụng")
            user.phone = phone.strip()
        
        user.updated_at = get_vietnam_now()
        db.commit()
        db.refresh(user)
        
        return {
            "success": True,
            "message": "Cập nhật thành công",
            "user": UserResponse.model_validate(user)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/change-password")
async def change_password(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Đổi mật khẩu"""
    try:
        body = await request.json()
        current_password = body.get("current_password", "")
        new_password = body.get("new_password", "")
        
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy user")
        
        # Verify current password
        if not user.password_hash or not pwd_context.verify(current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng")
        
        # Validate new password
        if len(new_password) < 6:
            raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 6 ký tự")
        
        # Update password
        user.password_hash = pwd_context.hash(new_password)
        user.updated_at = get_vietnam_now()
        db.commit()
        
        return {"success": True, "message": "Đổi mật khẩu thành công"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing password: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
