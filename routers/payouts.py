"""
Payouts Router - Payout management endpoints
Quản lý người nhận tiền hụi
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel as PydanticBaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import json

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, AuditLog,
    require_role, get_vietnam_now, get_vietnam_today_range,
    calculate_owner_fee, safe_json_dumps, logger
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/payouts", tags=["Payouts"])


@router.get("/by-date")
async def get_payouts_by_date(
    date: Optional[str] = None,  # Format: YYYY-MM-DD
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy danh sách người nhận tiền trong ngày"""
    try:
        if date:
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d")
                target_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                target_end = target_start + timedelta(days=1)
            except:
                raise HTTPException(status_code=400, detail="Định dạng ngày không hợp lệ. Dùng YYYY-MM-DD")
        else:
            target_start, target_end = get_vietnam_today_range()
        
        groups = db.query(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiGroup.is_active == True
        ).all()
        group_ids = [g.id for g in groups]
        group_map = {str(g.id): g for g in groups}
        
        if not group_ids:
            return {
                "date": target_start.strftime("%Y-%m-%d"),
                "total_receivers": 0, "total_amount": 0, "receivers": []
            }
        
        schedules = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id.in_(group_ids),
            HuiSchedule.due_date >= target_start,
            HuiSchedule.due_date < target_end,
            HuiSchedule.receiver_membership_id.isnot(None)
        ).all()
        
        if not schedules:
            return {
                "date": target_start.strftime("%Y-%m-%d"),
                "total_receivers": 0, "total_amount": 0, "receivers": []
            }
        
        member_payouts = {}
        
        for schedule in schedules:
            membership = db.query(HuiMembership).filter(
                HuiMembership.id == schedule.receiver_membership_id
            ).first()
            
            if not membership:
                continue
            
            member = db.query(Member).filter(Member.id == membership.member_id).first()
            if not member:
                continue
            
            group = group_map.get(str(schedule.hui_group_id))
            if not group:
                continue
            
            total_slots = db.query(func.sum(HuiMembership.slot_count)).filter(
                HuiMembership.hui_group_id == group.id,
                HuiMembership.is_active == True
            ).scalar() or 0
            total_slots = int(total_slots)
            
            receiver_slots = membership.slot_count or 1
            paying_slots = max(0, total_slots - receiver_slots)
            
            total_collection = float(group.amount_per_cycle) * paying_slots
            owner_fee = calculate_owner_fee(total_collection, group.fee_type, group.fee_value) if total_collection > 0 else 0
            payout_amount = total_collection - owner_fee
            
            member_id = str(member.id)
            if member_id not in member_payouts:
                member_payouts[member_id] = {
                    "member_id": member_id,
                    "member_name": member.name,
                    "member_phone": member.phone or "",
                    "telegram_linked": bool(member.telegram_chat_id),
                    "total_amount": 0,
                    "payouts": [],
                    "all_completed": True
                }
            
            payout_detail = {
                "schedule_id": str(schedule.id),
                "hui_group_id": str(group.id),
                "hui_group_name": group.name,
                "cycle_number": schedule.cycle_number,
                "total_cycles": group.total_cycles,
                "membership_id": str(membership.id),
                "slot_count": receiver_slots,
                "total_collection": total_collection,
                "owner_fee": owner_fee,
                "payout_amount": payout_amount,
                "is_completed": schedule.is_completed,
                "due_date": schedule.due_date.isoformat() if schedule.due_date else None
            }
            
            member_payouts[member_id]["payouts"].append(payout_detail)
            member_payouts[member_id]["total_amount"] += payout_amount
            
            if not schedule.is_completed:
                member_payouts[member_id]["all_completed"] = False
        
        receivers = sorted(
            list(member_payouts.values()),
            key=lambda x: x["total_amount"],
            reverse=True
        )
        
        total_amount = sum(r["total_amount"] for r in receivers)
        total_completed = len([r for r in receivers if r["all_completed"]])
        total_pending = len(receivers) - total_completed
        
        return {
            "date": target_start.strftime("%Y-%m-%d"),
            "total_receivers": len(receivers),
            "total_amount": total_amount,
            "completed_count": total_completed,
            "pending_count": total_pending,
            "receivers": receivers
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payouts by date: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CompletePayoutRequest(PydanticBaseModel):
    member_id: str
    schedule_ids: Optional[List[str]] = None
    note: Optional[str] = ""


@router.post("/complete")
async def complete_payout(
    request: CompletePayoutRequest,
    date: Optional[str] = None,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Hoàn thành chi tiền cho người nhận"""
    try:
        if date:
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d")
                target_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                target_end = target_start + timedelta(days=1)
            except:
                raise HTTPException(status_code=400, detail="Định dạng ngày không hợp lệ")
        else:
            target_start, target_end = get_vietnam_today_range()
        
        member = db.query(Member).filter(Member.id == request.member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        memberships = db.query(HuiMembership).filter(
            HuiMembership.member_id == request.member_id,
            HuiMembership.is_active == True
        ).all()
        membership_ids = [str(m.id) for m in memberships]
        
        if not membership_ids:
            raise HTTPException(status_code=400, detail="Thành viên không có membership nào")
        
        query = db.query(HuiSchedule).join(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiSchedule.receiver_membership_id.in_(membership_ids),
            HuiSchedule.due_date >= target_start,
            HuiSchedule.due_date < target_end
        )
        
        if request.schedule_ids:
            query = query.filter(HuiSchedule.id.in_(request.schedule_ids))
        
        schedules = query.all()
        
        if not schedules:
            raise HTTPException(status_code=404, detail="Không tìm thấy kỳ nào cần hoàn thành")
        
        completed_schedules = []
        total_payout = 0
        
        for schedule in schedules:
            if schedule.is_completed:
                continue
            
            group = db.query(HuiGroup).filter(HuiGroup.id == schedule.hui_group_id).first()
            if not group:
                continue
            
            receiver_membership = db.query(HuiMembership).filter(
                HuiMembership.id == schedule.receiver_membership_id
            ).first()
            
            total_slots = db.query(func.sum(HuiMembership.slot_count)).filter(
                HuiMembership.hui_group_id == group.id,
                HuiMembership.is_active == True
            ).scalar() or 0
            total_slots = int(total_slots)
            
            receiver_slots = receiver_membership.slot_count if receiver_membership else 1
            paying_slots = max(0, total_slots - receiver_slots)
            
            total_collection = float(group.amount_per_cycle) * paying_slots
            owner_fee = calculate_owner_fee(total_collection, group.fee_type, group.fee_value) if total_collection > 0 else 0
            distribution_amount = total_collection - owner_fee
            
            schedule.total_collection = total_collection
            schedule.owner_fee = owner_fee
            schedule.distribution_amount = distribution_amount
            schedule.is_completed = True
            schedule.completed_at = get_vietnam_now()
            
            if receiver_membership:
                try:
                    received_cycles = json.loads(receiver_membership.received_cycles or "[]")
                except:
                    received_cycles = []
                
                if schedule.cycle_number not in received_cycles:
                    received_cycles.append(schedule.cycle_number)
                
                receiver_membership.received_count = len(received_cycles)
                receiver_membership.received_cycles = json.dumps(received_cycles)
                receiver_membership.received_cycle = schedule.cycle_number
                
                if receiver_membership.received_count >= (receiver_membership.slot_count or 1):
                    receiver_membership.has_received = True
            
            if group.current_cycle == schedule.cycle_number and schedule.cycle_number < group.total_cycles:
                group.current_cycle = schedule.cycle_number + 1
            
            completed_schedules.append({
                "schedule_id": str(schedule.id),
                "hui_group_name": group.name,
                "cycle_number": schedule.cycle_number,
                "distribution_amount": distribution_amount
            })
            total_payout += distribution_amount
        
        if not completed_schedules:
            raise HTTPException(status_code=400, detail="Tất cả các kỳ đã được hoàn thành trước đó")
        
        db.commit()
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="complete_payout",
            entity_type="payout",
            entity_id=request.member_id,
            new_value=safe_json_dumps({
                "member_id": request.member_id,
                "member_name": member.name,
                "date": target_start.strftime("%Y-%m-%d"),
                "schedules_completed": len(completed_schedules),
                "total_payout": total_payout,
                "note": request.note
            })
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Completed payout for {member.name}: {len(completed_schedules)} schedules, total {total_payout:,.0f}đ")
        
        return {
            "success": True,
            "message": f"Đã hoàn thành chi tiền cho {member.name}",
            "member_name": member.name,
            "schedules_completed": len(completed_schedules),
            "total_payout": total_payout,
            "details": completed_schedules
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing payout: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
