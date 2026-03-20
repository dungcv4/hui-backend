"""
HuiGroups Router - Hui Groups management endpoints
CRUD + members + bank info + transfer slots
"""
from fastapi import APIRouter, HTTPException, Depends, status, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import timedelta
from pydantic import BaseModel as PydanticBaseModel

from routers.dependencies import (
    get_db, User, UserRole, Member, UserResponse, HuiGroup, HuiMembership, 
    HuiGroupCreate, HuiGroupResponse, HuiGroupDetail, HuiSchedule,
    Payment, PaymentStatus, AuditLog, require_role, get_current_user,
    get_vietnam_now, get_vietnam_today_range, logger, safe_json_dumps,
    generate_payment_code, pwd_context
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/hui-groups", tags=["Hui Groups"])


class BankInfoUpdate(PydanticBaseModel):
    bank_name: str
    bank_account_number: str
    bank_account_name: str


@router.get("", response_model=List[HuiGroupResponse])
async def list_hui_groups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    filter_period: Optional[str] = None,
    search: Optional[str] = None
):
    """Danh sách dây hụi với filter theo ngày đến hạn thanh toán"""
    try:
        today_start, today_end = get_vietnam_today_range()
        today_naive = today_start
        
        if current_user.role == UserRole.OWNER or current_user.role == UserRole.STAFF:
            query = db.query(HuiGroup).filter(
                HuiGroup.owner_id == current_user.id,
                HuiGroup.is_active == True
            )
        else:
            memberships = db.query(HuiMembership).filter(
                HuiMembership.member_id == current_user.id,
                HuiMembership.is_active == True
            ).all()
            group_ids = [m.hui_group_id for m in memberships]
            query = db.query(HuiGroup).filter(
                HuiGroup.id.in_(group_ids),
                HuiGroup.is_active == True
            )
        
        if search:
            query = query.filter(HuiGroup.name.ilike(f"%{search}%"))
        
        if filter_period:
            if filter_period == "today":
                schedule_ids = db.query(HuiSchedule.hui_group_id).filter(
                    HuiSchedule.due_date >= today_naive,
                    HuiSchedule.due_date < today_end,
                    HuiSchedule.is_completed == False
                ).distinct().all()
                group_ids_with_due = [s[0] for s in schedule_ids]
                query = query.filter(HuiGroup.id.in_(group_ids_with_due))
                
            elif filter_period == "week":
                week_end = today_naive + timedelta(days=7)
                schedule_ids = db.query(HuiSchedule.hui_group_id).filter(
                    HuiSchedule.due_date >= today_naive,
                    HuiSchedule.due_date < week_end,
                    HuiSchedule.is_completed == False
                ).distinct().all()
                group_ids_with_due = [s[0] for s in schedule_ids]
                query = query.filter(HuiGroup.id.in_(group_ids_with_due))
                
            elif filter_period == "month":
                month_end = today_naive + timedelta(days=30)
                schedule_ids = db.query(HuiSchedule.hui_group_id).filter(
                    HuiSchedule.due_date >= today_naive,
                    HuiSchedule.due_date < month_end,
                    HuiSchedule.is_completed == False
                ).distinct().all()
                group_ids_with_due = [s[0] for s in schedule_ids]
                query = query.filter(HuiGroup.id.in_(group_ids_with_due))
        
        groups = query.order_by(HuiGroup.created_at.desc()).offset(skip).limit(limit).all()
        
        result = []
        for g in groups:
            group_dict = HuiGroupResponse.model_validate(g).model_dump()
            
            next_schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == g.id,
                HuiSchedule.is_completed == False
            ).order_by(HuiSchedule.due_date.asc()).first()
            
            if next_schedule:
                group_dict["next_due_date"] = next_schedule.due_date.isoformat() if next_schedule.due_date else None
                group_dict["next_cycle"] = next_schedule.cycle_number
                
                total_slots = db.query(func.sum(HuiMembership.slot_count)).filter(
                    HuiMembership.hui_group_id == g.id,
                    HuiMembership.is_active == True
                ).scalar() or 0
                amount_per_cycle = float(g.amount_per_cycle) if g.amount_per_cycle else 0
                group_dict["next_cycle_amount"] = amount_per_cycle * int(total_slots)
                
                paid_count = db.query(func.count(Payment.id)).filter(
                    Payment.schedule_id == next_schedule.id,
                    Payment.payment_status == PaymentStatus.VERIFIED
                ).scalar() or 0
                
                pending_count = db.query(func.count(Payment.id)).filter(
                    Payment.schedule_id == next_schedule.id,
                    Payment.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE])
                ).scalar() or 0
                
                group_dict["paid_count"] = paid_count
                group_dict["pending_count"] = pending_count
            else:
                group_dict["next_due_date"] = None
                group_dict["next_cycle"] = None
                group_dict["next_cycle_amount"] = 0
                group_dict["paid_count"] = 0
                group_dict["pending_count"] = 0
            
            member_count = db.query(func.count(HuiMembership.id)).filter(
                HuiMembership.hui_group_id == g.id,
                HuiMembership.is_active == True
            ).scalar()
            group_dict["member_count"] = member_count or 0
            
            result.append(group_dict)
        
        return result
    
    except Exception as e:
        logger.error(f"Error listing hui groups: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=HuiGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_hui_group(
    hui_data: HuiGroupCreate,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Tạo dây hụi mới"""
    try:
        from utils import calculate_next_due_date
        
        start_date = hui_data.start_date
        
        hui_group = HuiGroup(
            name=hui_data.name,
            owner_id=current_user.id,
            amount_per_cycle=hui_data.amount_per_cycle,
            total_members=hui_data.total_members,
            cycle_type=hui_data.cycle_type,
            cycle_interval=hui_data.cycle_interval,
            total_cycles=hui_data.total_cycles,
            current_cycle=1,
            fee_type=hui_data.fee_type,
            fee_value=hui_data.fee_value,
            hui_method=hui_data.hui_method,
            bank_account_number=hui_data.bank_account_number,
            bank_name=hui_data.bank_name,
            bank_account_name=hui_data.bank_account_name,
            start_date=start_date,
            is_active=True
        )
        db.add(hui_group)
        db.commit()
        db.refresh(hui_group)
        
        expected_total = hui_data.amount_per_cycle * hui_data.total_members
        if hui_data.fee_type == 'percentage':
            expected_owner_fee = expected_total * (hui_data.fee_value / 100)
        else:
            expected_owner_fee = hui_data.fee_value
        
        for cycle in range(1, hui_data.total_cycles + 1):
            due_date = calculate_next_due_date(hui_data.start_date, hui_data.cycle_type, cycle, hui_data.cycle_interval)
            schedule = HuiSchedule(
                hui_group_id=hui_group.id,
                cycle_number=cycle,
                due_date=due_date,
                receiver_membership_id=None,
                total_collection=0,
                owner_fee=expected_owner_fee,
                distribution_amount=0,
                is_completed=False,
                completed_at=None
            )
            db.add(schedule)
        
        db.commit()
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="create_hui_group",
            entity_type="hui_group",
            entity_id=hui_group.id,
            new_value=safe_json_dumps({"name": hui_group.name, "total_members": hui_group.total_members})
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Hui group created: {hui_group.name} by {current_user.phone}")
        return HuiGroupResponse.model_validate(hui_group)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating hui group: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{group_id}", response_model=HuiGroupDetail)
async def get_hui_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Chi tiết dây hụi"""
    try:
        group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
            
        if current_user.role == UserRole.OWNER and group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        total_members_joined = db.query(func.count(HuiMembership.id)).filter(
            HuiMembership.hui_group_id == group_id,
            HuiMembership.is_active == True
        ).scalar()
        
        total_collected = db.query(func.sum(Payment.amount)).filter(
            Payment.hui_group_id == group_id,
            Payment.payment_status == PaymentStatus.VERIFIED
        ).scalar() or 0
        
        owner = db.query(User).filter(User.id == group.owner_id).first()
        
        response = HuiGroupDetail(
            **HuiGroupResponse.model_validate(group).model_dump(),
            owner=UserResponse.model_validate(owner),
            total_members_joined=total_members_joined,
            total_collected=total_collected,
            total_distributed=0
        )
        
        return response
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting hui group: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{group_id}", response_model=HuiGroupResponse)
async def update_hui_group(
    group_id: str,
    hui_data: HuiGroupCreate,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Cập nhật thông tin dây hụi"""
    try:
        group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")

        if current_user.role == UserRole.OWNER and group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        completed_cycles = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group_id,
            HuiSchedule.is_completed == True
        ).count()
        
        if completed_cycles > 0:
            group.name = hui_data.name
            group.bank_account_number = hui_data.bank_account_number
            group.bank_name = hui_data.bank_name
            group.bank_account_name = hui_data.bank_account_name
        else:
            group.name = hui_data.name
            group.amount_per_cycle = hui_data.amount_per_cycle
            group.total_members = hui_data.total_members
            group.cycle_type = hui_data.cycle_type
            group.cycle_interval = hui_data.cycle_interval
            group.total_cycles = hui_data.total_cycles
            group.fee_type = hui_data.fee_type
            group.fee_value = hui_data.fee_value
            group.hui_method = hui_data.hui_method
            group.bank_account_number = hui_data.bank_account_number
            group.bank_name = hui_data.bank_name
            group.bank_account_name = hui_data.bank_account_name
            group.start_date = hui_data.start_date
        
        db.commit()
        db.refresh(group)
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="update_hui_group",
            entity_type="hui_group",
            entity_id=group.id,
            new_value=safe_json_dumps({"name": group.name})
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Hui group updated: {group.name}")
        return HuiGroupResponse.model_validate(group)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating hui group: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{group_id}/bank-info")
async def update_hui_group_bank_info(
    group_id: str,
    bank_data: BankInfoUpdate,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Cập nhật thông tin ngân hàng cho dây hụi"""
    try:
        group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
            
        if current_user.role == UserRole.OWNER and group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        group.bank_name = bank_data.bank_name
        group.bank_account_number = bank_data.bank_account_number
        group.bank_account_name = bank_data.bank_account_name
        
        db.commit()
        
        logger.info(f"Bank info updated for hui group {group.name}")
        
        return {
            "success": True,
            "message": "Cập nhật thông tin ngân hàng thành công",
            "bank_info": {
                "bank_name": group.bank_name,
                "bank_account_number": group.bank_account_number,
                "bank_account_name": group.bank_account_name
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating bank info: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{group_id}")
async def delete_hui_group(
    group_id: str,
    current_user: User = Depends(require_role([UserRole.OWNER])),
    db: Session = Depends(get_db)
):
    """Xóa dây hụi (soft delete)"""
    try:
        group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
            
        if group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        completed_cycles = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group_id,
            HuiSchedule.is_completed == True
        ).count()
        
        if completed_cycles > 0:
            raise HTTPException(status_code=400, detail=f"Không thể xóa: Dây hụi đã có {completed_cycles} kỳ hoàn thành")
        
        group.is_active = False
        db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == group_id
        ).update({"is_active": False})
        
        db.commit()
        
        audit_log = AuditLog(
            user_id=current_user.id,
            action="delete_hui_group",
            entity_type="hui_group",
            entity_id=group.id,
            new_value=safe_json_dumps({"name": group.name, "deleted": True})
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Hui group soft deleted: {group.name}")
        return {"success": True, "message": "Xóa dây hụi thành công"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting hui group: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{group_id}/members")
async def get_hui_group_members(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Danh sách thành viên trong dây hụi"""
    try:
        # Check permissions
        if current_user.role == UserRole.OWNER:
            group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
            if not group or group.owner_id != current_user.id:
                raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")

        memberships = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == group_id,
            HuiMembership.is_active == True
        ).all()
        
        result = []
        for membership in memberships:
            member = db.query(Member).filter(Member.id == membership.member_id).first()
            membership_dict = {
                "id": membership.id,
                "hui_group_id": membership.hui_group_id,
                "member_id": membership.member_id,
                "slot_count": membership.slot_count or 1,
                "payment_code": membership.payment_code,
                "credit_score": membership.credit_score,
                "risk_level": membership.risk_level.value,
                "total_late_count": membership.total_late_count,
                "total_late_amount": membership.total_late_amount,
                "has_received": membership.has_received,
                "received_count": membership.received_count or 0,
                "received_cycles": membership.received_cycles,
                "received_cycle": membership.received_cycle,
                "joined_at": membership.joined_at.isoformat(),
                "cccd": member.cccd if member else None,
                "address": member.address if member else None,
                "rebate_percentage": membership.rebate_percentage,
                "guarantor_name": membership.guarantor_name,
                "guarantor_phone": membership.guarantor_phone,
                "notes": membership.notes,
                "tags": membership.tags,
                "member": {
                    "id": member.id,
                    "phone": member.phone,
                    "name": member.name,
                    "email": member.email,
                    "is_active": member.is_active,
                    "created_at": member.created_at.isoformat()
                } if member else None
            }
            result.append(membership_dict)
        
        return result
    
    except Exception as e:
        logger.error(f"Error listing members: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{group_id}/transfer-slots")
async def transfer_slots(
    group_id: str,
    transfer_data: dict = Body(...),
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Chuyển nhượng chân hụi từ người này sang người khác"""
    try:
        from_membership_id = transfer_data.get("from_membership_id")
        slots_to_transfer = transfer_data.get("slots_to_transfer", 1)
        transfer_type = transfer_data.get("transfer_type", "existing")
        to_membership_id = transfer_data.get("to_membership_id")
        new_member_data = transfer_data.get("new_member_data")
        
        from_membership = db.query(HuiMembership).filter(
            HuiMembership.id == from_membership_id,
            HuiMembership.hui_group_id == group_id
        ).first()
        
        if not from_membership:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên nguồn")
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
            
        if current_user.role == UserRole.OWNER and hui_group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        received_count = db.query(func.count(HuiSchedule.id)).filter(
            HuiSchedule.hui_group_id == group_id,
            HuiSchedule.receiver_membership_id == from_membership_id,
            HuiSchedule.is_completed == True
        ).scalar() or 0
        
        total_slots = from_membership.slot_count or 1
        transferable_slots = total_slots - received_count
        
        if slots_to_transfer < 1 or slots_to_transfer > transferable_slots:
            raise HTTPException(
                status_code=400, 
                detail=f"Chỉ có thể chuyển từ 1 đến {transferable_slots} chân"
            )
        
        from_member = db.query(Member).filter(Member.id == from_membership.member_id).first()
        from_member_name = from_member.name if from_member else "Unknown"
        
        to_membership = None
        to_member_name = ""
        
        if transfer_type == "existing":
            if not to_membership_id:
                raise HTTPException(status_code=400, detail="Vui lòng chọn người nhận")
            
            to_membership = db.query(HuiMembership).filter(
                HuiMembership.id == to_membership_id,
                HuiMembership.hui_group_id == group_id
            ).first()
            
            if not to_membership:
                raise HTTPException(status_code=404, detail="Không tìm thấy thành viên nhận")
            
            to_membership.slot_count = (to_membership.slot_count or 1) + slots_to_transfer
            to_member = db.query(Member).filter(Member.id == to_membership.member_id).first()
            to_member_name = to_member.name if to_member else "Unknown"
            
        else:
            if not new_member_data or not new_member_data.get("phone") or not new_member_data.get("name"):
                raise HTTPException(status_code=400, detail="Vui lòng nhập đầy đủ thông tin người mới")
            
            # Check if member exists for this owner
            existing_member = db.query(Member).filter(
                Member.phone == new_member_data["phone"],
                Member.owner_id == current_user.id
            ).first()
            
            if existing_member:
                existing_membership = db.query(HuiMembership).filter(
                    HuiMembership.hui_group_id == group_id,
                    HuiMembership.member_id == existing_member.id
                ).first()
                
                if existing_membership:
                    existing_membership.slot_count = (existing_membership.slot_count or 1) + slots_to_transfer
                    to_membership = existing_membership
                else:
                    to_membership = HuiMembership(
                        hui_group_id=group_id,
                        member_id=existing_member.id,
                        slot_count=slots_to_transfer,
                        payment_code=generate_payment_code(hui_group.name, existing_member.phone),
                        credit_score=100
                    )
                    db.add(to_membership)
                
                to_member_name = existing_member.name
            else:
                # Create new Member (not User)
                new_member = Member(
                    owner_id=current_user.id,
                    phone=new_member_data["phone"],
                    name=new_member_data["name"],
                    email=new_member_data.get("email")
                )
                db.add(new_member)
                db.flush()
                
                to_membership = HuiMembership(
                    hui_group_id=group_id,
                    member_id=new_member.id,
                    slot_count=slots_to_transfer,
                    payment_code=generate_payment_code(hui_group.name, new_member.phone),
                    credit_score=100
                )
                db.add(to_membership)
                to_member_name = new_member.name
        
        db.flush()
        
        from_membership.slot_count = total_slots - slots_to_transfer
        
        if from_membership.slot_count <= 0:
            from_membership.is_active = False
            from_membership.slot_count = 0
        
        future_schedules = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == group_id,
            HuiSchedule.receiver_membership_id == from_membership_id,
            HuiSchedule.is_completed == False
        ).order_by(HuiSchedule.cycle_number).all()
        
        slots_transferred = 0
        for schedule in future_schedules:
            if slots_transferred >= slots_to_transfer:
                break
            schedule.receiver_membership_id = to_membership.id
            slots_transferred += 1
            logger.info(f"Transferred cycle {schedule.cycle_number} from {from_member_name} to {to_member_name}")
        
        db.commit()
        
        logger.info(f"Slot transfer completed: {from_member_name} -> {to_member_name}, {slots_to_transfer} slots")
        
        return {
            "success": True,
            "message": f"Đã chuyển {slots_to_transfer} chân từ {from_member_name} sang {to_member_name}",
            "details": {
                "from_member": from_member_name,
                "from_remaining_slots": from_membership.slot_count,
                "to_member": to_member_name,
                "to_total_slots": to_membership.slot_count if to_membership else slots_to_transfer,
                "schedules_transferred": slots_transferred
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error transferring slots: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
