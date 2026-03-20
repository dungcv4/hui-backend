"""
Schedules Router - Schedule management endpoints
Bao gồm: get schedules, assign receiver, complete cycle
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, Payment, PaymentStatus,
    HuiSchedule, AuditLog, require_role, get_current_user,
    get_vietnam_now, logger, safe_json_dumps, json, calculate_owner_fee
)
from utils import calculate_member_payment_amount

router = APIRouter(tags=["Schedules"])


@router.get("/hui-groups/{group_id}/schedules")
async def get_hui_group_schedules(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lấy lịch các kỳ của dây hụi"""
    try:
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
            
        if current_user.role == UserRole.OWNER and hui_group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        memberships = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == group_id,
            HuiMembership.is_active == True
        ).all()
        total_slots = sum(m.slot_count or 1 for m in memberships)
        
        total_collection = hui_group.amount_per_cycle * total_slots
        owner_fee = calculate_owner_fee(total_collection, hui_group.fee_type, hui_group.fee_value)
        gross_distribution = total_collection - owner_fee
        
        schedules = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group_id
        ).order_by(HuiSchedule.cycle_number).all()
        
        result = []
        for schedule in schedules:
            expected_distribution = gross_distribution
            receiver_contribution = 0
            
            if schedule.receiver_membership_id:
                receiver_membership = db.query(HuiMembership).filter(
                    HuiMembership.id == schedule.receiver_membership_id
                ).first()
                if receiver_membership:
                    receiver_slot_count = receiver_membership.slot_count or 1
                    receiver_contribution = hui_group.amount_per_cycle * receiver_slot_count
                    expected_distribution = gross_distribution - receiver_contribution
            
            schedule_dict = {
                "id": schedule.id,
                "cycle_number": schedule.cycle_number,
                "due_date": schedule.due_date.isoformat(),
                "receiver_membership_id": schedule.receiver_membership_id,
                "total_collection": schedule.total_collection if schedule.is_completed else total_collection,
                "owner_fee": schedule.owner_fee if schedule.is_completed else owner_fee,
                "distribution_amount": schedule.distribution_amount if schedule.is_completed else expected_distribution,
                "receiver_contribution": receiver_contribution,
                "is_completed": schedule.is_completed,
                "completed_at": schedule.completed_at.isoformat() if schedule.completed_at else None,
            }
            result.append(schedule_dict)
        
        return result
    
    except Exception as e:
        logger.error(f"Error getting schedules: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/hui-groups/{group_id}/schedules/{cycle_number}")
async def assign_schedule_receiver(
    group_id: str,
    cycle_number: int,
    request: Request,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Chỉ định người hốt cho kỳ"""
    try:
        body = await request.json()
        receiver_membership_id = body.get('receiver_membership_id')
        
        if not receiver_membership_id:
            raise HTTPException(status_code=400, detail="Vui lòng chọn thành viên")
        
        schedule = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group_id,
            HuiSchedule.cycle_number == cycle_number
        ).first()
        
        # Check permissions
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not hui_group:
             raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        if current_user.role == UserRole.OWNER and hui_group.owner_id != current_user.id:
             raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        if not schedule:
            raise HTTPException(status_code=404, detail="Không tìm thấy kỳ này")
        
        if schedule.is_completed:
            raise HTTPException(status_code=400, detail="Kỳ này đã hoàn thành, không thể thay đổi")
        
        membership = db.query(HuiMembership).filter(
            HuiMembership.id == receiver_membership_id
        ).first()
        
        if not membership:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        slot_count = membership.slot_count or 1
        
        assigned_count = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group_id,
            HuiSchedule.receiver_membership_id == receiver_membership_id,
            HuiSchedule.id != schedule.id
        ).count()
        
        if assigned_count >= slot_count:
            raise HTTPException(
                status_code=400,
                detail=f"Thành viên này đã được chỉ định đủ {slot_count} chân"
            )
        
        schedule.receiver_membership_id = receiver_membership_id
        db.commit()
        
        logger.info(f"Assigned cycle {cycle_number} to membership {receiver_membership_id} ({assigned_count + 1}/{slot_count})")
        
        return {"success": True, "message": "Chỉ định thành công"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error assigning receiver: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hui-groups/{group_id}/schedules/{cycle_number}/complete")
async def complete_cycle(
    group_id: str,
    cycle_number: int,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Hoàn thành kỳ và chi tiền cho người hốt"""
    try:
        schedule = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group_id,
            HuiSchedule.cycle_number == cycle_number
        ).first()
        
        if not schedule:
            raise HTTPException(status_code=404, detail="Không tìm thấy kỳ này")
        
        if schedule.is_completed:
            raise HTTPException(status_code=400, detail="Kỳ này đã hoàn thành")
        
        if not schedule.receiver_membership_id:
            raise HTTPException(status_code=400, detail="Chưa chỉ định người hốt cho kỳ này")
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
            
        if current_user.role == UserRole.OWNER and hui_group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        memberships = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == group_id,
            HuiMembership.is_active == True
        ).all()
        total_slots = sum(m.slot_count or 1 for m in memberships)
        
        payments = db.query(Payment).filter(
            Payment.schedule_id == schedule.id
        ).all()
        
        verified_count = len([p for p in payments if p.payment_status == PaymentStatus.VERIFIED])
        
        total_collection = hui_group.amount_per_cycle * total_slots
        owner_fee = calculate_owner_fee(total_collection, hui_group.fee_type, hui_group.fee_value)
        distribution_amount = total_collection - owner_fee
        
        schedule.total_collection = total_collection
        schedule.owner_fee = owner_fee
        schedule.distribution_amount = distribution_amount
        schedule.is_completed = True
        schedule.completed_at = get_vietnam_now()
        
        receiver_membership = db.query(HuiMembership).filter(
            HuiMembership.id == schedule.receiver_membership_id
        ).first()
        
        if receiver_membership:
            try:
                received_cycles = json.loads(receiver_membership.received_cycles or "[]")
            except:
                received_cycles = []
            
            if cycle_number not in received_cycles:
                received_cycles.append(cycle_number)
            
            receiver_membership.received_count = len(received_cycles)
            receiver_membership.received_cycles = json.dumps(received_cycles)
            receiver_membership.received_cycle = cycle_number
            
            if receiver_membership.received_count >= (receiver_membership.slot_count or 1):
                receiver_membership.has_received = True
        
        if hui_group.current_cycle == cycle_number and cycle_number < hui_group.total_cycles:
            hui_group.current_cycle = cycle_number + 1
        
        db.commit()
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="complete_cycle",
            entity_type="schedule",
            entity_id=schedule.id,
            new_value=safe_json_dumps({
                "cycle_number": cycle_number,
                "total_collection": total_collection,
                "owner_fee": owner_fee,
                "distribution_amount": distribution_amount,
                "receiver_membership_id": schedule.receiver_membership_id,
                "total_slots": total_slots
            })
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Cycle {cycle_number} completed for group {group_id}, distributed {distribution_amount}")
        
        # Send Telegram notification if linked
        if hui_group.telegram_group_id:
            try:
                from config import settings as app_settings
                from telegram import Bot
                
                if app_settings.telegram_bot_token:
                    receiver_name = "N/A"
                    receiver_slot_count = 1
                    if receiver_membership:
                        receiver_member = db.query(Member).filter(Member.id == receiver_membership.member_id).first()
                        if receiver_member:
                            receiver_name = receiver_member.name
                        receiver_slot_count = receiver_membership.slot_count or 1
                    
                    receiver_contribution = hui_group.amount_per_cycle * receiver_slot_count
                    net_received = distribution_amount - receiver_contribution
                    
                    if net_received >= 0:
                        net_message = f"💰 <b>Tiền {receiver_name} nhận thực:</b> {net_received:,.0f}₫"
                    else:
                        net_message = f"💸 <b>{receiver_name} đóng thêm:</b> {abs(net_received):,.0f}₫"
                    
                    completion_message = f"""
📢 <b>THÔNG BÁO CHỐT KỲ</b>

✅ <b>Đã hoàn thành kỳ {cycle_number}/{hui_group.total_cycles}</b>

📌 <b>Dây hụi:</b> {hui_group.name}
👤 <b>Người hốt:</b> {receiver_name}

💵 <b>Chi tiết tài chính:</b>
  • Tổng thu từ {total_slots} chân: {total_collection:,.0f}₫
  • Phí chủ hụi: {owner_fee:,.0f}₫

{net_message}

⏰ Thời gian chốt: {datetime.now().strftime('%H:%M %d/%m/%Y')}
"""
                    
                    bot = Bot(token=app_settings.telegram_bot_token)
                    sent_message = await bot.send_message(
                        chat_id=hui_group.telegram_group_id,
                        text=completion_message,
                        parse_mode="HTML"
                    )
                    
                    try:
                        await bot.pin_chat_message(
                            chat_id=hui_group.telegram_group_id,
                            message_id=sent_message.message_id,
                            disable_notification=True
                        )
                    except Exception as pin_error:
                        logger.warning(f"Could not pin message: {pin_error}")
                    
            except Exception as tg_error:
                logger.warning(f"Could not send Telegram notification: {tg_error}")
        
        return {
            "success": True,
            "message": "Hoàn thành kỳ thành công",
            "data": {
                "cycle_number": cycle_number,
                "total_collection": total_collection,
                "owner_fee": owner_fee,
                "distribution_amount": distribution_amount,
                "verified_payments": verified_count,
                "total_slots": total_slots
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing cycle: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
