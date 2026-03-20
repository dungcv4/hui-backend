"""
Dashboard Router - Dashboard summary and statistics endpoints
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import timedelta

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, Payment, PaymentStatus,
    HuiSchedule, DashboardSummary, require_role,
    get_vietnam_today_range, get_vietnam_week_start, get_vietnam_month_start,
    logger
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/pending-payments")
async def get_pending_payments(
    period: str = "today",
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Danh sách chi tiết TẤT CẢ các thành viên cần đóng tiền theo period"""
    try:
        today_start, today_end = get_vietnam_today_range()
        week_start = get_vietnam_week_start()
        month_start = get_vietnam_month_start()
        
        if period == "week":
            period_start_naive = week_start
            period_end_naive = today_end
        elif period == "month":
            period_start_naive = month_start
            period_end_naive = today_end
        else:
            period_start_naive = today_start
            period_end_naive = today_end
        
        groups = db.query(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiGroup.is_active
        ).all()
        group_ids = [g.id for g in groups]
        group_map = {str(g.id): g for g in groups}
        
        if not group_ids:
            return []
        
        schedules = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id.in_(group_ids),
            HuiSchedule.due_date >= period_start_naive,
            HuiSchedule.due_date < period_end_naive
        ).all()
        
        if not schedules:
            return []
        
        schedule_pot_receivers = {
            str(s.id): str(s.receiver_membership_id) if s.receiver_membership_id else None
            for s in schedules
        }
        schedule_ids = [s.id for s in schedules]
        
        existing_payments = db.query(Payment).filter(
            Payment.schedule_id.in_(schedule_ids)
        ).all()
        
        payment_lookup = {}
        for p in existing_payments:
            key = (str(p.schedule_id), str(p.membership_id))
            payment_lookup[key] = p
        
        result = []
        for schedule in schedules:
            group = group_map.get(str(schedule.hui_group_id))
            if not group:
                continue
            
            pot_receiver_id = schedule_pot_receivers.get(str(schedule.id))
            
            memberships = db.query(HuiMembership).filter(
                HuiMembership.hui_group_id == schedule.hui_group_id,
                HuiMembership.is_active == True
            ).all()
            
            for membership in memberships:
                if pot_receiver_id and str(membership.id) == pot_receiver_id:
                    continue
                
                member = db.query(Member).filter(Member.id == membership.member_id).first()
                if not member:
                    continue
                
                payment_key = (str(schedule.id), str(membership.id))
                existing_payment = payment_lookup.get(payment_key)
                
                if existing_payment and existing_payment.payment_status == PaymentStatus.VERIFIED:
                    continue
                
                slot_count = membership.slot_count or 1
                # Tính tiền đúng theo logic hụi sống (gốc hoặc gốc+lãi)
                amount = calculate_member_payment_amount(group, membership, schedule.cycle_number)
                
                result.append({
                    "payment_id": str(existing_payment.id) if existing_payment else None,
                    "member_name": member.name,
                    "member_phone": member.phone or "",
                    "amount": amount,
                    "hui_group_id": str(schedule.hui_group_id),
                    "hui_group_name": group.name,
                    "due_date": schedule.due_date.isoformat() if schedule.due_date else None,
                    "reference_code": existing_payment.reference_code if existing_payment else None,
                    "payment_status": existing_payment.payment_status.value if existing_payment else "not_created",
                    "membership_id": str(membership.id),
                    "schedule_id": str(schedule.id),
                    "slot_count": slot_count
                })
        
        return result
    
    except Exception as e:
        logger.error(f"Error getting pending payments: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/overdue-payments")
async def get_overdue_payments(
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Danh sách chi tiết các khoản thanh toán quá hạn"""
    try:
        today_start, today_end = get_vietnam_today_range()
        today_start_naive = today_start
        
        groups = db.query(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiGroup.is_active
        ).all()
        group_ids = [g.id for g in groups]
        
        if not group_ids:
            return []
        
        overdue_payments = db.query(Payment).filter(
            Payment.hui_group_id.in_(group_ids),
            Payment.due_date < today_start_naive,
            Payment.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE])
        ).order_by(Payment.due_date.asc()).all()
        
        result = []
        for payment in overdue_payments:
            membership = db.query(HuiMembership).filter(
                HuiMembership.id == payment.membership_id
            ).first()
            
            if not membership:
                continue
            
            member = db.query(Member).filter(Member.id == membership.member_id).first()
            if not member:
                continue
            
            hui_group = db.query(HuiGroup).filter(HuiGroup.id == payment.hui_group_id).first()
            if not hui_group:
                continue
            
            schedule = None
            cycle_number = None
            if payment.schedule_id:
                schedule = db.query(HuiSchedule).filter(HuiSchedule.id == payment.schedule_id).first()
                if schedule:
                    cycle_number = schedule.cycle_number
            
            days_overdue = 0
            if payment.due_date:
                due_date = payment.due_date
                if due_date.tzinfo is None:
                    days_overdue = (today_start_naive - due_date).days
                else:
                    days_overdue = (today_start - due_date).days
            
            result.append({
                "payment_id": payment.id,
                "schedule_id": payment.schedule_id,
                "cycle_number": cycle_number,
                "membership_id": payment.membership_id,
                "member_id": member.id,
                "member_name": member.name,
                "member_phone": member.phone,
                "hui_group_id": hui_group.id,
                "hui_group_name": hui_group.name,
                "amount": payment.amount,
                "due_date": payment.due_date.isoformat() if payment.due_date else None,
                "days_overdue": max(0, days_overdue),
                "slot_count": membership.slot_count or 1,
                "credit_score": membership.credit_score,
                "total_late_count": membership.total_late_count
            })
        
        return result
    
    except Exception as e:
        logger.error(f"Error getting overdue payments: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    period: str = "today",
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Tổng quan dashboard theo khoảng thời gian"""
    try:
        today_start, today_end = get_vietnam_today_range()
        week_start = get_vietnam_week_start()
        month_start = get_vietnam_month_start()
        
        if period == "week":
            period_start = week_start
            period_end = today_end
        elif period == "month":
            period_start = month_start
            period_end = today_end
        else:
            period_start = today_start
            period_end = today_end
        
        groups = db.query(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiGroup.is_active
        ).all()
        group_ids = [g.id for g in groups]
        group_map = {str(g.id): g for g in groups}
        
        if not group_ids:
            return DashboardSummary()
        
        schedules_in_period = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id.in_(group_ids),
            HuiSchedule.due_date >= period_start,
            HuiSchedule.due_date < period_end
        ).all()
        
        schedule_ids = [s.id for s in schedules_in_period]
        
        schedule_pot_receivers = {
            str(s.id): str(s.receiver_membership_id) if s.receiver_membership_id else None
            for s in schedules_in_period
        }
        
        total_due = 0
        total_members_should_pay = 0
        
        for schedule in schedules_in_period:
            group = group_map.get(str(schedule.hui_group_id))
            if not group:
                continue
            
            memberships = db.query(HuiMembership).filter(
                HuiMembership.hui_group_id == schedule.hui_group_id,
                HuiMembership.is_active == True
            ).all()
            
            pot_receiver_id = str(schedule.receiver_membership_id) if schedule.receiver_membership_id else None
            
            for m in memberships:
                if pot_receiver_id and str(m.id) == pot_receiver_id:
                    continue
                # Tính tiền đúng theo logic hụi sống
                amount = calculate_member_payment_amount(group, m, schedule.cycle_number)
                total_due += amount
                total_members_should_pay += (m.slot_count or 1)
        
        all_payments_in_period = []
        if schedule_ids:
            all_payments_in_period = db.query(Payment).filter(
                Payment.schedule_id.in_(schedule_ids)
            ).all()
        
        payments_in_period = []
        for p in all_payments_in_period:
            pot_receiver_id = schedule_pot_receivers.get(str(p.schedule_id))
            if pot_receiver_id and str(p.membership_id) == pot_receiver_id:
                continue
            payments_in_period.append(p)
        
        total_collected = sum(p.amount for p in payments_in_period if p.payment_status == PaymentStatus.VERIFIED)
        total_pending = total_due - total_collected
        members_paid = len([p for p in payments_in_period if p.payment_status == PaymentStatus.VERIFIED])
        members_pending = total_members_should_pay - members_paid
        
        all_overdue_payments = db.query(Payment).filter(
            Payment.hui_group_id.in_(group_ids),
            Payment.due_date < today_start,
            Payment.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE])
        ).all()
        
        overdue_schedule_ids = list(set(p.schedule_id for p in all_overdue_payments))
        overdue_schedules = db.query(HuiSchedule).filter(
            HuiSchedule.id.in_(overdue_schedule_ids)
        ).all() if overdue_schedule_ids else []
        
        overdue_pot_receivers = {
            str(s.id): str(s.receiver_membership_id) if s.receiver_membership_id else None
            for s in overdue_schedules
        }
        
        overdue_payments = []
        for p in all_overdue_payments:
            pot_receiver_id = overdue_pot_receivers.get(str(p.schedule_id))
            if pot_receiver_id and str(p.membership_id) == pot_receiver_id:
                continue
            overdue_payments.append(p)
        
        total_overdue = sum(p.amount for p in overdue_payments)
        members_overdue = len(set(p.membership_id for p in overdue_payments))
        
        cycles_distribution = len([s for s in schedules_in_period if s.is_completed])
        total_distribution = sum(s.distribution_amount or 0 for s in schedules_in_period if s.is_completed)
        
        total_active_members = db.query(func.count(HuiMembership.id)).filter(
            HuiMembership.hui_group_id.in_(group_ids),
            HuiMembership.is_active
        ).scalar()
        
        profit_expected_in_period = sum(s.owner_fee or 0 for s in schedules_in_period)
        profit_collected_in_period = sum(s.owner_fee or 0 for s in schedules_in_period if s.is_completed)
        
        schedules_today = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id.in_(group_ids),
            HuiSchedule.due_date >= today_start,
            HuiSchedule.due_date < today_end
        ).all()
        profit_today = sum(s.owner_fee or 0 for s in schedules_today if s.is_completed)
        profit_today_expected = sum(s.owner_fee or 0 for s in schedules_today)
        completed_today = len([s for s in schedules_today if s.is_completed])
        total_today = len(schedules_today)
        
        schedules_week = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id.in_(group_ids),
            HuiSchedule.due_date >= week_start,
            HuiSchedule.due_date < today_end
        ).all()
        profit_this_week = sum(s.owner_fee or 0 for s in schedules_week if s.is_completed)
        profit_week_expected = sum(s.owner_fee or 0 for s in schedules_week)
        
        schedules_month = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id.in_(group_ids),
            HuiSchedule.due_date >= month_start,
            HuiSchedule.due_date < today_end
        ).all()
        profit_this_month = sum(s.owner_fee or 0 for s in schedules_month if s.is_completed)
        profit_month_expected = sum(s.owner_fee or 0 for s in schedules_month)
        
        profit_total = db.query(func.sum(HuiSchedule.owner_fee)).filter(
            HuiSchedule.hui_group_id.in_(group_ids),
            HuiSchedule.is_completed == True
        ).scalar() or 0
        
        profit_projected = 0
        for group in groups:
            total_pot = group.amount_per_cycle * group.total_members
            if group.fee_type == 'percentage':
                fee_per_cycle = total_pot * (group.fee_value / 100)
            else:
                fee_per_cycle = group.fee_value
            profit_projected += fee_per_cycle * group.total_cycles
        
        thirty_days_later = today_start + timedelta(days=30)
        groups_ending_soon = 0
        for group in groups:
            remaining_cycles = group.total_cycles - group.current_cycle
            if group.cycle_type.value == 'daily':
                days_remaining = remaining_cycles * group.cycle_interval
            elif group.cycle_type.value == 'weekly':
                days_remaining = remaining_cycles * group.cycle_interval * 7
            else:
                days_remaining = remaining_cycles * group.cycle_interval * 30
            
            if days_remaining <= 30:
                groups_ending_soon += 1
        
        if groups:
            total_progress = sum((g.current_cycle / g.total_cycles) * 100 for g in groups)
            average_progress = total_progress / len(groups)
        else:
            average_progress = 0
        
        total_pot_value = sum(g.amount_per_cycle * g.total_members * g.total_cycles for g in groups)
        
        return DashboardSummary(
            total_due_today=total_due,
            total_collected_today=total_collected,
            total_pending_today=total_pending,
            members_paid_today=members_paid,
            members_pending_today=members_pending,
            total_overdue=total_overdue,
            members_overdue=members_overdue,
            cycles_distribution_today=cycles_distribution,
            total_distribution_today=total_distribution,
            total_active_groups=len(groups),
            total_active_members=total_active_members or 0,
            total_revenue_month=profit_expected_in_period,
            profit_today=profit_today,
            profit_today_expected=profit_today_expected,
            profit_today_progress=completed_today,
            profit_today_total=total_today,
            profit_this_week=profit_this_week,
            profit_week_expected=profit_week_expected,
            profit_this_month=profit_this_month,
            profit_month_expected=profit_month_expected,
            profit_total=profit_total,
            profit_projected=profit_projected,
            groups_ending_soon=groups_ending_soon,
            average_progress=round(average_progress, 1),
            total_pot_value=total_pot_value
        )
    
    except Exception as e:
        logger.error(f"Error getting dashboard summary: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
