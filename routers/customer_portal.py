"""
Customer Portal Router - APIs dành riêng cho thành viên hụi (member/customer)
Tất cả endpoints yêu cầu member token authentication

Enhanced v2: Financial statistics, payment calendar, next payout info
"""
from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import calendar

from database import get_db
from models import (
    Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    PaymentStatus, PaymentMethod, GlobalBankConfig, User
)
from routers.customer_deps import get_current_member
from routers.dependencies import get_vietnam_today_range, get_vietnam_now, logger
from schemas import CustomerProfileUpdate
from utils import (
    calculate_member_payment_amount, format_vnd,
    generate_reference_code
)

router = APIRouter(prefix="/customer", tags=["Customer Portal"])

# Generic error message — don't expose internals to customer
_INTERNAL_ERROR = "Lỗi hệ thống, vui lòng thử lại sau"


# ==========================================
# HELPER: Common queries (optimized — no N+1)
# ==========================================

def _get_active_memberships(db: Session, member_id: str):
    """Get all active memberships for a member"""
    return db.query(HuiMembership).filter(
        HuiMembership.member_id == member_id,
        HuiMembership.is_active == True
    ).all()


def _get_total_paid(db: Session, membership_ids: list) -> float:
    """Sum of all verified payments across memberships — single query"""
    if not membership_ids:
        return 0
    result = db.query(func.sum(Payment.amount)).filter(
        Payment.membership_id.in_(membership_ids),
        Payment.payment_status == PaymentStatus.VERIFIED
    ).scalar()
    return float(result or 0)


def _get_total_received(db: Session, membership_ids: list) -> float:
    """Sum of distribution amounts for cycles where member was receiver — single query (no N+1)"""
    if not membership_ids:
        return 0
    result = db.query(func.sum(HuiSchedule.distribution_amount)).filter(
        HuiSchedule.receiver_membership_id.in_(membership_ids),
        HuiSchedule.is_completed == True
    ).scalar()
    return float(result or 0)


def _batch_load_groups(db: Session, group_ids: list) -> dict:
    """Load multiple groups by IDs in one query, return dict {group_id: group}"""
    if not group_ids:
        return {}
    groups = db.query(HuiGroup).filter(HuiGroup.id.in_(group_ids)).all()
    return {str(g.id): g for g in groups}


def _get_next_payout_info(db: Session, memberships: list, group_map: dict):
    """Find the next scheduled payout date for this member across all groups"""
    best = None
    now = datetime.now(timezone.utc)

    # Collect membership IDs that haven't received yet
    pending_ms_ids = [ms.id for ms in memberships if not ms.has_received]
    if not pending_ms_ids:
        return None

    # Single query: get ALL future schedules where these memberships are receivers
    next_schedules = db.query(HuiSchedule).filter(
        HuiSchedule.receiver_membership_id.in_(pending_ms_ids),
        HuiSchedule.is_completed == False,
        HuiSchedule.due_date >= now
    ).order_by(HuiSchedule.due_date.asc()).all()

    # Build ms_id → group mapping
    ms_group = {str(ms.id): str(ms.hui_group_id) for ms in memberships}

    for schedule in next_schedules:
        group_id = ms_group.get(str(schedule.receiver_membership_id))
        group = group_map.get(group_id) if group_id else None
        if not group or not group.is_active:
            continue

        info = {
            "group_name": group.name,
            "group_id": group.id,
            "cycle_number": schedule.cycle_number,
            "date": schedule.due_date.isoformat() if schedule.due_date else None,
            "estimated_amount": schedule.distribution_amount or (group.amount_per_cycle * group.total_members),
        }

        if not best or (schedule.due_date and (not best.get("_date") or schedule.due_date < best["_date"])):
            info["_date"] = schedule.due_date
            best = info

    if best:
        best.pop("_date", None)
    return best


def _get_upcoming_payments(db: Session, memberships: list, group_map: dict, limit: int = 5):
    """Get next N upcoming payments across all groups — optimized"""
    now = datetime.now(timezone.utc)
    upcoming = []

    membership_ids = [ms.id for ms in memberships]
    ms_group = {str(ms.id): str(ms.hui_group_id) for ms in memberships}

    # Batch: get all active group_ids
    active_group_ids = [
        str(ms.hui_group_id) for ms in memberships
        if group_map.get(str(ms.hui_group_id)) and group_map[str(ms.hui_group_id)].is_active
    ]

    if not active_group_ids:
        return []

    # Batch: get all future incomplete schedules for these groups
    future_schedules = db.query(HuiSchedule).filter(
        HuiSchedule.hui_group_id.in_(active_group_ids),
        HuiSchedule.is_completed == False,
        HuiSchedule.due_date >= now,
    ).order_by(HuiSchedule.due_date.asc()).all()

    # Batch: get already paid payments for these memberships
    paid_schedule_ids = set()
    if membership_ids:
        paid_rows = db.query(Payment.schedule_id, Payment.membership_id).filter(
            Payment.membership_id.in_(membership_ids),
            Payment.payment_status == PaymentStatus.VERIFIED
        ).all()
        paid_schedule_ids = {(str(r.schedule_id), str(r.membership_id)) for r in paid_rows}

    for schedule in future_schedules:
        for ms in memberships:
            group = group_map.get(str(ms.hui_group_id))
            if not group or str(group.id) != str(schedule.hui_group_id):
                continue
            if not group.is_active:
                continue
            # Skip if member is receiver for this cycle
            if schedule.receiver_membership_id == ms.id:
                continue
            # Skip if already paid
            if (str(schedule.id), str(ms.id)) in paid_schedule_ids:
                continue

            amount = calculate_member_payment_amount(group, ms, schedule.cycle_number)
            days_until = (schedule.due_date - now).days if schedule.due_date else None

            upcoming.append({
                "group_name": group.name,
                "group_id": group.id,
                "cycle_number": schedule.cycle_number,
                "date": schedule.due_date.isoformat() if schedule.due_date else None,
                "amount": amount,
                "days_until": max(0, days_until) if days_until is not None else None,
            })

    # Sort by date and return top N
    upcoming.sort(key=lambda x: x.get("date") or "9999")
    return upcoming[:limit]


# ==========================================
# DASHBOARD (Enhanced)
# ==========================================

@router.get("/dashboard")
async def customer_dashboard(
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Dashboard tổng hợp: dây hụi, tài chính, kỳ hốt tiếp, lịch đóng tiền sắp tới"""
    try:
        today_start, today_end = get_vietnam_today_range()

        memberships = _get_active_memberships(db, current_member.id)

        if not memberships:
            return {
                "member_name": current_member.name,
                "total_hui_groups": 0,
                "total_due_today": 0,
                "total_receive_today": 0,
                "net_amount": 0,
                "hui_groups": [],
                "qr_data": None,
                # Enhanced fields
                "total_paid_all_time": 0,
                "total_received_all_time": 0,
                "total_remaining_to_pay": 0,
                "next_payout": None,
                "upcoming_payments": [],
                "financial_summary": {
                    "net_position": 0,
                    "completion_percentage": 0,
                }
            }

        membership_ids = [m.id for m in memberships]
        group_ids = list(set(str(m.hui_group_id) for m in memberships))

        # ---- Batch load groups (no N+1) ----
        group_map = _batch_load_groups(db, group_ids)

        # ---- Financial totals (single queries each) ----
        total_paid_all_time = _get_total_paid(db, membership_ids)
        total_received_all_time = _get_total_received(db, membership_ids)

        # ---- Batch load: paid payments for remaining calculation ----
        paid_by_schedule_ms = set()
        if membership_ids:
            paid_rows = db.query(Payment.schedule_id, Payment.membership_id).filter(
                Payment.membership_id.in_(membership_ids),
                Payment.payment_status == PaymentStatus.VERIFIED
            ).all()
            paid_by_schedule_ms = {(str(r.schedule_id), str(r.membership_id)) for r in paid_rows}

        # Calculate remaining payments
        total_remaining = 0
        total_expected_all = 0
        for ms in memberships:
            group = group_map.get(str(ms.hui_group_id))
            if not group or not group.is_active:
                continue
            # Remaining cycles where member needs to pay (not receiver)
            remaining_schedules = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.is_completed == False,
                HuiSchedule.receiver_membership_id != ms.id
            ).all()
            for s in remaining_schedules:
                if (str(s.id), str(ms.id)) not in paid_by_schedule_ms:
                    total_remaining += calculate_member_payment_amount(group, ms, s.cycle_number)

            total_expected_all += group.amount_per_cycle * group.total_cycles

        # ---- Next payout (optimized) ----
        next_payout = _get_next_payout_info(db, memberships, group_map)

        # ---- Upcoming payments (optimized) ----
        upcoming_payments = _get_upcoming_payments(db, memberships, group_map, limit=5)

        # ---- Today's data ----
        total_due_today = 0
        total_receive_today = 0
        hui_groups_data = []

        # Batch load per-group paid totals
        group_paid_map = {}
        if membership_ids:
            paid_agg = db.query(
                Payment.membership_id,
                func.sum(Payment.amount).label("total")
            ).filter(
                Payment.membership_id.in_(membership_ids),
                Payment.payment_status == PaymentStatus.VERIFIED
            ).group_by(Payment.membership_id).all()
            group_paid_map = {str(r.membership_id): float(r.total or 0) for r in paid_agg}

        for membership in memberships:
            group = group_map.get(str(membership.hui_group_id))
            if not group or not group.is_active:
                continue

            today_schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.due_date >= today_start,
                HuiSchedule.due_date < today_end
            ).first()

            amount_due_today = 0
            amount_receive_today = 0
            is_receiver_today = False
            cycle_today = None
            payment_status_today = None

            if today_schedule:
                cycle_today = today_schedule.cycle_number
                if today_schedule.receiver_membership_id == membership.id:
                    is_receiver_today = True
                    amount_receive_today = today_schedule.distribution_amount or 0
                    total_receive_today += amount_receive_today
                else:
                    amount_due_today = calculate_member_payment_amount(
                        group, membership, today_schedule.cycle_number
                    )
                    total_due_today += amount_due_today
                    existing_payment = db.query(Payment).filter(
                        Payment.schedule_id == today_schedule.id,
                        Payment.membership_id == membership.id
                    ).first()
                    if existing_payment:
                        payment_status_today = existing_payment.payment_status.value
                        if existing_payment.payment_status == PaymentStatus.VERIFIED:
                            total_due_today -= amount_due_today
                            amount_due_today = 0

            progress = round((group.current_cycle / group.total_cycles) * 100, 1) if group.total_cycles else 0

            hui_groups_data.append({
                "hui_group_id": group.id,
                "hui_group_name": group.name,
                "membership_id": membership.id,
                "amount_per_cycle": group.amount_per_cycle,
                "cycle_type": group.cycle_type.value,
                "current_cycle": group.current_cycle,
                "total_cycles": group.total_cycles,
                "progress": progress,
                "has_received": membership.has_received,
                "received_count": membership.received_count or 0,
                "slot_count": membership.slot_count or 1,
                "credit_score": membership.credit_score,
                "total_paid": group_paid_map.get(str(membership.id), 0),
                # Today
                "cycle_today": cycle_today,
                "amount_due_today": amount_due_today,
                "amount_receive_today": amount_receive_today,
                "is_receiver_today": is_receiver_today,
                "payment_status_today": payment_status_today,
            })

        net_amount = total_receive_today - total_due_today

        # QR
        qr_data = None
        if total_due_today > 0:
            qr_data = _generate_batch_qr(db, current_member, memberships, total_due_today)

        # Completion %
        completion_pct = round((total_paid_all_time / total_expected_all) * 100, 1) if total_expected_all > 0 else 0

        return {
            "member_name": current_member.name,
            "total_hui_groups": len(hui_groups_data),
            "total_due_today": total_due_today,
            "total_receive_today": total_receive_today,
            "net_amount": net_amount,
            "hui_groups": hui_groups_data,
            "qr_data": qr_data,
            # Enhanced
            "total_paid_all_time": total_paid_all_time,
            "total_received_all_time": total_received_all_time,
            "total_remaining_to_pay": total_remaining,
            "next_payout": next_payout,
            "upcoming_payments": upcoming_payments,
            "financial_summary": {
                "net_position": total_received_all_time - total_paid_all_time,
                "completion_percentage": completion_pct,
            }
        }

    except Exception as e:
        logger.error(f"Error customer dashboard: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


# ==========================================
# STATISTICS (Optimized)
# ==========================================

@router.get("/statistics")
async def customer_statistics(
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Thống kê tài chính tổng hợp: per-group breakdown, monthly chart, payment counts"""
    try:
        memberships = _get_active_memberships(db, current_member.id)
        # Also get inactive memberships for historical data
        all_memberships = db.query(HuiMembership).filter(
            HuiMembership.member_id == current_member.id
        ).all()
        all_membership_ids = [m.id for m in all_memberships]

        # ---- Overall totals (single queries) ----
        total_paid = _get_total_paid(db, all_membership_ids)
        total_received = _get_total_received(db, all_membership_ids)

        # ---- Payment counts ----
        payment_counts = {}
        for ps in [PaymentStatus.VERIFIED, PaymentStatus.PENDING, PaymentStatus.OVERDUE, PaymentStatus.FAILED]:
            count = db.query(Payment).filter(
                Payment.membership_id.in_(all_membership_ids),
                Payment.payment_status == ps
            ).count() if all_membership_ids else 0
            payment_counts[ps.value] = count

        # ---- Batch load groups ----
        all_group_ids = list(set(str(m.hui_group_id) for m in all_memberships))
        group_map = _batch_load_groups(db, all_group_ids)

        # ---- Batch load paid totals per membership ----
        paid_per_ms = {}
        if all_membership_ids:
            paid_agg = db.query(
                Payment.membership_id,
                func.sum(Payment.amount).label("total"),
                func.count(Payment.id).label("count")
            ).filter(
                Payment.membership_id.in_(all_membership_ids),
                Payment.payment_status == PaymentStatus.VERIFIED
            ).group_by(Payment.membership_id).all()
            paid_per_ms = {str(r.membership_id): {"total": float(r.total or 0), "count": r.count} for r in paid_agg}

        # ---- Batch load received totals per membership ----
        received_per_ms = {}
        if all_membership_ids:
            recv_agg = db.query(
                HuiSchedule.receiver_membership_id,
                func.sum(HuiSchedule.distribution_amount).label("total")
            ).filter(
                HuiSchedule.receiver_membership_id.in_(all_membership_ids),
                HuiSchedule.is_completed == True
            ).group_by(HuiSchedule.receiver_membership_id).all()
            received_per_ms = {str(r.receiver_membership_id): float(r.total or 0) for r in recv_agg}

        # ---- Per-group breakdown ----
        per_group = []
        total_remaining = 0
        for ms in all_memberships:
            group = group_map.get(str(ms.hui_group_id))
            if not group:
                continue

            group_paid_data = paid_per_ms.get(str(ms.id), {"total": 0, "count": 0})
            group_paid = group_paid_data["total"]
            paid_cycles = group_paid_data["count"]

            group_received = received_per_ms.get(str(ms.id), 0)

            # Remaining
            total_cycles_to_pay = group.total_cycles - (ms.slot_count or 1)  # subtract receiver cycles
            remaining_cycles = max(0, total_cycles_to_pay - paid_cycles)
            remaining_amount = remaining_cycles * group.amount_per_cycle
            total_remaining += remaining_amount

            # Next receive date (if not yet received)
            next_receive_date = None
            next_receive_amount = None
            if not ms.has_received:
                next_recv = db.query(HuiSchedule).filter(
                    HuiSchedule.hui_group_id == group.id,
                    HuiSchedule.receiver_membership_id == ms.id,
                    HuiSchedule.is_completed == False
                ).order_by(HuiSchedule.due_date.asc()).first()
                if next_recv:
                    next_receive_date = next_recv.due_date.isoformat() if next_recv.due_date else None
                    next_receive_amount = next_recv.distribution_amount or (group.amount_per_cycle * group.total_members)

            per_group.append({
                "group_id": group.id,
                "group_name": group.name,
                "is_active": group.is_active,
                "total_cycles": group.total_cycles,
                "current_cycle": group.current_cycle,
                "amount_per_cycle": group.amount_per_cycle,
                "total_paid": float(group_paid),
                "total_received": float(group_received),
                "net_position": float(group_received - float(group_paid)),
                "remaining_cycles": remaining_cycles,
                "remaining_amount": remaining_amount,
                "has_received": ms.has_received,
                "received_count": ms.received_count or 0,
                "next_receive_date": next_receive_date,
                "next_receive_amount": next_receive_amount,
            })

        # ---- Monthly chart (last 12 months) — accurate month math ----
        monthly_chart = []
        now = get_vietnam_now()
        for i in range(11, -1, -1):
            # Go back i months from current month
            target_month = now.month - i
            target_year = now.year
            while target_month <= 0:
                target_month += 12
                target_year -= 1

            month_start = now.replace(
                year=target_year, month=target_month, day=1,
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None
            )
            _, last_day = calendar.monthrange(target_year, target_month)
            if target_month == 12:
                month_end = month_start.replace(year=target_year + 1, month=1)
            else:
                month_end = month_start.replace(month=target_month + 1)

            # Paid this month — single query
            month_paid = 0
            if all_membership_ids:
                month_paid = db.query(func.sum(Payment.amount)).filter(
                    Payment.membership_id.in_(all_membership_ids),
                    Payment.payment_status == PaymentStatus.VERIFIED,
                    Payment.paid_at >= month_start,
                    Payment.paid_at < month_end
                ).scalar() or 0

            # Received this month — single query (no N+1)
            month_received = 0
            if all_membership_ids:
                month_received = db.query(func.sum(HuiSchedule.distribution_amount)).filter(
                    HuiSchedule.receiver_membership_id.in_(all_membership_ids),
                    HuiSchedule.is_completed == True,
                    HuiSchedule.completed_at >= month_start,
                    HuiSchedule.completed_at < month_end
                ).scalar() or 0

            monthly_chart.append({
                "month": month_start.strftime("%Y-%m"),
                "month_label": month_start.strftime("%m/%Y"),
                "paid": float(month_paid),
                "received": float(month_received),
            })

        return {
            "total_paid": total_paid,
            "total_received": total_received,
            "net_position": total_received - total_paid,
            "total_remaining": total_remaining,
            "payment_count": payment_counts,
            "per_group": per_group,
            "monthly_chart": monthly_chart,
        }

    except Exception as e:
        logger.error(f"Error customer statistics: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


# ==========================================
# PAYMENT CALENDAR (Optimized)
# ==========================================

@router.get("/calendar")
async def customer_calendar(
    months: int = 3,
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Lịch thanh toán và nhận tiền trong N tháng tới"""
    try:
        now = datetime.now(timezone.utc)
        end_date = now + timedelta(days=months * 30)

        memberships = _get_active_memberships(db, current_member.id)
        group_ids = list(set(str(ms.hui_group_id) for ms in memberships))
        group_map = _batch_load_groups(db, group_ids)

        # Batch: all verified payments for these memberships
        membership_ids = [ms.id for ms in memberships]
        paid_set = set()
        if membership_ids:
            paid_rows = db.query(Payment.schedule_id, Payment.membership_id).filter(
                Payment.membership_id.in_(membership_ids),
                Payment.payment_status == PaymentStatus.VERIFIED
            ).all()
            paid_set = {(str(r.schedule_id), str(r.membership_id)) for r in paid_rows}

        events = []

        for ms in memberships:
            group = group_map.get(str(ms.hui_group_id))
            if not group or not group.is_active:
                continue

            schedules = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.due_date >= now,
                HuiSchedule.due_date <= end_date,
                HuiSchedule.is_completed == False
            ).order_by(HuiSchedule.due_date.asc()).all()

            for schedule in schedules:
                is_receiver = schedule.receiver_membership_id == ms.id

                if is_receiver:
                    events.append({
                        "date": schedule.due_date.isoformat() if schedule.due_date else None,
                        "type": "receive",
                        "group_name": group.name,
                        "group_id": group.id,
                        "cycle_number": schedule.cycle_number,
                        "amount": schedule.distribution_amount or (group.amount_per_cycle * group.total_members),
                        "days_until": max(0, (schedule.due_date - now).days) if schedule.due_date else None,
                    })
                else:
                    # Skip if already paid (use batch-loaded set)
                    if (str(schedule.id), str(ms.id)) in paid_set:
                        continue

                    amount = calculate_member_payment_amount(group, ms, schedule.cycle_number)
                    events.append({
                        "date": schedule.due_date.isoformat() if schedule.due_date else None,
                        "type": "payment",
                        "group_name": group.name,
                        "group_id": group.id,
                        "cycle_number": schedule.cycle_number,
                        "amount": amount,
                        "days_until": max(0, (schedule.due_date - now).days) if schedule.due_date else None,
                    })

        events.sort(key=lambda x: x.get("date") or "9999")
        return {"events": events}

    except Exception as e:
        logger.error(f"Error customer calendar: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


# ==========================================
# HUI GROUPS
# ==========================================

@router.get("/hui-groups")
async def customer_hui_groups(
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Danh sách tất cả dây hụi mà thành viên đang tham gia"""
    try:
        memberships = _get_active_memberships(db, current_member.id)

        if not memberships:
            return []

        # Batch load groups + paid amounts
        group_ids = list(set(str(m.hui_group_id) for m in memberships))
        group_map = _batch_load_groups(db, group_ids)

        membership_ids = [m.id for m in memberships]
        paid_agg = {}
        count_agg = {}
        if membership_ids:
            rows = db.query(
                Payment.membership_id,
                func.sum(Payment.amount).label("total"),
                func.count(Payment.id).label("count")
            ).filter(
                Payment.membership_id.in_(membership_ids),
                Payment.payment_status == PaymentStatus.VERIFIED
            ).group_by(Payment.membership_id).all()
            paid_agg = {str(r.membership_id): float(r.total or 0) for r in rows}
            count_agg = {str(r.membership_id): r.count for r in rows}

        result = []
        for membership in memberships:
            group = group_map.get(str(membership.hui_group_id))
            if not group:
                continue

            progress = round((group.current_cycle / group.total_cycles) * 100, 1) if group.total_cycles else 0

            result.append({
                "hui_group_id": group.id,
                "hui_group_name": group.name,
                "membership_id": membership.id,
                "amount_per_cycle": group.amount_per_cycle,
                "interest_per_cycle": group.interest_per_cycle or 0,
                "cycle_type": group.cycle_type.value,
                "current_cycle": group.current_cycle,
                "total_cycles": group.total_cycles,
                "total_members": group.total_members,
                "progress": progress,
                "has_received": membership.has_received,
                "received_count": membership.received_count or 0,
                "received_cycle": membership.received_cycle,
                "slot_count": membership.slot_count or 1,
                "credit_score": membership.credit_score,
                "payment_code": membership.payment_code,
                "total_paid_cycles": count_agg.get(str(membership.id), 0),
                "total_paid_amount": paid_agg.get(str(membership.id), 0),
                "start_date": group.start_date.isoformat() if group.start_date else None,
                "end_date": group.end_date.isoformat() if group.end_date else None,
            })

        return result

    except Exception as e:
        logger.error(f"Error listing customer hui groups: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.get("/hui-groups/{hui_group_id}")
async def customer_hui_group_detail(
    hui_group_id: str,
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Chi tiết dây hụi: thông tin nhóm, tài chính, kỳ hốt tiếp, lịch sử kỳ"""
    try:
        membership = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == hui_group_id,
            HuiMembership.member_id == current_member.id,
            HuiMembership.is_active == True
        ).first()

        if not membership:
            raise HTTPException(status_code=404, detail="Bạn không tham gia dây hụi này")

        group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")

        # ---- Financial summary for this group ----
        total_paid_this_group = db.query(func.sum(Payment.amount)).filter(
            Payment.membership_id == membership.id,
            Payment.payment_status == PaymentStatus.VERIFIED
        ).scalar() or 0

        # Received for this group (single query)
        total_received_this_group = db.query(func.sum(HuiSchedule.distribution_amount)).filter(
            HuiSchedule.receiver_membership_id == membership.id,
            HuiSchedule.is_completed == True
        ).scalar() or 0
        total_received_this_group = float(total_received_this_group)

        # Remaining — batch check paid
        remaining_pay_schedules = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group.id,
            HuiSchedule.is_completed == False,
            HuiSchedule.receiver_membership_id != membership.id
        ).all()

        paid_schedule_ids = set()
        if remaining_pay_schedules:
            schedule_ids = [s.id for s in remaining_pay_schedules]
            paid_rows = db.query(Payment.schedule_id).filter(
                Payment.schedule_id.in_(schedule_ids),
                Payment.membership_id == membership.id,
                Payment.payment_status == PaymentStatus.VERIFIED
            ).all()
            paid_schedule_ids = {str(r.schedule_id) for r in paid_rows}

        remaining_cycles = 0
        remaining_amount = 0
        for s in remaining_pay_schedules:
            if str(s.id) not in paid_schedule_ids:
                remaining_cycles += 1
                remaining_amount += calculate_member_payment_amount(group, membership, s.cycle_number)

        # ---- Next receive info ----
        next_receive_info = None
        if not membership.has_received:
            next_recv = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.receiver_membership_id == membership.id,
                HuiSchedule.is_completed == False
            ).order_by(HuiSchedule.due_date.asc()).first()
            if next_recv:
                days_until = (next_recv.due_date - datetime.now(timezone.utc)).days if next_recv.due_date else None
                next_receive_info = {
                    "cycle_number": next_recv.cycle_number,
                    "date": next_recv.due_date.isoformat() if next_recv.due_date else None,
                    "estimated_amount": next_recv.distribution_amount or (group.amount_per_cycle * group.total_members),
                    "days_until": max(0, days_until) if days_until is not None else None,
                }

        # ---- Next payment info ----
        next_payment_info = None
        now = datetime.now(timezone.utc)
        next_pay_schedule = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group.id,
            HuiSchedule.is_completed == False,
            HuiSchedule.due_date >= now,
            HuiSchedule.receiver_membership_id != membership.id
        ).order_by(HuiSchedule.due_date.asc()).first()
        if next_pay_schedule:
            already_paid = str(next_pay_schedule.id) in paid_schedule_ids
            if not already_paid:
                days_until = (next_pay_schedule.due_date - now).days if next_pay_schedule.due_date else None
                next_payment_info = {
                    "cycle_number": next_pay_schedule.cycle_number,
                    "date": next_pay_schedule.due_date.isoformat() if next_pay_schedule.due_date else None,
                    "amount": calculate_member_payment_amount(group, membership, next_pay_schedule.cycle_number),
                    "days_until": max(0, days_until) if days_until is not None else None,
                }

        # ---- Schedule timeline ----
        schedules = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == hui_group_id
        ).order_by(HuiSchedule.cycle_number.asc()).all()

        payments = db.query(Payment).filter(
            Payment.membership_id == membership.id,
        ).all()
        payment_by_schedule = {str(p.schedule_id): p for p in payments}

        # Batch load receiver names
        receiver_ms_ids = [str(s.receiver_membership_id) for s in schedules if s.receiver_membership_id]
        receiver_names = {}
        if receiver_ms_ids:
            receiver_memberships = db.query(HuiMembership).filter(
                HuiMembership.id.in_(receiver_ms_ids)
            ).all()
            member_ids_for_names = [rm.member_id for rm in receiver_memberships]
            if member_ids_for_names:
                members_list = db.query(Member).filter(Member.id.in_(member_ids_for_names)).all()
                member_name_map = {str(m.id): m.name for m in members_list}
                for rm in receiver_memberships:
                    receiver_names[str(rm.id)] = member_name_map.get(str(rm.member_id), "N/A")

        schedule_timeline = []
        for schedule in schedules:
            payment = payment_by_schedule.get(str(schedule.id))
            is_receiver = str(schedule.receiver_membership_id) == str(membership.id) if schedule.receiver_membership_id else False

            receiver_name = receiver_names.get(str(schedule.receiver_membership_id)) if schedule.receiver_membership_id else None

            amount_to_pay = 0
            if not is_receiver:
                amount_to_pay = calculate_member_payment_amount(group, membership, schedule.cycle_number)

            schedule_timeline.append({
                "cycle_number": schedule.cycle_number,
                "due_date": schedule.due_date.isoformat() if schedule.due_date else None,
                "is_completed": schedule.is_completed,
                "is_receiver": is_receiver,
                "receiver_name": receiver_name,
                "distribution_amount": schedule.distribution_amount or 0,
                "amount_to_pay": amount_to_pay,
                "payment_status": payment.payment_status.value if payment else ("receiver" if is_receiver else "not_created"),
                "paid_at": payment.paid_at.isoformat() if payment and payment.paid_at else None,
            })

        # Owner info
        owner = db.query(User).filter(User.id == group.owner_id).first()

        next_payment_amount = calculate_member_payment_amount(group, membership)
        qr_data = _generate_single_qr(db, group, membership, current_member)

        return {
            "hui_group": {
                "id": group.id,
                "name": group.name,
                "amount_per_cycle": group.amount_per_cycle,
                "interest_per_cycle": group.interest_per_cycle or 0,
                "total_members": group.total_members,
                "cycle_type": group.cycle_type.value,
                "cycle_interval": group.cycle_interval,
                "current_cycle": group.current_cycle,
                "total_cycles": group.total_cycles,
                "fee_type": group.fee_type,
                "fee_value": group.fee_value,
                "hui_method": group.hui_method.value if group.hui_method else "assigned",
                "start_date": group.start_date.isoformat() if group.start_date else None,
                "end_date": group.end_date.isoformat() if group.end_date else None,
                "owner_name": owner.name if owner else "N/A",
            },
            "membership": {
                "id": membership.id,
                "slot_count": membership.slot_count or 1,
                "payment_code": membership.payment_code,
                "has_received": membership.has_received,
                "received_count": membership.received_count or 0,
                "received_cycle": membership.received_cycle,
                "credit_score": membership.credit_score,
                "risk_level": membership.risk_level.value if membership.risk_level else "low",
                "rebate_percentage": membership.rebate_percentage or 0,
                "total_rebate_received": membership.total_rebate_received or 0,
            },
            # Enhanced financial
            "financial": {
                "total_paid": float(total_paid_this_group),
                "total_received": float(total_received_this_group),
                "net_position": float(total_received_this_group - float(total_paid_this_group)),
                "remaining_cycles": remaining_cycles,
                "remaining_amount": remaining_amount,
            },
            "next_receive_info": next_receive_info,
            "next_payment_info": next_payment_info,
            "next_payment_amount": next_payment_amount,
            "schedule_timeline": schedule_timeline,
            "qr_data": qr_data,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error customer hui detail: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


# ==========================================
# PROFILE
# ==========================================

@router.get("/profile")
async def customer_profile(
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Xem thông tin cá nhân của thành viên"""
    memberships = db.query(HuiMembership).filter(
        HuiMembership.member_id == current_member.id,
        HuiMembership.is_active == True
    ).all()

    return {
        "id": current_member.id,
        "name": current_member.name,
        "phone": current_member.phone,
        "email": current_member.email,
        "address": current_member.address,
        "cccd": current_member.cccd,
        "telegram_chat_id": current_member.telegram_chat_id,
        "total_memberships": len(memberships),
        "created_at": current_member.created_at.isoformat() if current_member.created_at else None,
    }


@router.put("/profile")
async def customer_update_profile(
    data: CustomerProfileUpdate,
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Cập nhật thông tin cá nhân (tên, email, địa chỉ). SĐT và CCCD không cho sửa."""
    try:
        member = db.query(Member).filter(Member.id == current_member.id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")

        if data.name is not None and data.name.strip():
            member.name = data.name.strip()
        if data.email is not None:
            member.email = data.email.strip() if data.email.strip() else None
        if data.address is not None:
            member.address = data.address.strip() if data.address.strip() else None

        db.commit()
        db.refresh(member)

        return {
            "success": True,
            "message": "Cập nhật thông tin thành công",
            "member": {
                "id": member.id,
                "name": member.name,
                "phone": member.phone,
                "email": member.email,
                "address": member.address,
                "cccd": member.cccd,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating customer profile: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


# ==========================================
# PAYMENTS HISTORY (with pagination)
# ==========================================

@router.get("/payments")
async def customer_payments(
    skip: int = Query(0, ge=0, description="Số bản ghi bỏ qua"),
    limit: int = Query(50, ge=1, le=200, description="Số bản ghi tối đa"),
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Lịch sử thanh toán của thành viên (có phân trang)"""
    try:
        memberships = db.query(HuiMembership).filter(
            HuiMembership.member_id == current_member.id
        ).all()

        membership_ids = [m.id for m in memberships]
        if not membership_ids:
            return []

        # Batch load groups
        group_ids = list(set(str(m.hui_group_id) for m in memberships))
        group_map = _batch_load_groups(db, group_ids)
        ms_group_map = {str(m.id): str(m.hui_group_id) for m in memberships}

        payments = db.query(Payment).filter(
            Payment.membership_id.in_(membership_ids)
        ).order_by(Payment.created_at.desc()).offset(skip).limit(limit).all()

        # Batch load schedules for these payments
        schedule_ids = [p.schedule_id for p in payments if p.schedule_id]
        schedule_map = {}
        if schedule_ids:
            schedules = db.query(HuiSchedule).filter(HuiSchedule.id.in_(schedule_ids)).all()
            schedule_map = {str(s.id): s for s in schedules}

        result = []
        for p in payments:
            group_id = ms_group_map.get(str(p.membership_id))
            group = group_map.get(group_id) if group_id else None
            schedule = schedule_map.get(str(p.schedule_id)) if p.schedule_id else None

            result.append({
                "payment_id": p.id,
                "hui_group_name": group.name if group else "N/A",
                "cycle_number": schedule.cycle_number if schedule else None,
                "amount": p.amount,
                "payment_status": p.payment_status.value,
                "payment_method": p.payment_method.value if p.payment_method else None,
                "reference_code": p.reference_code,
                "due_date": p.due_date.isoformat() if p.due_date else None,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })

        return result

    except Exception as e:
        logger.error(f"Error listing customer payments: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


# ==========================================
# QR CODE
# ==========================================

@router.get("/qr/{membership_id}")
async def customer_qr_single(
    membership_id: str,
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """Tạo QR code thanh toán cho 1 membership cụ thể"""
    try:
        membership = db.query(HuiMembership).filter(
            HuiMembership.id == membership_id,
            HuiMembership.member_id == current_member.id,
            HuiMembership.is_active == True
        ).first()

        if not membership:
            raise HTTPException(status_code=404, detail="Không tìm thấy thông tin thành viên")

        group = db.query(HuiGroup).filter(HuiGroup.id == membership.hui_group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")

        qr_data = _generate_single_qr(db, group, membership, current_member)
        return qr_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating customer QR: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.get("/qr-batch")
async def customer_qr_batch(
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db)
):
    """QR code tổng hợp cho tất cả khoản phải đóng hôm nay"""
    try:
        today_start, today_end = get_vietnam_today_range()

        memberships = _get_active_memberships(db, current_member.id)

        if not memberships:
            return {"total_due": 0, "items": [], "qr_data": None}

        group_ids = list(set(str(ms.hui_group_id) for ms in memberships))
        group_map = _batch_load_groups(db, group_ids)
        membership_ids = [ms.id for ms in memberships]

        # Batch: check already paid
        paid_set = set()
        if membership_ids:
            paid_rows = db.query(Payment.schedule_id, Payment.membership_id).filter(
                Payment.membership_id.in_(membership_ids),
                Payment.payment_status == PaymentStatus.VERIFIED
            ).all()
            paid_set = {(str(r.schedule_id), str(r.membership_id)) for r in paid_rows}

        total_due = 0
        items = []

        for membership in memberships:
            group = group_map.get(str(membership.hui_group_id))
            if not group or not group.is_active:
                continue

            today_schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.due_date >= today_start,
                HuiSchedule.due_date < today_end
            ).first()

            if not today_schedule:
                continue
            if today_schedule.receiver_membership_id == membership.id:
                continue

            if (str(today_schedule.id), str(membership.id)) in paid_set:
                continue

            amount = calculate_member_payment_amount(group, membership, today_schedule.cycle_number)
            total_due += amount
            items.append({
                "hui_group_name": group.name,
                "cycle_number": today_schedule.cycle_number,
                "amount": amount,
            })

        qr_data = _generate_batch_qr(db, current_member, memberships, total_due) if total_due > 0 else None

        return {
            "total_due": total_due,
            "items": items,
            "qr_data": qr_data,
        }

    except Exception as e:
        logger.error(f"Error generating batch QR: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


# ==========================================
# HELPER: QR Generation
# ==========================================

def _generate_single_qr(db: Session, group: HuiGroup, membership: HuiMembership, member: Member):
    """Generate VietQR data for a single hui group payment"""
    bank_config = db.query(GlobalBankConfig).filter(
        GlobalBankConfig.owner_id == group.owner_id,
        GlobalBankConfig.is_active == True
    ).first()

    bank_name = bank_config.bank_name if bank_config else (group.bank_name or "")
    account_number = bank_config.account_number if bank_config else (group.bank_account_number or "")
    account_name = bank_config.account_name if bank_config else (group.bank_account_name or "")
    bank_code = bank_config.bank_code if bank_config else ""

    amount = calculate_member_payment_amount(group, membership)
    ref_code = generate_reference_code(group.id, member.id, group.current_cycle)

    qr_url = ""
    if account_number and bank_code:
        qr_url = f"https://img.vietqr.io/image/{bank_code}-{account_number}-compact.jpg?amount={int(amount)}&addInfo={ref_code}&accountName={account_name}"

    return {
        "bank_name": bank_name,
        "account_number": account_number,
        "account_name": account_name,
        "amount": amount,
        "amount_formatted": format_vnd(amount),
        "reference_code": ref_code,
        "transfer_content": ref_code,
        "qr_url": qr_url,
        "hui_group_name": group.name,
        "cycle_number": group.current_cycle,
    }


def _generate_batch_qr(db: Session, member: Member, memberships, total_amount: float):
    """Generate VietQR for batch payment (all hui groups combined)"""
    if not memberships or total_amount <= 0:
        return None

    first_ms = memberships[0]
    group = db.query(HuiGroup).filter(HuiGroup.id == first_ms.hui_group_id).first()
    if not group:
        return None

    bank_config = db.query(GlobalBankConfig).filter(
        GlobalBankConfig.owner_id == group.owner_id,
        GlobalBankConfig.is_active == True
    ).first()

    bank_name = bank_config.bank_name if bank_config else (group.bank_name or "")
    account_number = bank_config.account_number if bank_config else (group.bank_account_number or "")
    account_name = bank_config.account_name if bank_config else (group.bank_account_name or "")
    bank_code = bank_config.bank_code if bank_config else ""

    batch_ref = f"BATCH{member.id[:8].upper()}"

    qr_url = ""
    if account_number and bank_code:
        qr_url = f"https://img.vietqr.io/image/{bank_code}-{account_number}-compact.jpg?amount={int(total_amount)}&addInfo={batch_ref}&accountName={account_name}"

    return {
        "bank_name": bank_name,
        "account_number": account_number,
        "account_name": account_name,
        "amount": total_amount,
        "amount_formatted": format_vnd(total_amount),
        "reference_code": batch_ref,
        "transfer_content": batch_ref,
        "qr_url": qr_url,
    }
