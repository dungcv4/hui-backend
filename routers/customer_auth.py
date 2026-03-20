"""
Customer Auth Router - Authentication endpoints for hui members (customers)
Tách biệt hoàn toàn với auth của chủ hụi/admin
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from passlib.context import CryptContext
from datetime import timedelta
from typing import Optional

from database import get_db
from config import settings
from models import Member
from auth import create_access_token
from routers.customer_deps import get_current_member

import logging

logger = logging.getLogger(__name__)
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

router = APIRouter(prefix="/customer/auth", tags=["Customer Auth"])


# ----- Schemas -----

class CustomerLoginRequest(BaseModel):
    phone: str = Field(..., description="Số điện thoại thành viên")
    password: str = Field(..., min_length=6, description="Mật khẩu")


class CustomerMemberResponse(BaseModel):
    id: str
    name: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    cccd: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    class Config:
        from_attributes = True


class CustomerTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    member: CustomerMemberResponse


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=6)
    new_password: str = Field(..., min_length=6)


class SetPasswordRequest(BaseModel):
    """Used by owner to set initial password for a member"""
    member_id: str
    password: str = Field(..., min_length=6)


# ----- Endpoints -----

@router.post("/login", response_model=CustomerTokenResponse)
async def customer_login(
    login_data: CustomerLoginRequest,
    db: Session = Depends(get_db)
):
    """Đăng nhập cho thành viên hụi"""
    try:
        # Find member by phone (may belong to any owner)
        member = db.query(Member).filter(
            Member.phone == login_data.phone,
            Member.is_active == True
        ).first()

        if not member:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Số điện thoại hoặc mật khẩu không đúng"
            )

        if not member.password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Tài khoản chưa được kích hoạt. Vui lòng liên hệ chủ hụi."
            )

        if not pwd_context.verify(login_data.password, member.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Số điện thoại hoặc mật khẩu không đúng"
            )

        # Create token with type='member' to distinguish from owner/admin
        access_token = create_access_token(
            data={"sub": member.id, "type": "member"},
            expires_delta=timedelta(hours=settings.jwt_expiration_hours)
        )

        return CustomerTokenResponse(
            access_token=access_token,
            member=CustomerMemberResponse.model_validate(member)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Customer login error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/me", response_model=CustomerMemberResponse)
async def get_current_member_info(
    current_member: Member = Depends(get_current_member)
):
    """Thông tin thành viên hiện tại"""
    return CustomerMemberResponse.model_validate(current_member)


@router.put("/change-password")
async def customer_change_password(
    data: ChangePasswordRequest,
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Đổi mật khẩu cho thành viên"""
    try:
        member = db.query(Member).filter(Member.id == current_member.id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")

        # Verify current password
        if not member.password_hash or not pwd_context.verify(data.current_password, member.password_hash):
            raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng")

        member.password_hash = pwd_context.hash(data.new_password)
        db.commit()

        return {"success": True, "message": "Đổi mật khẩu thành công"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing customer password: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
