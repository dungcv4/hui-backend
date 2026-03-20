from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, Enum as SQLEnum, ForeignKey, Index
from sqlalchemy.orm import relationship, backref
from datetime import datetime, timezone
import enum
import uuid
from database import Base

# Enums
class UserRole(str, enum.Enum):
    SYSTEM_ADMIN = "system_admin"  # Super Admin
    OWNER = "owner"  # Chủ hụi
    STAFF = "staff"  # Nhân viên
    # MEMBER removed - members now in separate table

class HuiCycle(str, enum.Enum):
    DAILY = "daily"  # Ngày
    WEEKLY = "weekly"  # Tuần
    MONTHLY = "monthly"  # Tháng

class HuiMethod(str, enum.Enum):
    ASSIGNED = "assigned"  # Chỉ định
    LOTTERY = "lottery"  # Bốc thăm
    AUCTION = "auction"  # Đấu giá

class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    OVERDUE = "overdue"

class PaymentMethod(str, enum.Enum):
    BANK_TRANSFER = "bank_transfer"
    QR_CODE = "qr_code"
    CASH = "cash"

class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class DebtStatus(str, enum.Enum):
    OUTSTANDING = "outstanding"  # Đang nợ
    PARTIAL = "partial"          # Đã trả 1 phần
    PAID = "paid"                # Đã trả đủ
    WAIVED = "waived"            # Chủ hụi miễn nợ

# Models
class TelegramSettings(Base):
    __tablename__ = "telegram_settings"
    
    setting_key = Column(String(100), primary_key=True)
    setting_value = Column(Text)
    description = Column(String(255))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class User(Base):
    """User - Người dùng hệ thống (owner, staff, admin). Member tách riêng."""
    __tablename__ = "users"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone = Column(String(20), nullable=False, unique=True)  # Phone unique cho user hệ thống
    name = Column(String(255), nullable=False)
    email = Column(String(255))
    password_hash = Column(String(255))
    role = Column(String(50), default="owner")  # owner, staff, system_admin
    telegram_chat_id = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    hui_groups_owned = relationship("HuiGroup", back_populates="owner", foreign_keys="HuiGroup.owner_id")
    members_managed = relationship("Member", back_populates="owner")  # Members thuộc owner này
    otp_records = relationship("OTPRecord", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")


class Member(Base):
    """Member - Thành viên hụi (riêng biệt với User hệ thống)"""
    __tablename__ = "members"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)  # Chủ hụi sở hữu
    
    # Thông tin cơ bản
    phone = Column(String(20), nullable=False)
    name = Column(String(255), nullable=False)
    email = Column(String(255))
    telegram_chat_id = Column(String(100))
    password_hash = Column(String(255))  # Customer portal login
    
    # Thông tin bổ sung
    cccd = Column(String(20))  # CCCD/CMND
    address = Column(Text)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    owner = relationship("User", back_populates="members_managed")
    memberships = relationship("HuiMembership", back_populates="member")
    
    # Unique: Mỗi owner không có 2 member cùng SĐT
    __table_args__ = (
        Index('idx_member_phone_owner', 'phone', 'owner_id', unique=True),
    )

class OTPRecord(Base):
    __tablename__ = "otp_records"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    phone = Column(String(20), nullable=False)
    otp_code = Column(String(6), nullable=False)
    is_verified = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    user = relationship("User", back_populates="otp_records")

class HuiGroup(Base):
    __tablename__ = "hui_groups"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    
    # Cấu hình dây hụi
    amount_per_cycle = Column(Float, nullable=False)  # Số tiền mỗi kỳ (VND)
    total_members = Column(Integer, nullable=False)  # Tổng số người
    cycle_type = Column(SQLEnum(HuiCycle), nullable=False)  # Ngày/Tuần/Tháng
    cycle_interval = Column(Integer, default=1)  # Khoảng cách giữa các kỳ (mỗi X ngày/tuần/tháng)
    total_cycles = Column(Integer, nullable=False)  # Tổng số kỳ
    current_cycle = Column(Integer, default=1)  # Kỳ hiện tại
    
    # Phí chủ hụi
    fee_type = Column(String(20), default="percentage")  # percentage hoặc fixed
    fee_value = Column(Float, default=0)  # % hoặc số tiền cố định
    
    # Lãi hụi sống - số tiền thành viên đã hốt phải đóng thêm mỗi kỳ
    interest_per_cycle = Column(Float, default=0)  # Tiền lãi mỗi kỳ (VND)
    
    # Phạt trễ hạn
    late_fee_type = Column(String(20), default="none")  # none, percentage, fixed, daily_percentage, daily_fixed
    late_fee_value = Column(Float, default=0)  # % hoặc VND tuỳ type
    late_fee_grace_days = Column(Integer, default=0)  # Số ngày ân hạn trước khi phạt
    late_fee_max_amount = Column(Float, default=0)  # Phạt tối đa (0 = không giới hạn)
    
    # Cách hốt
    hui_method = Column(SQLEnum(HuiMethod), default=HuiMethod.ASSIGNED)
    
    # Tài khoản nhận tiền
    bank_account_number = Column(String(50))
    bank_name = Column(String(100))
    bank_account_name = Column(String(255))
    
    # Ngày bắt đầu và kết thúc
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    
    # Telegram integration
    telegram_group_id = Column(String(100))  # Telegram group chat ID
    telegram_group_linked_at = Column(DateTime)  # When linked
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    owner = relationship("User", back_populates="hui_groups_owned", foreign_keys=[owner_id])
    memberships = relationship("HuiMembership", back_populates="hui_group")
    schedules = relationship("HuiSchedule", back_populates="hui_group")
    payments = relationship("Payment", back_populates="hui_group")

class HuiMembership(Base):
    """HuiMembership - Liên kết giữa Member và HuiGroup"""
    __tablename__ = "hui_memberships"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    hui_group_id = Column(String(36), ForeignKey("hui_groups.id"), nullable=False)
    member_id = Column(String(36), ForeignKey("members.id"), nullable=False)  # Tham chiếu bảng members
    
    # Số chân (slots) - mỗi người có thể chơi nhiều chân
    slot_count = Column(Integer, default=1)  # Số chân tham gia
    
    # Mã thanh toán
    payment_code = Column(String(50), unique=True, nullable=False)  # Mã nhận diện chuyển khoản
    
    # Credit scoring
    credit_score = Column(Integer, default=100)  # Điểm uy tín (0-100)
    total_late_count = Column(Integer, default=0)  # Số lần trễ
    total_late_amount = Column(Float, default=0)  # Tổng số tiền trễ
    risk_level = Column(SQLEnum(RiskLevel), default=RiskLevel.LOW)
    
    # Trạng thái hốt - hỗ trợ nhiều chân
    has_received = Column(Boolean, default=False)  # Đã hốt hết chưa (tất cả chân)
    received_count = Column(Integer, default=0)  # Số lần đã hốt (tối đa = slot_count)
    received_cycles = Column(Text)  # Các kỳ đã hốt (JSON array: "[1, 5, 8]")
    received_cycle = Column(Integer)  # Kỳ đã hốt (legacy - kỳ đầu tiên)
    
    # Hoàn lãi
    rebate_percentage = Column(Float, default=0)  # % hoàn lãi
    total_rebate_received = Column(Float, default=0)  # Tổng đã hoàn
    
    # Người bảo lãnh
    guarantor_name = Column(String(255))
    guarantor_phone = Column(String(20))
    
    notes = Column(Text)  # Ghi chú
    tags = Column(Text)  # Tags (JSON hoặc comma-separated)
    
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)
    
    # Relationships
    hui_group = relationship("HuiGroup", back_populates="memberships")
    member = relationship("Member", back_populates="memberships")  # Tham chiếu Member, không phải User
    payments = relationship("Payment", back_populates="membership")
    
    __table_args__ = (
        Index('idx_hui_member', 'hui_group_id', 'member_id', unique=True),
    )

class HuiSchedule(Base):
    __tablename__ = "hui_schedules"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    hui_group_id = Column(String(36), ForeignKey("hui_groups.id"), nullable=False)
    
    cycle_number = Column(Integer, nullable=False)  # Số kỳ
    due_date = Column(DateTime, nullable=False)  # Ngày đóng
    
    # Người hốt kỳ này
    receiver_membership_id = Column(String(36), ForeignKey("hui_memberships.id"))
    
    # Tài chính
    total_collection = Column(Float, default=0)  # Tổng thu
    owner_fee = Column(Float, default=0)  # Phí chủ hụi
    distribution_amount = Column(Float, default=0)  # Số tiền chi cho người hốt
    
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    hui_group = relationship("HuiGroup", back_populates="schedules")
    receiver = relationship("HuiMembership", foreign_keys=[receiver_membership_id])
    
    __table_args__ = (
        Index('idx_hui_cycle', 'hui_group_id', 'cycle_number', unique=True),
    )

class Payment(Base):
    __tablename__ = "payments"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    hui_group_id = Column(String(36), ForeignKey("hui_groups.id"), nullable=False)
    membership_id = Column(String(36), ForeignKey("hui_memberships.id"), nullable=False)
    schedule_id = Column(String(36), ForeignKey("hui_schedules.id"))
    
    # Thông tin thanh toán
    amount = Column(Float, nullable=False)
    payment_method = Column(SQLEnum(PaymentMethod), nullable=False)
    payment_status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.PENDING)
    
    # Reference code cho auto-detect
    reference_code = Column(String(100), unique=True, nullable=False, index=True)
    
    # Sepay/Bank info
    bank_transaction_ref = Column(String(100), index=True)
    qr_code_data = Column(Text)  # Base64 QR code
    
    # Webhook data
    webhook_data = Column(Text)  # JSON
    
    # Thời gian
    due_date = Column(DateTime)
    paid_at = Column(DateTime)
    verified_at = Column(DateTime)
    
    notes = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    hui_group = relationship("HuiGroup", back_populates="payments")
    membership = relationship("HuiMembership", back_populates="payments")

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String(50), nullable=False)  # sepay_transfer, etc.
    external_id = Column(String(100), index=True)  # Transaction ID từ Sepay
    payload = Column(Text, nullable=False)  # JSON
    signature = Column(String(255))
    status = Column(String(20), default="processing")  # processing, success, failed, pending_review
    error_message = Column(Text)
    payment_id = Column(String(36), ForeignKey("payments.id"))  # Payment được tạo/cập nhật
    is_verified = Column(Boolean, default=False)
    is_processed = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime)
    
    __table_args__ = (
        Index('idx_webhook_external', 'external_id'),
        Index('idx_webhook_status', 'status'),
    )

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"))
    action = Column(String(100), nullable=False)  # create_payment, verify_payment, etc.
    entity_type = Column(String(50))  # payment, hui_group, member, etc.
    entity_id = Column(String(36))
    
    old_value = Column(Text)  # JSON
    new_value = Column(Text)  # JSON
    ip_address = Column(String(50))
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (
        Index('idx_audit_user', 'user_id'),
        Index('idx_audit_entity', 'entity_type', 'entity_id'),
    )

    user = relationship("User", back_populates="audit_logs")


# Enum cho Batch Status
class BatchStatus(str, enum.Enum):
    PENDING = "pending"          # Chờ thanh toán
    PAID = "paid"                # Đã thanh toán đủ
    PARTIAL = "partial"          # Thanh toán thiếu
    OVERPAID = "overpaid"        # Thanh toán dư
    REVIEW = "review"            # Cần chủ hụi xử lý
    RESOLVED = "resolved"        # Đã xử lý xong


class PaymentBatch(Base):
    """
    Gom nhiều payments của 1 member trong 1 ngày thành 1 batch
    Member chỉ cần chuyển 1 lần với batch_code
    """
    __tablename__ = "payment_batches"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_id = Column(String(36), ForeignKey("members.id"), nullable=False)  # Tham chiếu bảng members
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)  # Chủ hụi
    
    # Batch identification
    batch_date = Column(DateTime, nullable=False)  # Ngày của batch
    batch_code = Column(String(50), unique=True, nullable=False, index=True)  # VD: BATCH_20260103_abc123
    
    # Amounts
    total_amount = Column(Float, nullable=False)           # Tổng tiền cần đóng
    received_amount = Column(Float, default=0)             # Số tiền đã nhận
    difference = Column(Float, default=0)                  # Chênh lệch (+ dư, - thiếu)
    
    # Status
    status = Column(SQLEnum(BatchStatus), default=BatchStatus.PENDING)
    
    # Webhook/Transaction info khi nhận tiền
    transaction_id = Column(String(100))                   # Sepay transaction ID
    transaction_content = Column(Text)                     # Nội dung chuyển khoản
    transaction_bank = Column(String(100))                 # Ngân hàng
    received_at = Column(DateTime)                         # Thời điểm nhận tiền
    
    # Resolution (khi chủ hụi xử lý)
    resolved_by = Column(String(36), ForeignKey("users.id"))
    resolved_at = Column(DateTime)
    resolution_note = Column(Text)                         # Ghi chú xử lý
    
    # QR Code
    qr_data = Column(Text)                                 # Base64 QR code
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    member = relationship("Member", foreign_keys=[member_id])  # Tham chiếu Member
    owner = relationship("User", foreign_keys=[owner_id])
    resolver = relationship("User", foreign_keys=[resolved_by])
    batch_items = relationship("BatchPayment", back_populates="batch", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_batch_member_date', 'member_id', 'batch_date'),
        Index('idx_batch_code', 'batch_code'),
        Index('idx_batch_status', 'status'),
    )


class BatchPayment(Base):
    """
    Liên kết giữa Batch và Payment
    Một batch có thể chứa nhiều payments từ nhiều dây hụi khác nhau
    """
    __tablename__ = "batch_payments"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id = Column(String(36), ForeignKey("payment_batches.id"), nullable=False)
    payment_id = Column(String(36), ForeignKey("payments.id"), nullable=False)
    
    # Snapshot thông tin payment tại thời điểm tạo batch
    hui_group_id = Column(String(36))
    hui_group_name = Column(String(255))
    cycle_number = Column(Integer)
    amount = Column(Float)
    
    # Trạng thái verify riêng cho từng payment trong batch
    is_verified = Column(Boolean, default=False)
    verified_at = Column(DateTime)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    batch = relationship("PaymentBatch", back_populates="batch_items")
    payment = relationship("Payment")
    
    __table_args__ = (
        Index('idx_batch_payment', 'batch_id', 'payment_id'),
    )



# Global Bank Config - Số TK chung cho chủ hụi
class GlobalBankConfig(Base):
    """
    Config số tài khoản ngân hàng chung cho chủ hụi
    Thay vì config riêng từng dây hụi
    """
    __tablename__ = "global_bank_configs"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, unique=True)
    
    bank_name = Column(String(100), nullable=False)
    bank_code = Column(String(20))  # VCB, TCB, MB...
    account_number = Column(String(50), nullable=False)
    account_name = Column(String(255))
    
    # QR settings
    qr_template = Column(String(50), default="compact")  # compact, qr_only, print
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationship
    owner = relationship("User", foreign_keys=[owner_id])


# Bill Send Status
class BillSendStatus(str, enum.Enum):
    PENDING = "pending"       # Chưa gửi
    SENT = "sent"             # Đã gửi thành công
    FAILED = "failed"         # Gửi thất bại
    NO_TELEGRAM = "no_telegram"  # Member chưa liên kết TG


# Bill Send History - Lịch sử gửi bill
class BillSendHistory(Base):
    """
    Lưu lịch sử gửi bill cho từng member
    Dùng để theo dõi và resend
    """
    __tablename__ = "bill_send_history"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    member_id = Column(String(36), ForeignKey("members.id"), nullable=False)  # Tham chiếu bảng members
    
    # Ngày gửi bill
    bill_date = Column(DateTime, nullable=False)  # Ngày của bill (due date)
    
    # Loại bill
    bill_type = Column(String(20))  # "single" hoặc "batch"
    batch_id = Column(String(36), ForeignKey("payment_batches.id"))  # Nếu là batch
    
    # Số tiền
    total_amount = Column(Float, nullable=False)
    items_count = Column(Integer, default=1)  # Số dây hụi trong bill
    
    # Trạng thái
    status = Column(SQLEnum(BillSendStatus), default=BillSendStatus.PENDING)
    error_message = Column(Text)
    
    # Kênh gửi
    sent_via = Column(String(50))  # "telegram_group", "telegram_private", "sms", "email"
    telegram_message_id = Column(String(100))  # ID message đã gửi (để có thể delete/edit)
    
    # Thời gian
    scheduled_at = Column(DateTime)  # Thời gian dự kiến gửi
    sent_at = Column(DateTime)       # Thời gian thực tế gửi
    
    # PDF và QR
    pdf_generated = Column(Boolean, default=False)
    qr_code = Column(Text)  # URL hoặc batch_code
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    owner = relationship("User", foreign_keys=[owner_id])
    member = relationship("Member", foreign_keys=[member_id])  # Tham chiếu Member
    batch = relationship("PaymentBatch", foreign_keys=[batch_id])
    
    __table_args__ = (
        Index('idx_bill_history_date', 'owner_id', 'bill_date'),
        Index('idx_bill_history_member', 'member_id', 'bill_date'),
        Index('idx_bill_history_status', 'status'),
    )


# Daily Bill Summary - Tổng hợp bill theo ngày (để hiển thị trên trang QR Tổng)
class DailyBillSummary(Base):
    """
    Tổng hợp tất cả members cần đóng trong 1 ngày
    Dùng cho trang Quản lý Thanh toán
    """
    __tablename__ = "daily_bill_summaries"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    bill_date = Column(DateTime, nullable=False)
    
    # Thống kê
    total_members = Column(Integer, default=0)      # Tổng số member cần đóng
    total_amount = Column(Float, default=0)         # Tổng số tiền cần thu
    
    members_with_telegram = Column(Integer, default=0)    # Có TG
    members_without_telegram = Column(Integer, default=0) # Chưa có TG
    
    bills_sent = Column(Integer, default=0)         # Đã gửi
    bills_pending = Column(Integer, default=0)      # Chưa gửi
    bills_failed = Column(Integer, default=0)       # Thất bại
    
    # Trạng thái gửi hàng loạt
    bulk_send_started_at = Column(DateTime)
    bulk_send_completed_at = Column(DateTime)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationship
    owner = relationship("User", foreign_keys=[owner_id])
    
    __table_args__ = (
        Index('idx_daily_summary_date', 'owner_id', 'bill_date', unique=True),
    )


class DebtRecord(Base):
    """Ghi nhận từng khoản nợ khi payment bị OVERDUE"""
    __tablename__ = "debt_records"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    member_id = Column(String(36), ForeignKey("members.id"), nullable=False)
    membership_id = Column(String(36), ForeignKey("hui_memberships.id"), nullable=False)
    payment_id = Column(String(36), ForeignKey("payments.id"), nullable=False, unique=True)
    hui_group_id = Column(String(36), ForeignKey("hui_groups.id"), nullable=False)
    
    # Số tiền
    original_amount = Column(Float, nullable=False)     # Số tiền gốc phải đóng
    late_fee = Column(Float, default=0)                 # Tiền phạt trễ hạn
    total_amount = Column(Float, nullable=False)        # Gốc + phạt
    paid_amount = Column(Float, default=0)              # Đã trả
    remaining_amount = Column(Float, nullable=False)    # Còn nợ
    
    # Thông tin quá hạn
    due_date = Column(DateTime, nullable=False)         # Hạn gốc
    days_overdue = Column(Integer, default=0)           # Số ngày quá hạn
    cycle_number = Column(Integer)                      # Kỳ nào
    
    # Trạng thái
    status = Column(SQLEnum(DebtStatus), default=DebtStatus.OUTSTANDING)
    
    # Xử lý
    resolved_at = Column(DateTime)
    resolved_by = Column(String(36), ForeignKey("users.id"))
    notes = Column(Text)
    
    # Telegram nhắc nợ
    last_reminder_at = Column(DateTime)
    reminder_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    owner = relationship("User", foreign_keys=[owner_id])
    member = relationship("Member", foreign_keys=[member_id])
    membership = relationship("HuiMembership", foreign_keys=[membership_id])
    payment = relationship("Payment", foreign_keys=[payment_id])
    hui_group = relationship("HuiGroup", foreign_keys=[hui_group_id])
    resolver = relationship("User", foreign_keys=[resolved_by])
    
    __table_args__ = (
        Index('idx_debt_owner', 'owner_id', 'status'),
        Index('idx_debt_member', 'member_id', 'status'),
        Index('idx_debt_payment', 'payment_id'),
    )
