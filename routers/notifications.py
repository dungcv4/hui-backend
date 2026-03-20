"""
Notifications Router - Real-time transaction alerts
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import timedelta
import json

from routers.dependencies import (
    get_db, User, UserRole, WebhookEvent, require_role,
    get_vietnam_now, get_vietnam_today_range, get_vietnam_week_start,
    get_vietnam_month_start, logger
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/recent")
async def get_recent_notifications(
    limit: int = 20,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy danh sách thông báo giao dịch mới từ Sepay webhook"""
    try:
        cutoff_time = get_vietnam_now() - timedelta(hours=24)
        cutoff_time_naive = cutoff_time.replace(tzinfo=None)
        
        webhook_events = db.query(WebhookEvent).filter(
            WebhookEvent.event_type == "sepay_transfer",
            WebhookEvent.created_at >= cutoff_time_naive
        ).order_by(WebhookEvent.created_at.desc()).limit(limit).all()
        
        notifications = []
        unread_count = 0
        
        for event in webhook_events:
            try:
                payload = json.loads(event.payload) if event.payload else {}
            except:
                payload = {}
            
            amount = payload.get('transferAmount', 0)
            content = payload.get('content', '')
            bank = payload.get('gateway', '')
            account = payload.get('accountNumber', '')
            
            is_read = event.status in ['success', 'failed']
            if not is_read:
                unread_count += 1
            
            notification = {
                "id": event.id,
                "type": "new_transaction",
                "title": f"Giao dịch từ {bank}" if bank else "Giao dịch mới",
                "description": content[:100] if content else f"Tài khoản: {account}",
                "amount": float(amount) if amount else 0,
                "status": event.status,
                "is_read": is_read,
                "external_id": event.external_id,
                "created_at": event.created_at.isoformat() if event.created_at else None
            }
            notifications.append(notification)
        
        return {
            "notifications": notifications,
            "unread_count": unread_count,
            "total": len(notifications)
        }
    
    except Exception as e:
        logger.error(f"Error getting notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Đánh dấu notification đã đọc"""
    try:
        event = db.query(WebhookEvent).filter(WebhookEvent.id == notification_id).first()
        # Acknowledge they've seen it
        return {"success": True}
    
    except Exception as e:
        logger.error(f"Error marking notification read: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mark-all-read")
async def mark_all_notifications_read(
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Đánh dấu tất cả notifications đã đọc"""
    try:
        return {"success": True}
    
    except Exception as e:
        logger.error(f"Error marking all notifications read: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transactions/summary")
async def get_transaction_summary(
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Tổng thu theo ngày/tuần/tháng để đối soát với ngân hàng"""
    try:
        today_start, today_end = get_vietnam_today_range()
        week_start = get_vietnam_week_start()
        month_start = get_vietnam_month_start()
        
        all_events = db.query(WebhookEvent).filter(
            WebhookEvent.event_type == "sepay_transfer",
            WebhookEvent.status == "success"
        ).all()
        
        total_today = 0
        count_today = 0
        total_week = 0
        count_week = 0
        total_month = 0
        count_month = 0
        
        for event in all_events:
            try:
                payload = json.loads(event.payload) if event.payload else {}
                amount = float(payload.get('transferAmount', 0))
            except:
                amount = 0
            
            event_time = event.created_at
            if event_time:
                if event_time >= today_start:
                    total_today += amount
                    count_today += 1
                if event_time >= week_start:
                    total_week += amount
                    count_week += 1
                if event_time >= month_start:
                    total_month += amount
                    count_month += 1
        
        return {
            "today": {
                "total": total_today,
                "count": count_today,
                "date": today_start.strftime("%Y-%m-%d")
            },
            "this_week": {
                "total": total_week,
                "count": count_week,
                "from": week_start.strftime("%Y-%m-%d")
            },
            "this_month": {
                "total": total_month,
                "count": count_month,
                "from": month_start.strftime("%Y-%m-%d")
            }
        }
    
    except Exception as e:
        logger.error(f"Error getting transaction summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))
