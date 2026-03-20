from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from models import UserRole, HuiCycle, HuiMethod, PaymentStatus, PaymentMethod, RiskLevel, DebtStatus

# Auth Schemas
class LoginRequest(BaseModel):
    phone: str = Field(..., description="Số điện thoại")
    password: str = Field(..., min_length=6, description="Mật khẩu")

class VerifyOTPRequest(BaseModel):
    phone: str
    otp_code: str = Field(..., min_length=6, max_length=6)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"

# User Schemas (for system users: owner, staff, admin)
class UserBase(BaseModel):
    phone: str
    name: str
    email: Optional[str] = None
    role: str = "owner"  # owner, staff, system_admin

class UserCreate(UserBase):
    password: Optional[str] = None

class UserResponse(UserBase):
    id: str
    is_active: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Member Schemas (for hui members - separate from system users)
class MemberBase(BaseModel):
    phone: str
    name: str
    email: Optional[str] = None
    cccd: Optional[str] = None
    address: Optional[str] = None

class MemberCreate(MemberBase):
    pass

class MemberUpdate(BaseModel):
    """All fields optional for partial update support"""
    phone: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    cccd: Optional[str] = None
    address: Optional[str] = None

class MemberResponse(MemberBase):
    id: str
    owner_id: str
    telegram_chat_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Customer Portal Schemas
class CustomerProfileUpdate(BaseModel):
    """Customer self-service profile update (phone và CCCD không cho sửa)"""
    name: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


# Hui Group Schemas
class HuiGroupBase(BaseModel):
    name: str = Field(..., description="Tên dây hụi")
    amount_per_cycle: float = Field(..., gt=0, description="Số tiền mỗi kỳ")
    total_members: int = Field(..., gt=0, description="Tổng số thành viên")
    cycle_type: HuiCycle = Field(..., description="Loại chu kỳ")
    cycle_interval: int = Field(1, gt=0, description="Khoảng cách giữa các kỳ (mỗi X ngày/tuần/tháng)")
    total_cycles: int = Field(..., gt=0, description="Tổng số kỳ")
    fee_type: str = Field("percentage", description="Loại phí")
    fee_value: float = Field(0, ge=0, description="Giá trị phí")
    interest_per_cycle: float = Field(0, ge=0, description="Lãi hụi sống - tiền thành viên đã hốt đóng thêm mỗi kỳ")
    hui_method: HuiMethod = HuiMethod.ASSIGNED
    bank_account_number: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_name: Optional[str] = None
    start_date: datetime
    # Late fee config
    late_fee_type: str = Field("none", description="Loại phạt: none, percentage, fixed, daily_percentage, daily_fixed")
    late_fee_value: float = Field(0, ge=0, description="Giá trị phạt")
    late_fee_grace_days: int = Field(0, ge=0, description="Số ngày ân hạn")
    late_fee_max_amount: float = Field(0, ge=0, description="Phạt tối đa (0=không giới hạn)")

class HuiGroupCreate(HuiGroupBase):
    pass

class HuiGroupResponse(HuiGroupBase):
    id: str
    owner_id: str
    current_cycle: int
    is_active: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class HuiGroupDetail(HuiGroupResponse):
    owner: UserResponse
    total_members_joined: int = 0
    total_collected: float = 0
    total_distributed: float = 0
    
    model_config = ConfigDict(from_attributes=True)

# Membership Schemas
class MembershipBase(BaseModel):
    cccd: Optional[str] = None
    address: Optional[str] = None
    rebate_percentage: float = Field(0, ge=0, le=100)
    guarantor_name: Optional[str] = None
    guarantor_phone: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None

class MembershipCreate(MembershipBase):
    hui_group_id: str
    member_id: str
    slot_count: int = Field(1, ge=1, description="Số chân tham gia")

class MembershipResponse(MembershipBase):
    id: str
    hui_group_id: str
    member_id: str
    slot_count: int = 1
    payment_code: str
    credit_score: int
    risk_level: RiskLevel
    total_late_count: int
    total_late_amount: float
    has_received: bool
    received_count: int = 0
    received_cycles: Optional[str] = None
    received_cycle: Optional[int]
    joined_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class MembershipDetail(MembershipResponse):
    member: UserResponse
    hui_group: HuiGroupResponse
    total_paid: float = 0
    total_pending: float = 0
    payment_history: List["PaymentResponse"] = []
    
    model_config = ConfigDict(from_attributes=True)

# Payment Schemas
class PaymentBase(BaseModel):
    amount: float = Field(..., gt=0)
    payment_method: PaymentMethod
    notes: Optional[str] = None

class PaymentCreate(PaymentBase):
    hui_group_id: str
    membership_id: str
    due_date: Optional[datetime] = None

class PaymentResponse(PaymentBase):
    id: str
    hui_group_id: str
    membership_id: str
    payment_status: PaymentStatus
    reference_code: str
    bank_transaction_ref: Optional[str]
    qr_code_data: Optional[str]
    due_date: Optional[datetime]
    paid_at: Optional[datetime]
    verified_at: Optional[datetime]
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class QRCodeRequest(BaseModel):
    hui_group_id: str
    membership_id: str
    amount: float = Field(..., gt=0)
    cycle_number: Optional[int] = None

class QRCodeResponse(BaseModel):
    transaction_id: str
    qr_code: str
    reference_code: str
    amount: float
    bank_account: str
    bank_name: str
    transfer_content: str
    expires_in_minutes: int = 15

# Dashboard Schemas
class DashboardSummary(BaseModel):
    """Dashboard hôm nay"""
    # Thu hôm nay
    total_due_today: float = 0
    total_collected_today: float = 0
    total_pending_today: float = 0
    members_paid_today: int = 0
    members_pending_today: int = 0
    
    # Quá hạn
    total_overdue: float = 0
    members_overdue: int = 0
    
    # Chi hôm nay
    cycles_distribution_today: int = 0
    total_distribution_today: float = 0
    
    # Tổng quan
    total_active_groups: int = 0
    total_active_members: int = 0
    total_revenue_month: float = 0
    
    # Lợi nhuận chủ hụi (chỉ tính khi schedule đã completed)
    profit_today: float = 0              # Lãi hôm nay (đã thu - từ schedules completed)
    profit_today_expected: float = 0     # Lãi dự kiến hôm nay (nếu tất cả đóng đủ)
    profit_today_progress: int = 0       # Số kỳ đã hoàn thành / tổng kỳ hôm nay
    profit_today_total: int = 0          # Tổng số kỳ hôm nay
    profit_this_week: float = 0          # Lãi tuần này (đã thu)
    profit_week_expected: float = 0      # Lãi dự kiến tuần này
    profit_this_month: float = 0         # Lãi tháng này (đã thu)
    profit_month_expected: float = 0     # Lãi dự kiến tháng này
    profit_total: float = 0              # Tổng lãi đã thu từ đầu
    profit_projected: float = 0          # Dự kiến lãi khi kết thúc tất cả dây hụi
    
    # Thống kê dây hụi (MỚI)
    groups_ending_soon: int = 0      # Dây hụi sắp kết thúc (trong 30 ngày)
    average_progress: float = 0      # Tiến độ trung bình (%)
    total_pot_value: float = 0       # Tổng giá trị hụi đang quản lý

class CashflowSummary(BaseModel):
    """Báo cáo dòng tiền"""
    period: str  # day, week, month, year
    total_income: float = 0
    total_expense: float = 0
    net_cashflow: float = 0
    owner_fee_earned: float = 0
    pending_collections: float = 0

class RiskReport(BaseModel):
    """Báo cáo rủi ro"""
    total_at_risk: int = 0
    critical_members: List[MembershipResponse] = []
    high_risk_members: List[MembershipResponse] = []
    total_overdue_amount: float = 0
    avg_days_overdue: float = 0

# Webhook Schemas
class WebhookPayload(BaseModel):
    event_type: str
    amount: float
    content: str
    transaction_date: str
    reference_code: Optional[str] = None
    account_number: Optional[str] = None

class WebhookResponse(BaseModel):
    status: str
    event_id: str
    timestamp: datetime


# Debt Management Schemas
class DebtRecordResponse(BaseModel):
    id: str
    owner_id: str
    member_id: str
    membership_id: str
    payment_id: str
    hui_group_id: str
    original_amount: float
    late_fee: float
    total_amount: float
    paid_amount: float
    remaining_amount: float
    due_date: datetime
    days_overdue: int
    cycle_number: Optional[int] = None
    status: DebtStatus
    notes: Optional[str] = None
    last_reminder_at: Optional[datetime] = None
    reminder_count: int = 0
    resolved_at: Optional[datetime] = None
    created_at: datetime
    # Populated from joins
    member_name: Optional[str] = None
    member_phone: Optional[str] = None
    hui_group_name: Optional[str] = None

class DebtPayRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Số tiền thanh toán")
    notes: Optional[str] = None

class DebtWaiveRequest(BaseModel):
    notes: Optional[str] = None

class DebtSummaryResponse(BaseModel):
    total_outstanding: float = 0
    total_late_fees: float = 0
    total_debt_count: int = 0
    members_with_debt: int = 0
    debts_by_group: list = []
    recent_debts: list = []