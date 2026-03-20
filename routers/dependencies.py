"""
Dependencies module - Shared imports, helpers and dependencies cho tất cả routers
Tách từ server.py để tránh circular imports
"""

from fastapi import FastAPI, HTTPException, Depends, status, Request, Body, APIRouter
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from typing import List, Optional
from pydantic import BaseModel as PydanticBaseModel
import logging
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from passlib.context import CryptContext

# Local imports
from database import get_db, engine
from models import (
    User, UserRole, Member, HuiGroup, HuiMembership, Payment, PaymentStatus, 
    PaymentMethod, HuiSchedule, AuditLog, WebhookEvent, PaymentBatch, 
    BatchPayment, BatchStatus, GlobalBankConfig, BillSendHistory, 
    BillSendStatus, DailyBillSummary, RiskLevel, HuiCycle, HuiMethod
)
from schemas import (
    LoginRequest, TokenResponse, UserResponse, UserCreate,
    MemberResponse, MemberCreate, MemberUpdate,
    HuiGroupCreate, HuiGroupResponse, HuiGroupDetail,
    MembershipCreate, MembershipResponse, MembershipDetail,
    PaymentCreate, PaymentResponse, QRCodeRequest, QRCodeResponse,
    DashboardSummary, CashflowSummary, RiskReport,
    WebhookPayload, WebhookResponse
)
from utils import generate_payment_code, generate_reference_code, safe_json_dumps, calculate_owner_fee
from auth import (
    authenticate_user, create_access_token, get_current_user,
    require_role, verify_token
)
from config import settings
import payment_service

# Setup logging
logger = logging.getLogger(__name__)

# Vietnam timezone helper
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

def get_vietnam_now():
    """Get current time in Vietnam timezone"""
    return datetime.now(VIETNAM_TZ)

def get_vietnam_today_range():
    """Get start and end of today in Vietnam timezone (returns naive datetimes for DB comparison)"""
    now = get_vietnam_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    # Return naive datetimes for MySQL comparison
    return today_start.replace(tzinfo=None), today_end.replace(tzinfo=None)

def get_vietnam_week_start():
    """Get Monday of current week in Vietnam timezone (naive datetime)"""
    now = get_vietnam_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    return week_start.replace(tzinfo=None)

def get_vietnam_month_start():
    """Get first day of current month in Vietnam timezone (naive datetime)"""
    now = get_vietnam_now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start.replace(tzinfo=None)

# Security
security = HTTPBearer()

# Password context for hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
