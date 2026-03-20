"""
Customer Dependencies - Auth dependency for customer (member) routes
Tách biệt hoàn toàn với User auth của chủ hụi/admin
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError, ExpiredSignatureError, jwt

from database import get_db
from config import settings
from models import Member

security = HTTPBearer()


def get_current_member(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Member:
    """Decode JWT token and return the authenticated Member.
    Token must contain type='member' to distinguish from owner/admin tokens."""

    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token đã hết hạn, vui lòng đăng nhập lại",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Không thể xác thực. Vui lòng đăng nhập lại.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Must be a member token
    if payload.get("type") != "member":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token không hợp lệ cho Customer Portal",
            headers={"WWW-Authenticate": "Bearer"},
        )

    member_id: str = payload.get("sub")
    if member_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token không hợp lệ",
            headers={"WWW-Authenticate": "Bearer"},
        )

    member = db.query(Member).filter(Member.id == member_id, Member.is_active == True).first()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản không tồn tại hoặc đã bị vô hiệu hóa",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return member
