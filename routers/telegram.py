"""
Telegram Router - Telegram bot integration endpoints
Settings, linking, scheduler status
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Body
from sqlalchemy.orm import Session
from sqlalchemy import text

from routers.dependencies import (
    get_db, User, UserRole, HuiGroup, require_role, get_current_user,
    get_vietnam_now, logger
)

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.get("/settings")
async def get_telegram_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get Telegram bot settings"""
    try:
        from config import settings as app_settings
        
        result = db.execute(text("SELECT setting_key, setting_value FROM telegram_settings"))
        settings_dict = {row[0]: row[1] for row in result}
        
        bot_configured = bool(app_settings.telegram_bot_token)
        bot_username = None
        
        if bot_configured:
            try:
                from telegram import Bot
                bot = Bot(token=app_settings.telegram_bot_token)
                bot_info = await bot.get_me()
                bot_username = bot_info.username
            except Exception as bot_error:
                logger.warning(f"Could not get bot info: {bot_error}")
        
        linked_count = db.execute(text(
            "SELECT COUNT(*) FROM users WHERE telegram_chat_id IS NOT NULL"
        )).scalar() or 0
        
        return {
            "bot_configured": bot_configured,
            "bot_username": bot_username,
            "linked_users_count": linked_count,
            "settings": {
                "enabled": settings_dict.get("telegram_enabled", "true") == "true",
                "reminder_time_1": settings_dict.get("reminder_time_1", "08:00"),
                "reminder_time_2": settings_dict.get("reminder_time_2", "16:00"),
                "reminder_time_3": settings_dict.get("reminder_time_3", "21:00"),
                "reminder_1_enabled": settings_dict.get("reminder_1_enabled", "true") == "true",
                "reminder_2_enabled": settings_dict.get("reminder_2_enabled", "true") == "true",
                "reminder_3_enabled": settings_dict.get("reminder_3_enabled", "true") == "true",
            }
        }
    except Exception as e:
        logger.error(f"Error getting telegram settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings")
async def update_telegram_settings(
    settings_data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update Telegram bot settings"""
    try:
        for key, value in settings_data.items():
            str_value = str(value).lower() if isinstance(value, bool) else str(value)
            db.execute(text("""
                INSERT INTO telegram_settings (id, setting_key, setting_value)
                VALUES (UUID(), :key, :value)
                ON DUPLICATE KEY UPDATE setting_value = :value
            """), {"key": key, "value": str_value})
        
        db.commit()
        return {"success": True, "message": "Đã cập nhật cài đặt Telegram"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating telegram settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/link")
async def link_telegram_account(
    phone: str = Body(..., embed=True),
    telegram_chat_id: str = Body(..., embed=True),
    db: Session = Depends(get_db)
):
    """Link a Telegram chat ID to a user by phone number (called by bot)"""
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            return {"success": False, "message": "Số điện thoại không tồn tại trong hệ thống"}
        
        user.telegram_chat_id = telegram_chat_id
        db.commit()
        
        return {
            "success": True,
            "message": f"Đã liên kết Telegram với tài khoản {user.name}",
            "user_name": user.name
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error linking telegram: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/unlink")
async def unlink_telegram_account(
    user_id: str = Body(..., embed=True),
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Unlink Telegram from a user account"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Không tìm thấy user")
        
        user.telegram_chat_id = None
        db.commit()
        
        return {"success": True, "message": f"Đã hủy liên kết Telegram của {user.name}"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error unlinking telegram: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/linked-users")
async def get_linked_users(
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Get list of users with Telegram linked"""
    try:
        users = db.query(User).filter(User.telegram_chat_id != None).all()
        
        return {
            "users": [
                {
                    "id": u.id,
                    "name": u.name,
                    "phone": u.phone,
                    "telegram_chat_id": u.telegram_chat_id
                } for u in users
            ],
            "count": len(users)
        }
    except Exception as e:
        logger.error(f"Error getting linked users: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test-send")
async def test_send_telegram(
    user_id: str = Body(..., embed=True),
    message: str = Body("Test message from HuiManager", embed=True),
    current_user: User = Depends(require_role([UserRole.OWNER])),
    db: Session = Depends(get_db)
):
    """Send test message to a user via Telegram"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        if not app_settings.telegram_bot_token:
            raise HTTPException(status_code=400, detail="Telegram Bot chưa được cấu hình")
        
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.telegram_chat_id:
            raise HTTPException(status_code=400, detail="User chưa liên kết Telegram")
        
        bot = Bot(token=app_settings.telegram_bot_token)
        await bot.send_message(
            chat_id=user.telegram_chat_id,
            text=f"🔔 <b>Test từ HuiManager</b>\n\n{message}",
            parse_mode="HTML"
        )
        
        return {"success": True, "message": f"Đã gửi tin nhắn đến {user.name}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending test telegram: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hui-groups/{group_id}/telegram-status")
async def get_hui_group_telegram_status(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get Telegram link status for a hui group"""
    try:
        group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        return {
            "hui_group_id": group.id,
            "hui_group_name": group.name,
            "telegram_group_id": group.telegram_group_id,
            "is_linked": bool(group.telegram_group_id),
            "linked_at": group.telegram_group_linked_at.isoformat() if group.telegram_group_linked_at else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting telegram status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hui-groups/{group_id}/unlink-telegram")
async def unlink_hui_group_telegram(
    group_id: str,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Unlink Telegram group from a hui group"""
    try:
        group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        group.telegram_group_id = None
        group.telegram_group_linked_at = None
        db.commit()
        
        return {"success": True, "message": f"Đã hủy liên kết Telegram của dây hụi {group.name}"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error unlinking telegram group: {e}")
        raise HTTPException(status_code=500, detail=str(e))
