from datetime import datetime, timedelta
from typing import List, TYPE_CHECKING
from models import HuiCycle
import json

# Type checking imports to avoid circular imports
if TYPE_CHECKING:
    from models import HuiGroup, HuiMembership


def calculate_member_payment_amount(
    hui_group,  # HuiGroup
    membership,  # HuiMembership
    current_cycle: int = None
) -> float:
    """
    Tính số tiền thành viên phải đóng dựa trên logic "hụi sống":
    - Chưa hốt: đóng số tiền gốc (amount_per_cycle)
    - Đã hốt: đóng số tiền gốc + lãi (amount_per_cycle + interest_per_cycle)
    
    Args:
        hui_group: Dây hụi
        membership: Thông tin thành viên trong dây hụi
        current_cycle: Kỳ hiện tại (optional, dùng hui_group.current_cycle nếu không truyền)
    
    Returns:
        float: Số tiền phải đóng
    """
    base_amount = float(hui_group.amount_per_cycle)
    interest_amount = float(hui_group.interest_per_cycle or 0)
    slot_count = int(membership.slot_count or 1)
    
    # Kiểm tra thành viên đã hốt chưa
    has_received = False
    
    # Cách 1: Check flag has_received hoặc received_count
    if membership.has_received or (membership.received_count and membership.received_count > 0):
        has_received = True
    
    # Cách 2: Check received_cycle (legacy - kỳ đã hốt)
    if membership.received_cycle and membership.received_cycle > 0:
        cycle_to_check = current_cycle or hui_group.current_cycle
        if membership.received_cycle < cycle_to_check:
            # Đã hốt ở kỳ trước → đóng thêm lãi
            has_received = True
    
    if has_received:
        # Đã hốt → đóng gốc + lãi
        return (base_amount + interest_amount) * slot_count
    else:
        # Chưa hốt → đóng gốc
        return base_amount * slot_count

def format_vnd(amount: float) -> str:
    """Format amount as Vietnamese Dong"""
    return f"{int(amount):,} VNĐ".replace(",", ".")

def generate_payment_code(hui_group_id: str, member_id: str) -> str:
    """Generate unique payment code for member"""
    # Format: HUI{first 8 chars of group_id}M{first 8 chars of member_id}
    return f"HUI{hui_group_id[:8].upper()}M{member_id[:8].upper()}"

def generate_reference_code(hui_group_id: str, member_id: str, cycle: int) -> str:
    """Generate reference code for payment"""
    return f"HUI{hui_group_id[:8].upper()}M{member_id[:8].upper()}C{cycle}"

def calculate_next_due_date(start_date: datetime, cycle_type: HuiCycle, cycle_number: int, cycle_interval: int = 1) -> datetime:
    """Calculate due date for a specific cycle
    Args:
        start_date: Ngày bắt đầu
        cycle_type: Loại chu kỳ (ngày/tuần/tháng)
        cycle_number: Kỳ thứ mấy (1, 2, 3...)
        cycle_interval: Khoảng cách giữa các kỳ (mỗi X ngày/tuần/tháng)
    """
    intervals = (cycle_number - 1) * cycle_interval
    
    if cycle_type == HuiCycle.DAILY:
        return start_date + timedelta(days=intervals)
    elif cycle_type == HuiCycle.WEEKLY:
        return start_date + timedelta(weeks=intervals)
    elif cycle_type == HuiCycle.MONTHLY:
        # Add months (approximate)
        months = intervals
        year = start_date.year + months // 12
        month = start_date.month + months % 12
        if month > 12:
            year += 1
            month -= 12
        return start_date.replace(year=year, month=month)
    return start_date

def calculate_owner_fee(amount: float, fee_type: str, fee_value: float) -> float:
    """Calculate owner fee"""
    if fee_type == "percentage":
        return amount * (fee_value / 100)
    elif fee_type == "fixed":
        return fee_value
    return 0

def calculate_credit_score(on_time_count: int, late_count: int, total_amount_paid: float, total_late_amount: float) -> int:
    """Calculate member credit score (0-100)"""
    base_score = 100
    
    # Deduct for late payments
    late_penalty = late_count * 5  # -5 points per late payment
    
    # Deduct for late amount ratio
    if total_amount_paid > 0:
        late_ratio = total_late_amount / total_amount_paid
        ratio_penalty = int(late_ratio * 30)  # Up to -30 points
    else:
        ratio_penalty = 0
    
    score = base_score - late_penalty - ratio_penalty
    return max(0, min(100, score))  # Keep between 0-100

def determine_risk_level(credit_score: int, consecutive_late: int) -> str:
    """Determine risk level based on credit score and behavior"""
    if credit_score >= 80 and consecutive_late == 0:
        return "low"
    elif credit_score >= 60 and consecutive_late <= 1:
        return "medium"
    elif credit_score >= 40:
        return "high"
    else:
        return "critical"

def safe_json_loads(json_str: str, default=None):
    """Safely parse JSON string"""
    try:
        return json.loads(json_str) if json_str else default
    except:
        return default

def safe_json_dumps(obj) -> str:
    """Safely convert object to JSON string"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except:
        return "{}"