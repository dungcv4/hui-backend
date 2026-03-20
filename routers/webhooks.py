"""
Webhooks Router - Sepay integration endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel as PydanticBaseModel
from typing import Optional
import hmac
import hashlib
import json
import re
import os

from routers.dependencies import (
    get_db, User, UserRole, HuiGroup, HuiMembership, Payment, PaymentStatus,
    PaymentBatch, BatchPayment, BatchStatus, WebhookEvent, AuditLog,
    require_role, get_vietnam_now, logger
)

router = APIRouter(tags=["Webhooks"])


class SepayWebhookPayload(PydanticBaseModel):
    """Schema cho webhook từ Sepay.vn"""
    id: int
    gateway: str
    transactionDate: str
    accountNumber: str
    code: Optional[str] = None
    content: str
    transferType: str
    transferAmount: float
    accumulated: Optional[float] = None
    subAccount: Optional[str] = None
    referenceCode: str
    description: Optional[str] = None


def verify_sepay_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature từ Sepay"""
    try:
        hash_object = hmac.new(
            secret.encode('utf-8'),
            msg=payload,
            digestmod=hashlib.sha256
        )
        calculated = "sha256=" + hash_object.hexdigest()
        return hmac.compare_digest(calculated, signature)
    except Exception:
        return False


def extract_payment_code(content: str) -> tuple:
    """Trích xuất mã thanh toán từ nội dung chuyển khoản"""
    content_upper = content.upper()
    
    # Check for BATCH code first
    batch_pattern = r'BATCH_\d{8}_[A-Z0-9]{6,}'
    batch_match = re.search(batch_pattern, content_upper)
    if batch_match:
        return ("batch", batch_match.group(0))
    
    # Then check for individual payment code
    payment_patterns = [
        r'PC[A-Z0-9]{6,}',
        r'HUI\d{6,}',
    ]
    
    for pattern in payment_patterns:
        match = re.search(pattern, content_upper)
        if match:
            return ("payment", match.group(0))
    
    return (None, None)


@router.post("/webhooks/sepay")
async def sepay_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """Webhook từ SePay để xử lý thanh toán tự động"""
    try:
        body = await request.body()
        payload_json = json.loads(body)
        
        sepay_secret = os.environ.get('SEPAY_WEBHOOK_SECRET')
        if sepay_secret:
            signature = request.headers.get('x-signature-sha256', '')
            if signature and not verify_sepay_signature(body, signature, sepay_secret):
                logger.warning("Invalid Sepay webhook signature")
                raise HTTPException(status_code=401, detail="Invalid signature")
        
        try:
            payload = SepayWebhookPayload(**payload_json)
        except Exception as e:
            logger.error(f"Invalid Sepay payload: {e}")
            raise HTTPException(status_code=400, detail="Invalid payload format")
        
        logger.info(f"Received Sepay webhook: txn_id={payload.id}, amount={payload.transferAmount}")
        
        if payload.transferType != 'in':
            logger.info(f"Ignoring outgoing transfer: {payload.id}")
            return {"success": True, "message": "Outgoing transfer ignored"}
        
        existing_event = db.query(WebhookEvent).filter(
            WebhookEvent.external_id == str(payload.id)
        ).first()
        
        if existing_event:
            logger.info(f"Duplicate webhook for transaction {payload.id}")
            return {"success": True, "message": "Already processed"}
        
        webhook_event = WebhookEvent(
            event_type="sepay_transfer",
            external_id=str(payload.id),
            payload=json.dumps(payload_json),
            status="processing"
        )
        db.add(webhook_event)
        db.commit()
        
        code_type, code_value = extract_payment_code(payload.content)
        
        if not code_value:
            logger.warning(f"No payment/batch code found in content: {payload.content}")
            webhook_event.status = "pending_review"
            webhook_event.error_message = "No payment code in content"
            db.commit()
            return {"success": True, "message": "No payment code found, manual review required"}
        
        logger.info(f"Extracted code: type={code_type}, value={code_value}")
        
        # Process BATCH CODE
        if code_type == "batch":
            batch = db.query(PaymentBatch).filter(
                PaymentBatch.batch_code == code_value
            ).first()
            
            if not batch:
                logger.warning(f"Batch not found: {code_value}")
                webhook_event.status = "pending_review"
                webhook_event.error_message = f"Batch not found: {code_value}"
                db.commit()
                return {"success": True, "message": "Batch not found, manual review required"}
            
            batch.received_amount = float(payload.transferAmount)
            batch.difference = batch.received_amount - batch.total_amount
            batch.transaction_id = str(payload.id)
            batch.transaction_content = payload.content
            batch.transaction_bank = payload.gateway
            batch.received_at = get_vietnam_now()
            
            if abs(batch.difference) < 1000:
                batch_items = db.query(BatchPayment).filter(
                    BatchPayment.batch_id == batch.id
                ).all()
                
                for item in batch_items:
                    item.is_verified = True
                    item.verified_at = get_vietnam_now()
                    
                    payment = db.query(Payment).filter(Payment.id == item.payment_id).first()
                    if payment:
                        payment.payment_status = PaymentStatus.VERIFIED
                        payment.verified_at = get_vietnam_now()
                        payment.paid_at = get_vietnam_now()
                        payment.bank_transaction_ref = payload.referenceCode
                        payment.notes = f"Auto-verified via Batch {code_value}. Bank: {payload.gateway}"
                
                batch.status = BatchStatus.PAID
                webhook_event.status = "success"
                webhook_event.error_message = f"Batch auto-verified: {len(batch_items)} payments"
                
                logger.info(f"Batch auto-verified: {code_value}, {len(batch_items)} payments")
            else:
                batch.status = BatchStatus.REVIEW
                webhook_event.status = "pending_review"
                webhook_event.error_message = f"Batch difference: {batch.difference:,.0f}đ"
                logger.warning(f"Batch amount mismatch: {code_value}, diff={batch.difference}")
            
            db.commit()
            
            return {
                "success": True,
                "message": "Batch webhook processed",
                "batch_code": code_value,
                "auto_verified": batch.status == BatchStatus.PAID
            }
        
        # Process INDIVIDUAL PAYMENT CODE
        payment_code = code_value
        logger.info(f"Processing individual payment code: {payment_code}")
        
        membership = db.query(HuiMembership).filter(
            HuiMembership.payment_code == payment_code,
            HuiMembership.is_active == True
        ).first()
        
        if not membership:
            logger.warning(f"Membership not found for payment code: {payment_code}")
            webhook_event.status = "failed"
            webhook_event.error_message = f"Membership not found: {payment_code}"
            db.commit()
            return {"success": True, "message": "Membership not found, manual review required"}
        
        pending_payment = db.query(Payment).filter(
            Payment.membership_id == membership.id,
            Payment.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE]),
            Payment.amount == payload.transferAmount
        ).order_by(Payment.due_date.asc()).first()
        
        if not pending_payment:
            pending_payment = db.query(Payment).filter(
                Payment.membership_id == membership.id,
                Payment.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE])
            ).order_by(Payment.due_date.asc()).first()
        
        if pending_payment:
            pending_payment.payment_status = PaymentStatus.VERIFIED
            pending_payment.bank_transaction_ref = payload.referenceCode
            pending_payment.webhook_data = json.dumps(payload_json)
            pending_payment.paid_at = get_vietnam_now()
            pending_payment.verified_at = get_vietnam_now()
            pending_payment.notes = f"Auto-verified via Sepay. Bank: {payload.gateway}"
            
            webhook_event.status = "success"
            webhook_event.payment_id = pending_payment.id
            
            logger.info(f"Payment auto-verified: {pending_payment.id}")
        else:
            webhook_event.status = "pending_review"
            webhook_event.error_message = "No pending payment found for this membership"
            logger.warning(f"No pending payment for membership {membership.id}")
        
        db.commit()
        
        return {
            "success": True,
            "message": "Webhook processed successfully",
            "payment_verified": pending_payment is not None,
            "payment_code": payment_code
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing Sepay webhook: {str(e)}")
        return {"success": False, "message": str(e)}


@router.get("/sepay/status")
async def get_sepay_integration_status(
    current_user: User = Depends(require_role([UserRole.OWNER])),
    db: Session = Depends(get_db)
):
    """Kiểm tra trạng thái tích hợp Sepay"""
    try:
        total_webhooks = 0
        success_webhooks = 0
        failed_webhooks = 0
        pending_review = 0
        recent_webhooks_data = []
        
        try:
            total_webhooks = db.query(func.count(WebhookEvent.id)).filter(
                WebhookEvent.event_type == "sepay_transfer"
            ).scalar() or 0
            
            try:
                success_webhooks = db.query(func.count(WebhookEvent.id)).filter(
                    WebhookEvent.event_type == "sepay_transfer",
                    WebhookEvent.status == "success"
                ).scalar() or 0
                
                failed_webhooks = db.query(func.count(WebhookEvent.id)).filter(
                    WebhookEvent.event_type == "sepay_transfer",
                    WebhookEvent.status == "failed"
                ).scalar() or 0
                
                pending_review = db.query(func.count(WebhookEvent.id)).filter(
                    WebhookEvent.event_type == "sepay_transfer",
                    WebhookEvent.status == "pending_review"
                ).scalar() or 0
                
                recent_webhooks = db.query(WebhookEvent).filter(
                    WebhookEvent.event_type == "sepay_transfer"
                ).order_by(WebhookEvent.created_at.desc()).limit(10).all()
                
                recent_webhooks_data = [
                    {
                        "id": w.id,
                        "external_id": w.external_id,
                        "status": getattr(w, 'status', 'unknown'),
                        "created_at": w.created_at.isoformat() if w.created_at else None,
                        "error_message": getattr(w, 'error_message', None)
                    } for w in recent_webhooks
                ]
                
            except Exception as status_error:
                logger.warning(f"Status column not available: {str(status_error)}")
                
        except Exception as webhook_error:
            logger.warning(f"WebhookEvent table not available: {str(webhook_error)}")
        
        backend_url = os.environ.get('REACT_APP_BACKEND_URL', 'http://localhost:8000')
        
        return {
            "integration_active": bool(os.environ.get('SEPAY_WEBHOOK_SECRET')),
            "webhook_url": f"{backend_url}/api/webhooks/sepay",
            "statistics": {
                "total_webhooks": total_webhooks,
                "success": success_webhooks,
                "failed": failed_webhooks,
                "pending_review": pending_review
            },
            "recent_webhooks": recent_webhooks_data
        }
        
    except Exception as e:
        logger.error(f"Error getting Sepay integration status: {str(e)}")
        backend_url = os.environ.get('REACT_APP_BACKEND_URL', 'http://localhost:8000')
        
        return {
            "integration_active": bool(os.environ.get('SEPAY_WEBHOOK_SECRET')),
            "webhook_url": f"{backend_url}/api/webhooks/sepay",
            "statistics": {"total_webhooks": 0, "success": 0, "failed": 0, "pending_review": 0},
            "recent_webhooks": []
        }
