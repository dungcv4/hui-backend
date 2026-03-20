"""
Telegram Messages Router - Send messages, bills, summaries to Telegram
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
import urllib.parse

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    PaymentStatus, require_role, get_current_user, get_vietnam_today_range,
    logger
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/telegram", tags=["Telegram Messages"])


@router.post("/send-message/{hui_group_id}")
async def send_custom_message_to_group(
    hui_group_id: str,
    message: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send custom message from owner to hui group's Telegram"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group:
            return {"success": False, "message": "Không tìm thấy dây hụi"}
        
        if not hui_group.telegram_group_id:
            return {"success": False, "message": "Dây hụi này chưa liên kết Telegram group"}
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot chưa được cấu hình"}
        
        formatted_message = f"""
📢 <b>THÔNG BÁO</b>
🏦 {hui_group.name}

{message}
"""
        
        bot = Bot(token=app_settings.telegram_bot_token)
        await bot.send_message(
            chat_id=hui_group.telegram_group_id,
            text=formatted_message.strip(),
            parse_mode="HTML"
        )
        
        return {"success": True, "message": "Đã gửi thông báo vào group Telegram"}
    
    except Exception as e:
        logger.error(f"Error sending custom message: {e}")
        return {"success": False, "message": str(e)}


@router.post("/send-bill-to-group/{hui_group_id}")
async def send_bill_to_group(
    hui_group_id: str,
    request_body: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send bill with QR code for a specific member to the group"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        member_id = request_body.get("member_id")
        if not member_id:
            return {"success": False, "message": "Vui lòng chọn thành viên"}
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group:
            return {"success": False, "message": "Không tìm thấy dây hụi"}
        
        if not hui_group.telegram_group_id:
            return {"success": False, "message": "Dây hụi này chưa liên kết Telegram group"}
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot chưa được cấu hình"}
        
        membership = db.query(HuiMembership).filter(
            HuiMembership.id == member_id,
            HuiMembership.hui_group_id == hui_group_id
        ).first()
        
        if not membership:
            return {"success": False, "message": "Không tìm thấy thành viên trong dây hụi này"}
        
        member = db.query(Member).filter(Member.id == membership.member_id).first()
        if not member:
            return {"success": False, "message": "Không tìm thấy thông tin thành viên"}
        
        current_schedule = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == hui_group_id,
            HuiSchedule.cycle_number == hui_group.current_cycle
        ).first()
        
        # Tính tiền đúng theo logic hụi sống
        amount = calculate_member_payment_amount(hui_group, membership, hui_group.current_cycle)
        due_date_str = current_schedule.due_date.strftime("%d/%m/%Y") if current_schedule and current_schedule.due_date else "N/A"
        amount_str = f"{amount:,.0f}".replace(",", ".") + " đ"
        
        qr_url = None
        if hui_group.bank_name and hui_group.bank_account_number:
            bank_codes = {
                'Vietcombank': 'VCB', 'Techcombank': 'TCB', 'MB Bank': 'MB', 'MB': 'MB',
                'BIDV': 'BIDV', 'Agribank': 'VBA', 'VPBank': 'VPB', 'ACB': 'ACB',
                'Sacombank': 'STB', 'TPBank': 'TPB', 'VIB': 'VIB',
            }
            bank_code = bank_codes.get(hui_group.bank_name, hui_group.bank_name)
            content = membership.payment_code or f"HUI {member.name}"
            encoded_content = urllib.parse.quote(content)
            qr_url = f"https://img.vietqr.io/image/{bank_code}-{hui_group.bank_account_number}-compact2.png?amount={int(amount)}&addInfo={encoded_content}"
        
        slot_info = f" ({membership.slot_count} chân)" if membership.slot_count > 1 else ""
        message = f"""
📋 <b>BILL ĐÓNG HỤI</b>

━━━━━━━━━━━━━━━━━━━━
👤 <b>{member.name}</b>{slot_info}
🏦 {hui_group.name}
━━━━━━━━━━━━━━━━━━━━

📅 <b>Kỳ:</b> {hui_group.current_cycle}/{hui_group.total_cycles}
💰 <b>Số tiền:</b> {amount_str}
⏰ <b>Hạn đóng:</b> {due_date_str}

━━━━━━━━━━━━━━━━━━━━
📝 <b>Thông tin chuyển khoản:</b>
🏦 Ngân hàng: <code>{hui_group.bank_name or 'N/A'}</code>
💳 Số TK: <code>{hui_group.bank_account_number or 'N/A'}</code>
👤 Chủ TK: <code>{hui_group.bank_account_name or 'N/A'}</code>
📌 Nội dung CK: <code>{membership.payment_code or 'N/A'}</code>
━━━━━━━━━━━━━━━━━━━━
"""
        
        bot = Bot(token=app_settings.telegram_bot_token)
        await bot.send_message(chat_id=hui_group.telegram_group_id, text=message.strip(), parse_mode="HTML")
        
        if qr_url:
            await bot.send_photo(
                chat_id=hui_group.telegram_group_id,
                photo=qr_url,
                caption=f"📱 <b>Quét mã QR để thanh toán - {member.name}</b>",
                parse_mode="HTML"
            )
        
        return {"success": True, "message": f"Đã gửi bill của {member.name} vào group"}
    
    except Exception as e:
        logger.error(f"Error sending bill to group: {e}")
        return {"success": False, "message": str(e)}


@router.post("/send-daily-summary/{hui_group_id}")
async def send_daily_summary(
    hui_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send daily summary to hui group's Telegram"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group:
            return {"success": False, "message": "Không tìm thấy dây hụi"}
        
        if not hui_group.telegram_group_id:
            return {"success": False, "message": "Dây hụi này chưa liên kết Telegram group"}
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot chưa được cấu hình"}
        
        today_start, today_end = get_vietnam_today_range()
        
        current_schedule = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == hui_group_id,
            HuiSchedule.cycle_number == hui_group.current_cycle
        ).first()
        
        if not current_schedule:
            return {"success": False, "message": "Không tìm thấy lịch kỳ hiện tại"}
        
        payments = db.query(Payment).filter(
            Payment.schedule_id == current_schedule.id
        ).all()
        
        paid_members = []
        pending_members = []
        total_paid = 0
        total_pending = 0
        
        for payment in payments:
            membership = db.query(HuiMembership).filter(HuiMembership.id == payment.membership_id).first()
            member = db.query(Member).filter(Member.id == membership.member_id).first() if membership else None
            member_name = member.name if member else "N/A"
            
            if payment.payment_status == PaymentStatus.VERIFIED:
                paid_members.append(f"• {member_name}: {payment.amount:,.0f}đ".replace(",", "."))
                total_paid += payment.amount
            else:
                pending_members.append(f"• {member_name}: {payment.amount:,.0f}đ".replace(",", "."))
                total_pending += payment.amount
        
        paid_list = "\n".join(paid_members) if paid_members else "• (Chưa có)"
        pending_list = "\n".join(pending_members) if pending_members else "• (Không còn ai)"
        
        total_paid_str = f"{total_paid:,.0f}".replace(",", ".") + "đ"
        total_pending_str = f"{total_pending:,.0f}".replace(",", ".") + "đ"
        
        current_cycle = hui_group.current_cycle
        due_date_str = current_schedule.due_date.strftime("%d/%m/%Y") if current_schedule.due_date else "N/A"
        
        paid_count = len(paid_members)
        pending_count = len(pending_members)
        
        message = f"""
📊 <b>BÁO CÁO TỔNG HỢP</b>
🏦 {hui_group.name}

━━━━━━━━━━━━━━━━━━━━
📅 Kỳ {current_cycle}/{hui_group.total_cycles}
⏰ Hạn đóng: {due_date_str}
━━━━━━━━━━━━━━━━━━━━

✅ <b>ĐÃ ĐÓNG ({paid_count} người):</b>
{paid_list}
💰 Tổng thu: <b>{total_paid_str}</b>

⏳ <b>CHƯA ĐÓNG ({pending_count} người):</b>
{pending_list}
💸 Còn thiếu: <b>{total_pending_str}</b>

━━━━━━━━━━━━━━━━━━━━
📈 <b>Tỷ lệ:</b> {paid_count}/{paid_count + pending_count} ({round(paid_count/(paid_count+pending_count)*100) if (paid_count+pending_count) > 0 else 0}%)
━━━━━━━━━━━━━━━━━━━━

🤖 <i>HuiManager Pro - Báo cáo tự động</i>
"""
        
        bot = Bot(token=app_settings.telegram_bot_token)
        await bot.send_message(chat_id=hui_group.telegram_group_id, text=message.strip(), parse_mode="HTML")
        
        return {"success": True, "message": "Đã gửi báo cáo tổng kết"}
    
    except Exception as e:
        logger.error(f"Error sending daily summary: {e}")
        return {"success": False, "message": str(e)}


@router.post("/send-reminder")
async def send_telegram_reminder(
    payment_id: str = Body(...),
    reminder_type: int = Body(1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send payment reminder to a user via Telegram"""
    try:
        from telegram_service import send_payment_reminder
        
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        
        membership = db.query(HuiMembership).filter(HuiMembership.id == payment.membership_id).first()
        member = db.query(Member).filter(Member.id == membership.member_id).first()
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == payment.hui_group_id).first()
        schedule = db.query(HuiSchedule).filter(HuiSchedule.id == payment.schedule_id).first()
        
        if not member.telegram_chat_id:
            return {"success": False, "message": "Người dùng chưa liên kết Telegram"}
        
        success = await send_payment_reminder(
            chat_id=member.telegram_chat_id,
            member_name=member.name,
            hui_group_name=hui_group.name,
            cycle_number=schedule.cycle_number if schedule else 1,
            total_cycles=hui_group.total_cycles,
            amount=payment.amount,
            due_date=schedule.due_date if schedule else None,
            payment_code=membership.payment_code,
            bank_name=hui_group.bank_name,
            bank_account=hui_group.bank_account_number,
            bank_account_name=hui_group.bank_account_name,
            reminder_type=reminder_type
        )
        
        if success:
            return {"success": True, "message": f"Đã gửi nhắc nhở đến {member.name}"}
        else:
            return {"success": False, "message": "Không thể gửi tin nhắn Telegram"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending reminder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-bulk-reminders")
async def send_bulk_reminders(
    reminder_type: int = Body(1, embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send payment reminders to all users with pending payments today"""
    try:
        from datetime import datetime
        from telegram_service import send_payment_reminder
        from sqlalchemy import text
        
        # NOTE: This raw SQL query still uses 'users' table but needs to reference 'members' table
        # TODO: Update this query to use members table after full migration
        query = db.execute(text("""
            SELECT 
                p.id as payment_id,
                p.amount,
                p.reference_code,
                m.id as member_id,
                m.name as member_name,
                m.phone as member_phone,
                m.telegram_chat_id,
                hg.id as hui_group_id,
                hg.name as hui_group_name,
                hg.total_cycles,
                hg.bank_name,
                hg.bank_account_number,
                hg.bank_account_name,
                hs.cycle_number,
                hs.due_date,
                hm.payment_code
            FROM payments p
            JOIN hui_memberships hm ON p.membership_id = hm.id
            JOIN members m ON hm.member_id = m.id
            JOIN hui_groups hg ON p.hui_group_id = hg.id
            JOIN hui_schedules hs ON p.schedule_id = hs.id
            WHERE p.payment_status = 'pending'
            AND DATE(hs.due_date) = CURDATE()
            AND m.telegram_chat_id IS NOT NULL
            ORDER BY hg.name, m.name
        """))
        
        reminders = [
            {
                "telegram_chat_id": row[6],
                "member_name": row[4],
                "hui_group_name": row[8],
                "cycle_number": row[13],
                "total_cycles": row[9],
                "amount": float(row[1]) if row[1] else 0,
                "due_date": row[14].isoformat() if row[14] else None,
                "payment_code": row[15],
                "bank_name": row[10],
                "bank_account_number": row[11],
                "bank_account_name": row[12]
            } for row in query
        ]
        
        sent_count = 0
        failed_count = 0
        results = []
        
        for reminder in reminders:
            try:
                success = await send_payment_reminder(
                    chat_id=reminder["telegram_chat_id"],
                    member_name=reminder["member_name"],
                    hui_group_name=reminder["hui_group_name"],
                    cycle_number=reminder["cycle_number"],
                    total_cycles=reminder["total_cycles"],
                    amount=reminder["amount"],
                    due_date=datetime.fromisoformat(reminder["due_date"]) if reminder["due_date"] else None,
                    payment_code=reminder["payment_code"],
                    bank_name=reminder["bank_name"],
                    bank_account=reminder["bank_account_number"],
                    bank_account_name=reminder["bank_account_name"],
                    reminder_type=reminder_type
                )
                
                if success:
                    sent_count += 1
                    results.append({"member": reminder["member_name"], "status": "sent"})
                else:
                    failed_count += 1
                    results.append({"member": reminder["member_name"], "status": "failed"})
                    
            except Exception as e:
                failed_count += 1
                results.append({"member": reminder["member_name"], "status": "error", "error": str(e)})
        
        return {
            "success": True,
            "total": len(reminders),
            "sent": sent_count,
            "failed": failed_count,
            "results": results
        }
    
    except Exception as e:
        logger.error(f"Error sending bulk reminders: {e}")
        raise HTTPException(status_code=500, detail=str(e))
