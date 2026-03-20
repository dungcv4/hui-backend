"""
Telegram Bot Service for Hui Manager
Handles:
- Sending payment reminders
- Processing /start commands to link users
- Generating professional bills
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict
from telegram import Bot, Update
from telegram.constants import ParseMode
from config import settings

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.bot_token = settings.telegram_bot_token
        self.bot = None
        if self.bot_token:
            self.bot = Bot(token=self.bot_token)
    
    def is_configured(self) -> bool:
        return self.bot is not None
    
    async def send_message(self, chat_id: str, message: str, parse_mode: str = ParseMode.HTML) -> bool:
        """Send a message to a specific chat"""
        if not self.is_configured():
            logger.warning("Telegram bot not configured")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
    async def send_photo(self, chat_id: str, photo_url: str, caption: str = None) -> bool:
        """Send a photo (QR code) to a specific chat"""
        if not self.is_configured():
            return False
        
        try:
            await self.bot.send_photo(
                chat_id=chat_id,
                photo=photo_url,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram photo: {e}")
            return False
    
    def format_currency(self, amount: float) -> str:
        """Format amount as Vietnamese currency"""
        return f"{amount:,.0f}".replace(",", ".") + " đ"
    
    def generate_bill_message(
        self,
        member_name: str,
        hui_group_name: str,
        cycle_number: int,
        total_cycles: int,
        amount: float,
        due_date: datetime,
        payment_code: str,
        bank_name: str = None,
        bank_account: str = None,
        bank_account_name: str = None,
        reminder_type: int = 1  # 1=morning, 2=afternoon, 3=evening
    ) -> str:
        """Generate professional bill message in Vietnamese"""
        
        # Format due date
        due_str = due_date.strftime("%d/%m/%Y") if due_date else "N/A"
        
        # Emoji based on reminder type
        if reminder_type == 1:
            header_emoji = "📋"
            greeting = "Chào buổi sáng"
            urgency = ""
        elif reminder_type == 2:
            header_emoji = "⏰"
            greeting = "Nhắc nhở"
            urgency = "\n\n⚠️ <i>Bạn chưa đóng hụi hôm nay!</i>"
        else:
            header_emoji = "🔔"
            greeting = "Nhắc nhở cuối ngày"
            urgency = "\n\n🚨 <b>Hạn đóng hụi sắp hết!</b>"
        
        message = f"""
{header_emoji} <b>{greeting} - HuiManager Pro</b>

━━━━━━━━━━━━━━━━━━━━
👤 <b>Thành viên:</b> {member_name}
🏦 <b>Dây hụi:</b> {hui_group_name}
━━━━━━━━━━━━━━━━━━━━

📅 <b>Kỳ:</b> {cycle_number}/{total_cycles}
💰 <b>Số tiền:</b> {self.format_currency(amount)}
⏰ <b>Hạn đóng:</b> {due_str}

━━━━━━━━━━━━━━━━━━━━
📝 <b>Thông tin chuyển khoản:</b>
"""
        
        if bank_name and bank_account:
            message += f"""
🏦 Ngân hàng: <code>{bank_name}</code>
💳 Số TK: <code>{bank_account}</code>
👤 Chủ TK: <code>{bank_account_name or 'N/A'}</code>
"""
        
        message += f"""
📌 Nội dung CK: <code>{payment_code}</code>

━━━━━━━━━━━━━━━━━━━━{urgency}

💡 <i>Vui lòng ghi đúng nội dung chuyển khoản để hệ thống tự động xác nhận.</i>

🤖 <i>Tin nhắn tự động từ HuiManager Pro</i>
"""
        return message.strip()
    
    def generate_payment_success_message(
        self,
        member_name: str,
        hui_group_name: str,
        cycle_number: int,
        amount: float,
        paid_at: datetime
    ) -> str:
        """Generate payment success confirmation message"""
        
        paid_str = paid_at.strftime("%H:%M %d/%m/%Y") if paid_at else "N/A"
        
        message = f"""
✅ <b>XÁC NHẬN THANH TOÁN THÀNH CÔNG</b>

━━━━━━━━━━━━━━━━━━━━
👤 <b>Thành viên:</b> {member_name}
🏦 <b>Dây hụi:</b> {hui_group_name}
📅 <b>Kỳ:</b> {cycle_number}
💰 <b>Số tiền:</b> {self.format_currency(amount)}
🕐 <b>Thời gian:</b> {paid_str}
━━━━━━━━━━━━━━━━━━━━

🎉 Cảm ơn bạn đã đóng hụi đúng hạn!

🤖 <i>HuiManager Pro</i>
"""
        return message.strip()
    
    def generate_qr_url(
        self,
        bank_code: str,
        account_number: str,
        amount: float,
        content: str,
        account_name: str = None
    ) -> str:
        """Generate VietQR URL"""
        import urllib.parse
        
        # Map common bank names to codes
        bank_codes = {
            'Vietcombank': 'VCB',
            'Techcombank': 'TCB', 
            'MB Bank': 'MB',
            'BIDV': 'BIDV',
            'Agribank': 'VBA',
            'VPBank': 'VPB',
            'ACB': 'ACB',
            'Sacombank': 'STB',
            'TPBank': 'TPB',
            'VIB': 'VIB',
        }
        
        code = bank_codes.get(bank_code, bank_code)
        encoded_content = urllib.parse.quote(content)
        encoded_name = urllib.parse.quote(account_name or '')
        
        return f"https://img.vietqr.io/image/{code}-{account_number}-compact2.png?amount={int(amount)}&addInfo={encoded_content}&accountName={encoded_name}"


# Create singleton instance
telegram_service = TelegramService()


async def send_payment_reminder(
    chat_id: str,
    member_name: str,
    hui_group_name: str,
    cycle_number: int,
    total_cycles: int,
    amount: float,
    due_date: datetime,
    payment_code: str,
    bank_name: str = None,
    bank_account: str = None,
    bank_account_name: str = None,
    reminder_type: int = 1
) -> bool:
    """Send payment reminder with QR code"""
    
    # Generate bill message
    message = telegram_service.generate_bill_message(
        member_name=member_name,
        hui_group_name=hui_group_name,
        cycle_number=cycle_number,
        total_cycles=total_cycles,
        amount=amount,
        due_date=due_date,
        payment_code=payment_code,
        bank_name=bank_name,
        bank_account=bank_account,
        bank_account_name=bank_account_name,
        reminder_type=reminder_type
    )
    
    # Send message first
    success = await telegram_service.send_message(chat_id, message)
    
    # Send QR code if bank info is available
    if success and bank_name and bank_account:
        qr_url = telegram_service.generate_qr_url(
            bank_code=bank_name,
            account_number=bank_account,
            amount=amount,
            content=payment_code,
            account_name=bank_account_name
        )
        await telegram_service.send_photo(
            chat_id=chat_id,
            photo_url=qr_url,
            caption="📱 <b>Quét mã QR để thanh toán</b>"
        )
    
    return success


async def send_payment_confirmation(
    chat_id: str,
    member_name: str,
    hui_group_name: str,
    cycle_number: int,
    amount: float,
    paid_at: datetime
) -> bool:
    """Send payment success confirmation"""
    
    message = telegram_service.generate_payment_success_message(
        member_name=member_name,
        hui_group_name=hui_group_name,
        cycle_number=cycle_number,
        amount=amount,
        paid_at=paid_at
    )
    
    return await telegram_service.send_message(chat_id, message)
