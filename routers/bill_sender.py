"""
Bill Sender Router - Smart bill sending via Telegram
Handles single bill, batch bill, and send-all operations
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from urllib.parse import quote
import uuid

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    PaymentStatus, PaymentMethod, PaymentBatch, BatchPayment, BatchStatus,
    BillSendHistory, BillSendStatus, GlobalBankConfig,
    require_role, get_vietnam_now, get_vietnam_today_range, logger
)
from utils import calculate_member_payment_amount

router = APIRouter(prefix="/telegram", tags=["Bill Sender"])


def generate_batch_code(member_id: str, date) -> str:
    """Generate unique batch code for a member"""
    import hashlib
    date_str = date.strftime("%y%m%d") if hasattr(date, 'strftime') else str(date)[:10].replace("-", "")
    hash_input = f"{member_id}{date_str}"
    hash_val = hashlib.md5(hash_input.encode()).hexdigest()[:6].upper()
    return f"HB{date_str}{hash_val}"



def get_sepay_bank_code(bank_name: str) -> str:
    """Map tên ngân hàng sang mã Sepay"""
    bank_mapping = {
        "vietcombank": "VCB", "vcb": "VCB", "techcombank": "TCB", "tcb": "TCB",
        "mbbank": "MB", "mb": "MB", "vietinbank": "CTG", "bidv": "BIDV",
        "agribank": "AGR", "vpbank": "VPB", "acb": "ACB", "sacombank": "STB",
    }
    bank_lower = bank_name.lower().strip().replace(" ", "")
    return bank_mapping.get(bank_lower, bank_name.upper()[:3])


async def _send_smart_bill_internal(member_id: str, db: Session, current_user: User):
    """Internal logic to send smart bill (single or batch)"""
    try:
        from telegram import Bot
        from config import settings as app_settings
        import urllib.parse
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot chưa được cấu hình"}
        
        bot = Bot(token=app_settings.telegram_bot_token)
        today_start, today_end = get_vietnam_today_range()
        
        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            return {"success": False, "message": "Không tìm thấy thành viên"}
        
        # Get all memberships for this member in owner's groups
        memberships = db.query(HuiMembership).join(HuiGroup).filter(
            HuiMembership.member_id == member_id,
            HuiMembership.is_active == True,
            HuiGroup.owner_id == current_user.id
        ).all()
        
        if not memberships:
            return {"success": False, "message": "Member không có trong dây hụi nào"}
        
        # Find all payments needed today
        payments_today = []
        total_amount = 0
        
        for membership in memberships:
            group = db.query(HuiGroup).filter(HuiGroup.id == membership.hui_group_id).first()
            schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group.id,
                HuiSchedule.due_date >= today_start,
                HuiSchedule.due_date < today_end
            ).first()
            
            if not schedule:
                continue
            
            # Skip if pot receiver
            if schedule.receiver_membership_id and str(schedule.receiver_membership_id) == str(membership.id):
                continue
            
            # Skip if already paid
            existing = db.query(Payment).filter(
                Payment.membership_id == membership.id,
                Payment.schedule_id == schedule.id,
                Payment.payment_status == PaymentStatus.VERIFIED
            ).first()
            
            if existing:
                continue
            
            # Tính tiền đúng theo logic hụi sống
            amount = calculate_member_payment_amount(group, membership, schedule.cycle_number)
            payments_today.append({
                "group": group,
                "schedule": schedule,
                "membership": membership,
                "amount": amount
            })
            total_amount += amount
        
        if not payments_today:
            return {"success": False, "message": "Member không có khoản nào cần đóng hôm nay"}
        
        # Determine bill type
        if len(payments_today) == 1:
            # Single bill
            p = payments_today[0]
            group = p["group"]
            schedule = p["schedule"]
            membership = p["membership"]
            amount = p["amount"]
            
            due_date_str = schedule.due_date.strftime("%d/%m/%Y") if schedule.due_date else "N/A"
            amount_str = f"{amount:,.0f}".replace(",", ".") + " đ"
            payment_code = membership.payment_code or f"HUI {member.name}"
            
            qr_url = None
            if group.bank_account_number and group.bank_name:
                bank_code = get_sepay_bank_code(group.bank_name)
                encoded = urllib.parse.quote(payment_code)
                qr_url = f"https://img.vietqr.io/image/{bank_code}-{group.bank_account_number}-compact2.png?amount={int(amount)}&addInfo={encoded}"
            
            message = f"""
📋 <b>BILL ĐÓNG HỤI</b>

👤 <b>{member.name}</b>
🏦 {group.name} - Kỳ {schedule.cycle_number}/{group.total_cycles}
💰 <b>Số tiền: {amount_str}</b>
⏰ Hạn đóng: {due_date_str}

━━━━━━━━━━━━━━━━━━━━
🏦 Ngân hàng: <code>{group.bank_name or 'N/A'}</code>
💳 Số TK: <code>{group.bank_account_number or 'N/A'}</code>
👤 Chủ TK: <code>{group.bank_account_name or 'N/A'}</code>
📌 Nội dung CK: <code>{payment_code}</code>
"""
            
            telegram_id = member.telegram_chat_id or group.telegram_group_id
            if not telegram_id:
                return {"success": False, "message": "Không có Telegram ID để gửi"}
            
            await bot.send_message(chat_id=telegram_id, text=message.strip(), parse_mode="HTML")
            
            if qr_url:
                await bot.send_photo(chat_id=telegram_id, photo=qr_url, caption="📱 Quét QR để thanh toán")
            
            return {"success": True, "message": f"Đã gửi bill cho {member.name}", "bill_type": "single", "total_amount": amount}
        
        else:
            # Batch bill
            batch_code = generate_batch_code(member_id, today_start)
            
            owner_group = db.query(HuiGroup).filter(
                HuiGroup.owner_id == current_user.id,
                HuiGroup.bank_account_number != None
            ).first()
            
            if not owner_group:
                return {"success": False, "message": "Chưa cấu hình ngân hàng cho dây hụi"}
            
            qr_url = None
            if owner_group.bank_account_number and owner_group.bank_name:
                bank_code = get_sepay_bank_code(owner_group.bank_name)
                encoded = quote(batch_code)
                qr_url = f"https://qr.sepay.vn/img?acc={owner_group.bank_account_number}&bank={bank_code}&amount={int(total_amount)}&des={encoded}&template=compact"
            
            amount_str = f"{total_amount:,.0f}".replace(",", ".") + " đ"
            
            details_list = []
            for p in payments_today:
                slot_info = f" x{p['membership'].slot_count}" if p['membership'].slot_count > 1 else ""
                details_list.append(f"• {p['group'].name} - Kỳ {p['schedule'].cycle_number}{slot_info}: {p['amount']:,.0f}đ".replace(",", "."))
            details_str = "\n".join(details_list)
            
            message = f"""
📦 <b>BILL TỔNG HỢP</b>

━━━━━━━━━━━━━━━━━━━━
👤 <b>{member.name}</b>
📊 {len(payments_today)} dây hụi cần đóng hôm nay
━━━━━━━━━━━━━━━━━━━━

📋 <b>Chi tiết:</b>
{details_str}

━━━━━━━━━━━━━━━━━━━━
💰 <b>TỔNG CỘNG: {amount_str}</b>
━━━━━━━━━━━━━━━━━━━━

📝 <b>Thông tin chuyển khoản:</b>
🏦 Ngân hàng: <code>{owner_group.bank_name or 'N/A'}</code>
💳 Số TK: <code>{owner_group.bank_account_number or 'N/A'}</code>
👤 Chủ TK: <code>{owner_group.bank_account_name or 'N/A'}</code>
📌 Nội dung CK: <code>{batch_code}</code>

⚠️ <i>Vui lòng chuyển ĐÚNG số tiền và ghi ĐÚNG nội dung</i>
"""
            
            telegram_id = member.telegram_chat_id or owner_group.telegram_group_id
            if not telegram_id:
                return {"success": False, "message": "Không có Telegram ID để gửi"}
            
            await bot.send_message(chat_id=telegram_id, text=message.strip(), parse_mode="HTML")
            
            if qr_url:
                await bot.send_photo(chat_id=telegram_id, photo=qr_url, caption=f"📱 Quét QR để thanh toán TỔNG - {member.name}")
            
            return {
                "success": True,
                "message": f"Đã gửi bill tổng cho {member.name} ({len(payments_today)} dây hụi)",
                "bill_type": "batch",
                "batch_code": batch_code,
                "total_amount": total_amount,
                "groups_count": len(payments_today)
            }
    
    except Exception as e:
        logger.error(f"Error in _send_smart_bill_internal: {e}")
        return {"success": False, "message": str(e)}


@router.post("/send-smart-bill/{member_id}")
async def send_smart_bill(
    member_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF]))
):
    """Send smart bill (single or batch) for a member"""
    return await _send_smart_bill_internal(member_id, db, current_user)


@router.post("/send-all-bills-today")
async def send_all_bills_today(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF]))
):
    """Send bills to ALL members who need to pay today"""
    try:
        today_start, today_end = get_vietnam_today_range()
        
        schedules_today = db.query(HuiSchedule).join(HuiGroup).filter(
            HuiGroup.owner_id == current_user.id,
            HuiSchedule.due_date >= today_start,
            HuiSchedule.due_date < today_end
        ).all()
        
        if not schedules_today:
            return {"success": False, "message": "Không có kỳ nào đến hạn hôm nay"}
        
        members_to_bill = set()
        
        for schedule in schedules_today:
            memberships = db.query(HuiMembership).filter(
                HuiMembership.hui_group_id == schedule.hui_group_id,
                HuiMembership.is_active == True
            ).all()
            
            for membership in memberships:
                if schedule.receiver_membership_id and str(schedule.receiver_membership_id) == str(membership.id):
                    continue
                
                existing = db.query(Payment).filter(
                    Payment.membership_id == membership.id,
                    Payment.schedule_id == schedule.id,
                    Payment.payment_status == PaymentStatus.VERIFIED
                ).first()
                
                if existing:
                    continue
                
                members_to_bill.add(membership.member_id)
        
        if not members_to_bill:
            return {"success": False, "message": "Tất cả members đã đóng đủ"}
        
        results = {
            "total": len(members_to_bill),
            "success": 0,
            "failed": 0,
            "single_bills": 0,
            "batch_bills": 0,
            "details": []
        }
        
        for member_id in members_to_bill:
            try:
                member = db.query(Member).filter(Member.id == member_id).first()
                if not member:
                    continue
                
                result = await _send_smart_bill_internal(member_id, db, current_user)
                
                if result.get("success"):
                    results["success"] += 1
                    if result.get("bill_type") == "single":
                        results["single_bills"] += 1
                    else:
                        results["batch_bills"] += 1
                else:
                    results["failed"] += 1
                
                results["details"].append({
                    "member_name": member.name,
                    "success": result.get("success"),
                    "bill_type": result.get("bill_type"),
                    "message": result.get("message")
                })
            except Exception as e:
                results["failed"] += 1
                results["details"].append({
                    "member_name": member.name if member else "N/A",
                    "success": False,
                    "message": str(e)
                })
        
        return {
            "success": True,
            "message": f"Đã xử lý {results['total']} members: {results['success']} thành công, {results['failed']} thất bại",
            "results": results
        }
    
    except Exception as e:
        logger.error(f"Error sending all bills: {e}")
        raise HTTPException(status_code=500, detail=str(e))
