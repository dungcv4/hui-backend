"""
Payments Router - Payment management endpoints
Bao gồm: verify payment, bulk create payments, payment QR, etc.
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from pydantic import BaseModel as PydanticBaseModel

from routers.dependencies import (
    get_db, User, UserRole, HuiGroup, HuiMembership, Payment, PaymentStatus,
    PaymentMethod, HuiSchedule, require_role, get_current_user,
    get_vietnam_now, logger, payment_service, generate_reference_code
)
from utils import calculate_member_payment_amount

router = APIRouter(tags=["Payments"])


class VerifyByMembershipRequest(PydanticBaseModel):
    membership_id: str
    schedule_id: str
    note: Optional[str] = ""


@router.get("/schedules/{schedule_id}/payments")
async def get_schedule_payments(
    schedule_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lấy tất cả payments của 1 kỳ (LOẠI NGƯỜI HỐT)"""
    try:
        schedule = db.query(HuiSchedule).filter(HuiSchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Không tìm thấy kỳ")
            
        group = db.query(HuiGroup).filter(HuiGroup.id == schedule.hui_group_id).first()
        if current_user.role == UserRole.OWNER and group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        pot_receiver_membership_id = schedule.receiver_membership_id if schedule else None
        
        payments = db.query(Payment).filter(
            Payment.schedule_id == schedule_id
        ).all()
        
        result = []
        for payment in payments:
            if pot_receiver_membership_id and str(payment.membership_id) == str(pot_receiver_membership_id):
                continue
                
            payment_dict = {
                "id": payment.id,
                "hui_group_id": payment.hui_group_id,
                "membership_id": payment.membership_id,
                "amount": payment.amount,
                "payment_method": payment.payment_method.value,
                "payment_status": payment.payment_status.value,
                "reference_code": payment.reference_code,
                "bank_transaction_ref": payment.bank_transaction_ref,
                "qr_code_data": payment.qr_code_data,
                "due_date": payment.due_date.isoformat() if payment.due_date else None,
                "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
                "verified_at": payment.verified_at.isoformat() if payment.verified_at else None,
                "created_at": payment.created_at.isoformat(),
            }
            result.append(payment_dict)
        
        return result
    
    except Exception as e:
        logger.error(f"Error getting schedule payments: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/payments/{payment_id}/verify")
async def verify_payment(
    payment_id: str,
    request: Request,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Xác nhận thanh toán manual"""
    try:
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Không tìm thấy giao dịch")
            
        group = db.query(HuiGroup).filter(HuiGroup.id == payment.hui_group_id).first()
        if current_user.role == UserRole.OWNER and group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập giao dịch này")
        
        if payment.payment_status == PaymentStatus.VERIFIED:
            raise HTTPException(status_code=400, detail="Giao dịch đã được xác nhận")
        
        note = ""
        try:
            body = await request.json()
            note = body.get("note", "")
        except:
            pass
        
        was_overdue, days_late = payment_service.calculate_days_late(payment)
        
        payment.payment_status = PaymentStatus.VERIFIED
        payment.verified_at = get_vietnam_now()
        payment.paid_at = get_vietnam_now()
        if note:
            payment.notes = note
        
        if was_overdue and days_late > 0:
            membership = db.query(HuiMembership).filter(
                HuiMembership.id == payment.membership_id
            ).first()
            
            if membership:
                hui_group = db.query(HuiGroup).filter(HuiGroup.id == payment.hui_group_id).first()
                schedule = db.query(HuiSchedule).filter(HuiSchedule.id == payment.schedule_id).first() if payment.schedule_id else None
                
                payment_service.handle_late_payment(
                    db=db,
                    membership=membership,
                    payment=payment,
                    days_late=days_late,
                    hui_group=hui_group,
                    schedule=schedule,
                    note=note
                )
        
        db.commit()
        payment_service.sync_batch_after_payment_verify(db, payment, current_user.id)
        payment_service.create_audit_log(
            db=db,
            user_id=current_user.id,
            action="verify_payment",
            entity_type="payment",
            entity_id=payment.id,
            data={
                "payment_id": payment.id, 
                "verified_by": current_user.id,
                "was_overdue": was_overdue,
                "days_late": days_late,
                "note": note
            }
        )
        
        logger.info(f"Payment verified: {payment_id} by {current_user.phone}, overdue={was_overdue}, days_late={days_late}")
        
        return {
            "success": True, 
            "message": "Xác nhận thanh toán thành công",
            "was_overdue": was_overdue,
            "days_late": days_late
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying payment: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/payments/verify-by-membership")
async def verify_payment_by_membership(
    request_data: VerifyByMembershipRequest,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Xác nhận thanh toán dựa trên membership_id và schedule_id"""
    try:
        membership_id = request_data.membership_id
        schedule_id = request_data.schedule_id
        note = request_data.note or ""
        
        membership = db.query(HuiMembership).filter(HuiMembership.id == membership_id).first()
        if not membership:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        schedule = db.query(HuiSchedule).filter(HuiSchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Không tìm thấy kỳ thanh toán")
        
        group = db.query(HuiGroup).filter(HuiGroup.id == schedule.hui_group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
            
        if current_user.role == UserRole.OWNER and group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        payment = db.query(Payment).filter(
            Payment.membership_id == membership_id,
            Payment.schedule_id == schedule_id
        ).first()
        
        if not payment:
            # Tính tiền đúng theo logic hụi sống
            payment_amount = calculate_member_payment_amount(group, membership, schedule.cycle_number)
            
            reference_code = generate_reference_code(
                schedule.hui_group_id,
                membership.member_id,
                schedule.cycle_number
            )
            
            payment = Payment(
                hui_group_id=schedule.hui_group_id,
                membership_id=membership_id,
                schedule_id=schedule_id,
                amount=payment_amount,
                payment_method=PaymentMethod.CASH,
                payment_status=PaymentStatus.PENDING,
                reference_code=reference_code,
                due_date=schedule.due_date
            )
            db.add(payment)
            db.flush()
            logger.info(f"Created new payment for membership {membership_id}, schedule {schedule_id}")
        
        if payment.payment_status == PaymentStatus.VERIFIED:
            raise HTTPException(status_code=400, detail="Giao dịch đã được xác nhận trước đó")
        
        was_overdue, days_late = payment_service.calculate_days_late(payment)
        
        payment.payment_status = PaymentStatus.VERIFIED
        payment.verified_at = get_vietnam_now()
        payment.paid_at = get_vietnam_now()
        if note:
            payment.notes = note
        
        if was_overdue and days_late > 0:
            payment_service.handle_late_payment(
                db=db,
                membership=membership,
                payment=payment,
                days_late=days_late,
                hui_group=group,
                schedule=schedule,
                note=note
            )
        
        db.commit()
        payment_service.sync_batch_after_payment_verify(db, payment, current_user.id)
        payment_service.create_audit_log(
            db=db,
            user_id=current_user.id,
            action="verify_payment_by_membership",
            entity_type="payment",
            entity_id=str(payment.id),
            data={
                "payment_id": str(payment.id),
                "membership_id": membership_id,
                "schedule_id": schedule_id,
                "verified_by": str(current_user.id),
                "was_overdue": was_overdue,
                "days_late": days_late
            }
        )
        
        logger.info(f"Payment verified by membership: membership={membership_id}, schedule={schedule_id}, overdue={was_overdue}")
        
        return {
            "success": True,
            "message": "Xác nhận thanh toán thành công",
            "payment_id": str(payment.id),
            "was_overdue": was_overdue,
            "days_late": days_late
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying payment by membership: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schedules/{schedule_id}/bulk-create-payments")
async def bulk_create_payments(
    schedule_id: str,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Tạo payments cho tất cả members trong 1 kỳ (LOẠI NGƯỜI HỐT)"""
    try:
        schedule = db.query(HuiSchedule).filter(HuiSchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Không tìm thấy kỳ")
        
        group = db.query(HuiGroup).filter(HuiGroup.id == schedule.hui_group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")

        if current_user.role == UserRole.OWNER and group.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập dây hụi này")
        
        pot_receiver_membership_id = schedule.receiver_membership_id
        
        memberships = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == schedule.hui_group_id,
            HuiMembership.is_active == True
        ).all()
        
        if pot_receiver_membership_id:
            memberships = [m for m in memberships if str(m.id) != str(pot_receiver_membership_id)]
        
        existing_count = db.query(func.count(Payment.id)).filter(
            Payment.schedule_id == schedule_id
        ).scalar()
        
        if existing_count > 0:
            return {"success": True, "message": f"Đã tồn tại {existing_count} payments", "created": 0}
        
        created = 0
        for membership in memberships:
            # Tính tiền đúng theo logic hụi sống
            payment_amount = calculate_member_payment_amount(group, membership, schedule.cycle_number)
            
            reference_code = generate_reference_code(
                schedule.hui_group_id,
                membership.member_id,
                schedule.cycle_number
            )
            
            payment = Payment(
                hui_group_id=schedule.hui_group_id,
                membership_id=membership.id,
                schedule_id=schedule_id,
                amount=payment_amount,
                payment_method=PaymentMethod.QR_CODE,
                payment_status=PaymentStatus.PENDING,
                reference_code=reference_code,
                due_date=schedule.due_date
            )
            db.add(payment)
            created += 1
        
        db.commit()
        
        logger.info(f"Created {created} payments for schedule {schedule_id}")
        
        return {"success": True, "message": f"Đã tạo {created} payments (đã loại người hốt)", "created": created}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk creating payments: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
