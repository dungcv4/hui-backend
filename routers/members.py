"""
Members Router - Member management endpoints (tách riêng khỏi Users)
CRUD operations cho thành viên hụi
"""
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from passlib.context import CryptContext

from database import get_db
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from models import Member, User, UserRole, HuiMembership, HuiGroup, Payment, PaymentStatus, HuiSchedule, AuditLog
from utils import calculate_member_payment_amount

_VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _get_vietnam_today_range():
    """Trả naive datetime start/end của hôm nay theo giờ VN (để so sánh với DB)"""
    now = datetime.now(_VIETNAM_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end = start.replace(hour=23, minute=59, second=59)
    return start, end
from schemas import MemberCreate, MemberUpdate, MemberResponse
from auth import get_current_user
import logging
import json

logger = logging.getLogger(__name__)

# Top-level — not re-created on every request
pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# Generic error message for 500s
_INTERNAL_ERROR = "Lỗi hệ thống, vui lòng thử lại sau"


def safe_json_dumps(obj):
    """Safe JSON dumps with default handler"""
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


def _check_owner_or_staff(current_user: User):
    """Check if user has owner or staff role"""
    if current_user.role not in [UserRole.OWNER.value, UserRole.STAFF.value]:
        raise HTTPException(status_code=403, detail="Chỉ chủ hụi mới có quyền thực hiện thao tác này")


router = APIRouter(prefix="/members", tags=["Members"])


@router.get("", response_model=List[MemberResponse])
async def list_members(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    search: str = None
):
    """Danh sách thành viên của owner hiện tại"""
    try:
        _check_owner_or_staff(current_user)
        
        query = db.query(Member).filter(
            Member.owner_id == current_user.id,
            Member.is_active == True
        )
        
        if search:
            query = query.filter(
                (Member.name.ilike(f"%{search}%")) | 
                (Member.phone.ilike(f"%{search}%"))
            )
        
        members = query.offset(skip).limit(limit).all()
        return [MemberResponse.model_validate(m) for m in members]
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing members: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.post("", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    member_data: MemberCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Tạo thành viên mới"""
    try:
        _check_owner_or_staff(current_user)
        
        # Check duplicate phone for THIS owner
        existing = db.query(Member).filter(
            Member.phone == member_data.phone,
            Member.owner_id == current_user.id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="Số điện thoại đã tồn tại trong danh sách thành viên của bạn")
        
        member = Member(
            owner_id=current_user.id,
            phone=member_data.phone,
            name=member_data.name,
            email=member_data.email,
            cccd=member_data.cccd,
            address=member_data.address
        )
        db.add(member)
        
        # Audit log — single commit with entity
        audit_log = AuditLog(
            user_id=current_user.id,
            action="create_member",
            entity_type="member",
            entity_id=member.id,
            new_value=safe_json_dumps(member_data.model_dump())
        )
        db.add(audit_log)
        db.commit()
        db.refresh(member)
        
        logger.info(f"Member created: {member.phone} by {current_user.phone}")
        return MemberResponse.model_validate(member)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating member: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.get("/{member_id}", response_model=MemberResponse)
async def get_member(
    member_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Chi tiết thành viên"""
    try:
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        # Check ownership
        if member.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập thành viên này")
        
        return MemberResponse.model_validate(member)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting member: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.put("/{member_id}", response_model=MemberResponse)
async def update_member(
    member_id: str,
    member_data: MemberUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cập nhật thông tin thành viên (partial update — chỉ gửi field cần sửa)"""
    try:
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        # Check ownership
        if member.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền chỉnh sửa thành viên này")
        
        # Only update fields that were explicitly provided (partial update)
        update_data = member_data.model_dump(exclude_unset=True)
        
        # Check duplicate phone if phone is being changed
        if "phone" in update_data and update_data["phone"] != member.phone:
            existing = db.query(Member).filter(
                Member.phone == update_data["phone"],
                Member.owner_id == current_user.id,
                Member.id != member_id
            ).first()
            if existing:
                raise HTTPException(status_code=400, detail="Số điện thoại đã tồn tại trong danh sách của bạn")
        
        # Apply partial updates
        for field, value in update_data.items():
            setattr(member, field, value)
        
        # Audit log — single commit with entity
        audit_log = AuditLog(
            user_id=current_user.id,
            action="update_member",
            entity_type="member",
            entity_id=member.id,
            new_value=safe_json_dumps(update_data)
        )
        db.add(audit_log)
        db.commit()
        db.refresh(member)
        
        logger.info(f"Member updated: {member.phone}")
        return MemberResponse.model_validate(member)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating member: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.delete("/{member_id}")
async def delete_member(
    member_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Xóa thành viên (soft delete)"""
    try:
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        # Check ownership
        if member.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền xóa thành viên này")
        
        # Check active memberships
        active_memberships = db.query(HuiMembership).filter(
            HuiMembership.member_id == member_id,
            HuiMembership.is_active == True
        ).count()
        
        if active_memberships > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"Không thể xóa: Thành viên đang tham gia {active_memberships} dây hụi"
            )
        
        member.is_active = False
        
        # Audit log — single commit
        audit_log = AuditLog(
            user_id=current_user.id,
            action="delete_member",
            entity_type="member",
            entity_id=member.id,
            new_value=safe_json_dumps({"name": member.name, "phone": member.phone, "deleted": True})
        )
        db.add(audit_log)
        db.commit()
        
        logger.info(f"Member soft deleted: {member.phone}")
        return {"success": True, "message": "Xóa thành viên thành công"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting member: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.post("/{member_id}/set-password")
async def set_member_password(
    member_id: str,
    request_body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Chủ hụi đặt mật khẩu cho thành viên (để thành viên đăng nhập customer portal)"""
    try:
        _check_owner_or_staff(current_user)

        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")

        if member.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền thay đổi thành viên này")

        password = request_body.get("password", "")
        if len(password) < 6:
            raise HTTPException(status_code=400, detail="Mật khẩu phải có ít nhất 6 ký tự")

        member.password_hash = pwd_ctx.hash(password)
        db.commit()

        logger.info(f"Password set for member: {member.phone} by owner {current_user.phone}")
        return {"success": True, "message": f"Đã đặt mật khẩu cho {member.name}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting member password: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)


@router.get("/{member_id}/detail")
async def get_member_detail(
    member_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Chi tiết đầy đủ của thành viên (bao gồm memberships và payments)"""
    try:
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        # Check ownership
        if member.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập thành viên này")
        
        # Get all memberships
        memberships = db.query(HuiMembership).filter(
            HuiMembership.member_id == member_id
        ).all()
        
        # Batch load groups (no N+1)
        group_ids = list(set(str(m.hui_group_id) for m in memberships))
        groups = db.query(HuiGroup).filter(HuiGroup.id.in_(group_ids)).all() if group_ids else []
        group_map = {str(g.id): g for g in groups}
        
        # Batch load all payments for all memberships
        membership_ids = [m.id for m in memberships]
        all_payments = []
        if membership_ids:
            all_payments = db.query(Payment).filter(
                Payment.membership_id.in_(membership_ids)
            ).order_by(Payment.due_date.desc()).all()
        
        # Group payments by membership_id
        payments_by_ms = {}
        for p in all_payments:
            ms_id = str(p.membership_id)
            if ms_id not in payments_by_ms:
                payments_by_ms[ms_id] = []
            payments_by_ms[ms_id].append(p)
        
        membership_details = []
        total_paid = 0
        total_pending = 0
        
        for membership in memberships:
            hui_group = group_map.get(str(membership.hui_group_id))
            payments = payments_by_ms.get(str(membership.id), [])
            
            paid = sum(p.amount for p in payments if p.payment_status == PaymentStatus.VERIFIED)
            pending = sum(p.amount for p in payments if p.payment_status in [PaymentStatus.PENDING, PaymentStatus.OVERDUE])
            
            total_paid += paid
            total_pending += pending
            
            membership_details.append({
                "id": membership.id,
                "hui_group_id": membership.hui_group_id,
                "hui_group_name": hui_group.name if hui_group else "N/A",
                "payment_code": membership.payment_code,
                "slot_count": membership.slot_count or 1,
                "credit_score": membership.credit_score,
                "risk_level": membership.risk_level.value,
                "total_late_count": membership.total_late_count or 0,
                "total_late_amount": membership.total_late_amount or 0,
                "has_received": membership.has_received,
                "received_cycle": membership.received_cycle,
                "joined_at": membership.joined_at.isoformat(),
                "is_active": membership.is_active,
                "notes": membership.notes,
                "total_paid": paid,
                "total_pending": pending,
                "payments": [
                    {
                        "id": p.id,
                        "amount": p.amount,
                        "status": p.payment_status.value,
                        "due_date": p.due_date.isoformat() if p.due_date else None,
                        "paid_at": p.paid_at.isoformat() if p.paid_at else None
                    } for p in payments[:10]  # Limit to last 10 payments
                ]
            })
        
        # ---- Today summary ----
        today_start, today_end = _get_vietnam_today_range()
        today_total_due = 0
        today_total_receive = 0
        today_details = []

        for ms in memberships:
            group = group_map.get(str(ms.hui_group_id))
            if not group or not group.is_active:
                continue

            schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.due_date >= today_start,
                HuiSchedule.due_date <= today_end
            ).first()

            if not schedule:
                continue

            is_receiver = (schedule.receiver_membership_id == ms.id)
            if is_receiver:
                amount = schedule.distribution_amount or (group.amount_per_cycle * group.total_members)
                today_total_receive += amount
                today_details.append({
                    "hui_group_id": str(group.id),
                    "hui_group_name": group.name,
                    "cycle_number": schedule.cycle_number,
                    "type": "receive",
                    "amount": amount,
                    "payment_code": ms.payment_code,
                })
            else:
                amount = calculate_member_payment_amount(group, ms, schedule.cycle_number)
                # Check if already paid today
                paid = db.query(Payment).filter(
                    Payment.schedule_id == schedule.id,
                    Payment.membership_id == ms.id,
                    Payment.payment_status == PaymentStatus.VERIFIED
                ).first()
                status = "paid" if paid else "pending"
                if not paid:
                    today_total_due += amount
                today_details.append({
                    "hui_group_id": str(group.id),
                    "hui_group_name": group.name,
                    "cycle_number": schedule.cycle_number,
                    "type": "pay",
                    "amount": amount,
                    "status": status,
                    "payment_code": ms.payment_code,
                })

        # ---- Upcoming payments (next 5 cycles) ----
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        upcoming = []
        for ms in memberships:
            group = group_map.get(str(ms.hui_group_id))
            if not group or not group.is_active:
                continue
            next_schedules = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.is_completed == False,
                HuiSchedule.due_date > today_end,
                HuiSchedule.receiver_membership_id != ms.id
            ).order_by(HuiSchedule.due_date.asc()).limit(3).all()

            for s in next_schedules:
                days_until = (s.due_date - now_utc).days if s.due_date else None
                upcoming.append({
                    "hui_group_name": group.name,
                    "cycle_number": s.cycle_number,
                    "due_date": s.due_date.isoformat() if s.due_date else None,
                    "amount": calculate_member_payment_amount(group, ms, s.cycle_number),
                    "days_until": max(0, days_until) if days_until is not None else None,
                })

        upcoming.sort(key=lambda x: x.get("due_date") or "9999")
        upcoming = upcoming[:5]

        # ---- Next payout ----
        next_payout = None
        if not all(m.has_received for m in memberships):
            pending_ms = [m for m in memberships if not m.has_received]
            for ms in pending_ms:
                group = group_map.get(str(ms.hui_group_id))
                if not group:
                    continue
                recv_schedule = db.query(HuiSchedule).filter(
                    HuiSchedule.hui_group_id == group.id,
                    HuiSchedule.receiver_membership_id == ms.id,
                    HuiSchedule.is_completed == False
                ).order_by(HuiSchedule.due_date.asc()).first()
                if recv_schedule:
                    days_until = (recv_schedule.due_date - now_utc).days if recv_schedule.due_date else None
                    candidate = {
                        "hui_group_name": group.name,
                        "cycle_number": recv_schedule.cycle_number,
                        "due_date": recv_schedule.due_date.isoformat() if recv_schedule.due_date else None,
                        "estimated_amount": recv_schedule.distribution_amount or (group.amount_per_cycle * group.total_members),
                        "days_until": max(0, days_until) if days_until is not None else None,
                    }
                    if next_payout is None or (recv_schedule.due_date and recv_schedule.due_date < datetime.fromisoformat(next_payout["due_date"])):
                        next_payout = candidate

        # Calculate overall stats
        overall_late_count = sum(m.total_late_count or 0 for m in memberships)
        overall_late_amount = sum(m.total_late_amount or 0 for m in memberships)
        avg_credit_score = sum(m.credit_score or 100 for m in memberships) / len(memberships) if memberships else 100
        
        # Determine overall risk level
        if avg_credit_score < 40:
            overall_risk = "critical"
        elif avg_credit_score < 60:
            overall_risk = "high"
        elif avg_credit_score < 80:
            overall_risk = "medium"
        else:
            overall_risk = "low"
        
        return {
            "id": member.id,
            "phone": member.phone,
            "name": member.name,
            "email": member.email,
            "cccd": member.cccd,
            "address": member.address,
            "telegram_chat_id": member.telegram_chat_id,
            "is_active": member.is_active,
            "created_at": member.created_at.isoformat(),
            "total_memberships": len(memberships),
            "active_memberships": len([m for m in memberships if m.is_active]),
            "total_paid": total_paid,
            "total_pending": total_pending,
            "avg_credit_score": round(avg_credit_score),
            "overall_risk_level": overall_risk,
            "total_late_count": overall_late_count,
            "total_late_amount": overall_late_amount,
            "memberships": membership_details,
            # New fields
            "today_summary": {
                "total_due": today_total_due,
                "total_receive": today_total_receive,
                "net": today_total_receive - today_total_due,
                "details": today_details,
            },
            "upcoming_payments": upcoming,
            "next_payout": next_payout,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting member detail: {str(e)}")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR)
