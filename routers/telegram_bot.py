"""
Telegram Bot Router - Telegram bot webhook handler and webhook configuration
Handles /start, /link, /unlink, /status, /help commands
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Body
from sqlalchemy.orm import Session
import os

from routers.dependencies import (
    get_db, User, HuiGroup, get_current_user, get_vietnam_now, logger
)

router = APIRouter(prefix="/telegram", tags=["Telegram Bot"])


@router.post("/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook endpoint for Telegram Bot - handles /start, /link, /unlink, /status"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        if not app_settings.telegram_bot_token:
            return {"ok": True}
        
        data = await request.json()
        bot = Bot(token=app_settings.telegram_bot_token)
        
        message = data.get("message", {})
        chat = message.get("chat", {})
        text = message.get("text", "")
        
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("chat_type") or chat.get("type", "")
        
        # Handle /start command (private chat)
        if text.startswith("/start") and chat_type == "private":
            await bot.send_message(
                chat_id=chat_id,
                text="""
👋 <b>Chào mừng đến với HuiManager Pro Bot!</b>

Để liên kết tài khoản, vui lòng gửi <b>số điện thoại</b> của bạn.

Ví dụ: <code>0987654321</code>

📱 Số điện thoại phải trùng với số đã đăng ký trong hệ thống HuiManager.
""",
                parse_mode="HTML"
            )
            return {"ok": True}
        
        # Handle phone number in private chat
        if chat_type == "private" and text.replace(" ", "").isdigit() and len(text.replace(" ", "")) >= 9:
            phone = text.replace(" ", "")
            user = db.query(User).filter(User.phone == phone).first()
            
            if user:
                user.telegram_chat_id = chat_id
                db.commit()
                
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"""
✅ <b>Liên kết thành công!</b>

👤 <b>Tên:</b> {user.name}

Từ giờ bạn sẽ nhận được:
• 📋 Bill nhắc nhở đóng hụi
• ✅ Xác nhận khi thanh toán thành công

🤖 <i>HuiManager Pro</i>
""",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="❌ <b>Không tìm thấy tài khoản</b>\n\nSố điện thoại này chưa được đăng ký trong hệ thống HuiManager.\n\nVui lòng kiểm tra lại hoặc liên hệ chủ hụi.",
                    parse_mode="HTML"
                )
            return {"ok": True}
        
        # Handle /link command in group
        if text.startswith("/link") and chat_type in ["group", "supergroup"]:
            parts = text.split()
            
            if len(parts) < 2:
                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ <b>Thiếu mã dây hụi</b>\n\nCách dùng: <code>/link [mã dây hụi]</code>\n\nVí dụ: <code>/link abc123-xyz789</code>",
                    parse_mode="HTML"
                )
                return {"ok": True}
            
            hui_group_id = parts[1].strip()
            hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
            
            if not hui_group:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ <b>Không tìm thấy dây hụi</b>\n\nMã <code>{hui_group_id}</code> không tồn tại.",
                    parse_mode="HTML"
                )
                return {"ok": True}
            
            if hui_group.telegram_group_id and hui_group.telegram_group_id != chat_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ <b>Dây hụi đã được liên kết</b>\n\nDây hụi <b>{hui_group.name}</b> đã được liên kết với group Telegram khác.",
                    parse_mode="HTML"
                )
                return {"ok": True}
            
            hui_group.telegram_group_id = chat_id
            hui_group.telegram_group_linked_at = get_vietnam_now()
            db.commit()
            
            group_title = chat.get("title", "Group")
            
            await bot.send_message(
                chat_id=chat_id,
                text=f"""
✅ <b>LIÊN KẾT THÀNH CÔNG!</b>

━━━━━━━━━━━━━━━━━━━━
🏦 <b>Dây hụi:</b> {hui_group.name}
💬 <b>Group:</b> {group_title}
━━━━━━━━━━━━━━━━━━━━

<b>Từ giờ group này sẽ nhận được:</b>
• 🔔 Thông báo khi có người đóng tiền
• 📊 Báo cáo tổng kết ngày
• 🏆 Thông báo người hốt hụi kỳ mới

🤖 <i>HuiManager Pro</i>
""",
                parse_mode="HTML"
            )
            
            logger.info(f"Linked hui group {hui_group.name} to telegram group {chat_id}")
            return {"ok": True}
        
        # Handle /unlink command in group
        if text.startswith("/unlink") and chat_type in ["group", "supergroup"]:
            hui_group = db.query(HuiGroup).filter(HuiGroup.telegram_group_id == chat_id).first()
            
            if hui_group:
                hui_name = hui_group.name
                hui_group.telegram_group_id = None
                hui_group.telegram_group_linked_at = None
                db.commit()
                
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Đã hủy liên kết group với dây hụi <b>{hui_name}</b>",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Group này chưa được liên kết với dây hụi nào.",
                    parse_mode="HTML"
                )
            return {"ok": True}
        
        # Handle /status command in group
        if text.startswith("/status") and chat_type in ["group", "supergroup"]:
            hui_group = db.query(HuiGroup).filter(HuiGroup.telegram_group_id == chat_id).first()
            
            if hui_group:
                linked_at = hui_group.telegram_group_linked_at.strftime("%d/%m/%Y %H:%M") if hui_group.telegram_group_linked_at else "N/A"
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"""
📊 <b>TRẠNG THÁI LIÊN KẾT</b>

✅ Group này đã liên kết với:
🏦 <b>Dây hụi:</b> {hui_group.name}
📅 <b>Kỳ hiện tại:</b> {hui_group.current_cycle}/{hui_group.total_cycles}
🕐 <b>Liên kết lúc:</b> {linked_at}

🤖 <i>HuiManager Pro</i>
""",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Group này chưa được liên kết với dây hụi nào.\n\nDùng <code>/link [mã dây hụi]</code> để liên kết.",
                    parse_mode="HTML"
                )
            return {"ok": True}
        
        # Handle /help command
        if text.startswith("/help"):
            if chat_type == "private":
                help_text = """
📖 <b>HƯỚNG DẪN SỬ DỤNG</b>

<b>Liên kết tài khoản:</b>
Gửi số điện thoại của bạn để liên kết.

<b>Sau khi liên kết:</b>
• Nhận bill nhắc nhở đóng hụi
• Nhận xác nhận khi thanh toán

🤖 <i>HuiManager Pro</i>
"""
            else:
                help_text = """
📖 <b>HƯỚNG DẪN SỬ DỤNG (GROUP)</b>

<b>Các lệnh có sẵn:</b>
• <code>/link [mã dây hụi]</code> - Liên kết group với dây hụi
• <code>/unlink</code> - Hủy liên kết
• <code>/status</code> - Xem trạng thái liên kết
• <code>/help</code> - Hiển thị trợ giúp

🤖 <i>HuiManager Pro</i>
"""
            await bot.send_message(chat_id=chat_id, text=help_text, parse_mode="HTML")
            return {"ok": True}
        
        return {"ok": True}
        
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}")
        return {"ok": True}


@router.get("/webhook-config")
async def get_webhook_config(current_user: User = Depends(get_current_user)):
    """Get current Telegram webhook configuration"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        result = {
            "bot_configured": bool(app_settings.telegram_bot_token),
            "bot_username": None,
            "webhook_url": None,
            "webhook_active": False,
            "pending_updates": 0,
            "last_error": None
        }
        
        if app_settings.telegram_bot_token:
            bot = Bot(token=app_settings.telegram_bot_token)
            
            bot_info = await bot.get_me()
            result["bot_username"] = bot_info.username
            
            webhook_info = await bot.get_webhook_info()
            result["webhook_url"] = webhook_info.url or ""
            result["webhook_active"] = bool(webhook_info.url)
            result["pending_updates"] = webhook_info.pending_update_count or 0
            result["last_error"] = webhook_info.last_error_message
        
        return result
    
    except Exception as e:
        logger.error(f"Error getting webhook config: {e}")
        return {"bot_configured": False, "error": str(e)}


@router.delete("/webhook")
async def delete_webhook(current_user: User = Depends(get_current_user)):
    """Delete/disable Telegram webhook"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot token chưa được cấu hình"}
        
        bot = Bot(token=app_settings.telegram_bot_token)
        await bot.delete_webhook()
        
        return {"success": True, "message": "Đã xóa webhook."}
    
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
        return {"success": False, "message": str(e)}


@router.post("/setup-webhook")
async def setup_telegram_webhook(
    webhook_base_url: str = Body(None, embed=True),
    current_user: User = Depends(get_current_user)
):
    """Setup Telegram bot webhook URL"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot token chưa được cấu hình"}
        
        bot = Bot(token=app_settings.telegram_bot_token)
        
        backend_url = webhook_base_url
        if not backend_url:
            backend_url = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
        if not backend_url:
            backend_url = os.environ.get("BACKEND_URL", "").rstrip("/")
        
        if not backend_url:
            return {"success": False, "message": "Vui lòng cung cấp URL backend"}
        
        webhook_url = f"{backend_url}/api/telegram/webhook"
        await bot.set_webhook(url=webhook_url)
        
        webhook_info = await bot.get_webhook_info()
        
        return {
            "success": True,
            "message": "Đã cài đặt webhook thành công",
            "webhook_url": webhook_url,
            "webhook_info": {
                "url": webhook_info.url,
                "pending_update_count": webhook_info.pending_update_count
            }
        }
    
    except Exception as e:
        logger.error(f"Error setting up webhook: {e}")
        return {"success": False, "message": str(e)}


@router.get("/webhook-info")
async def get_telegram_webhook_info(current_user: User = Depends(get_current_user)):
    """Get current Telegram webhook info"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot token chưa được cấu hình"}
        
        bot = Bot(token=app_settings.telegram_bot_token)
        webhook_info = await bot.get_webhook_info()
        
        return {
            "success": True,
            "webhook_url": webhook_info.url,
            "pending_update_count": webhook_info.pending_update_count,
            "has_custom_certificate": webhook_info.has_custom_certificate,
            "last_error_date": webhook_info.last_error_date,
            "last_error_message": webhook_info.last_error_message
        }
    
    except Exception as e:
        logger.error(f"Error getting webhook info: {e}")
        return {"success": False, "message": str(e)}
