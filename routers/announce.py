"""
Announcements Router - Winner announcement and member info endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    require_role, get_current_user, logger
)
from utils import calculate_member_payment_amount

router = APIRouter(tags=["Announcements"])


@router.post("/telegram/announce-winner")
async def announce_winner_to_group(
    hui_group_id: str = Body(...),
    winner_name: str = Body(...),
    cycle_number: int = Body(...),
    total_pot: float = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send winner announcement to linked Telegram group"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group or not hui_group.telegram_group_id:
            return {"success": False, "message": "Group chưa được liên kết"}
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot chưa được cấu hình"}
        
        pot_str = f"{total_pot:,.0f}".replace(",", ".") + " đ"
        
        message = f"""
🏆🎉 <b>THÔNG BÁO HỐT HỤI</b> 🎉🏆

━━━━━━━━━━━━━━━━━━━━
🏦 <b>Dây hụi:</b> {hui_group.name}
📅 <b>Kỳ:</b> {cycle_number}/{hui_group.total_cycles}
━━━━━━━━━━━━━━━━━━━━

🥇 <b>NGƯỜI HỐT HỤI KỲ NÀY:</b>

👤 <b>{winner_name}</b>

💰 <b>Tổng tiền hốt:</b> {pot_str}

━━━━━━━━━━━━━━━━━━━━

🎊 Chúc mừng {winner_name}!

🤖 <i>HuiManager Pro</i>
"""
        
        bot = Bot(token=app_settings.telegram_bot_token)
        await bot.send_message(
            chat_id=hui_group.telegram_group_id,
            text=message.strip(),
            parse_mode="HTML"
        )
        
        return {"success": True, "message": "Đã gửi thông báo người hốt hụi"}
    
    except Exception as e:
        logger.error(f"Error announcing winner: {e}")
        return {"success": False, "message": str(e)}


@router.get("/hui-groups/{hui_group_id}/members-for-bill")
async def get_members_for_bill(
    hui_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get members list with payment status for bill sending - EXCLUDING pot receiver"""
    try:
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        current_schedule = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == hui_group_id,
            HuiSchedule.cycle_number == hui_group.current_cycle
        ).first()
        
        pot_receiver_membership_id = current_schedule.receiver_membership_id if current_schedule else None
        
        pot_receiver = None
        if pot_receiver_membership_id:
            receiver_membership = db.query(HuiMembership).filter(
                HuiMembership.id == pot_receiver_membership_id
            ).first()
            if receiver_membership:
                receiver_member = db.query(Member).filter(Member.id == receiver_membership.member_id).first()
                if receiver_member:
                    pot_receiver = {
                        "membership_id": str(pot_receiver_membership_id),
                        "name": receiver_member.name,
                        "phone": receiver_member.phone,
                        "slot_count": receiver_membership.slot_count or 1,
                        "payment_code": receiver_membership.payment_code
                    }
        
        result = db.execute(text("""
            SELECT 
                hm.id as membership_id,
                m.id as member_id,
                m.name,
                m.phone,
                hm.slot_count,
                hm.payment_code,
                p.payment_status,
                p.amount
            FROM hui_memberships hm
            JOIN members m ON hm.member_id = m.id
            LEFT JOIN payments p ON p.membership_id = hm.id 
                AND p.schedule_id = :schedule_id
            WHERE hm.hui_group_id = :group_id
                AND hm.is_active = TRUE
                AND (hm.id != :pot_receiver_id OR :pot_receiver_id IS NULL)
            ORDER BY m.name
        """), {
            "group_id": hui_group_id,
            "schedule_id": current_schedule.id if current_schedule else None,
            "pot_receiver_id": pot_receiver_membership_id
        })
        
        members = []
        for row in result:
            members.append({
                "membership_id": row[0],
                "user_id": row[1],
                "name": row[2],
                "phone": row[3],
                "slot_count": row[4] or 1,
                "payment_code": row[5],
                "payment_status": row[6] or "pending",
                "amount": float(row[7]) if row[7] else float(hui_group.amount_per_cycle * (row[4] or 1))
            })
        
        return {
            "hui_group_id": hui_group_id,
            "hui_group_name": hui_group.name,
            "current_cycle": hui_group.current_cycle,
            "pot_receiver": pot_receiver,
            "members": members,
            "total_members": len(members)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting members for bill: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/telegram/request-qr-from-receiver/{hui_group_id}")
async def request_qr_from_receiver(
    hui_group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send message to group requesting pot receiver to provide their QR code"""
    try:
        from config import settings as app_settings
        from telegram import Bot
        from utils import calculate_owner_fee
        
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == hui_group_id).first()
        if not hui_group:
            return {"success": False, "message": "Không tìm thấy dây hụi"}
        
        if not hui_group.telegram_group_id:
            return {"success": False, "message": "Dây hụi này chưa liên kết Telegram group"}
        
        if not app_settings.telegram_bot_token:
            return {"success": False, "message": "Bot chưa được cấu hình"}
        
        current_schedule = db.query(HuiSchedule).filter(
            HuiSchedule.hui_group_id == hui_group_id,
            HuiSchedule.cycle_number == hui_group.current_cycle
        ).first()
        
        if not current_schedule or not current_schedule.receiver_membership_id:
            return {"success": False, "message": "Chưa chỉ định người hốt cho kỳ này"}
        
        receiver_membership = db.query(HuiMembership).filter(
            HuiMembership.id == current_schedule.receiver_membership_id
        ).first()
        
        receiver = db.query(Member).filter(Member.id == receiver_membership.member_id).first()
        if not receiver:
            return {"success": False, "message": "Không tìm thấy thông tin người hốt"}
        
        memberships = db.query(HuiMembership).filter(
            HuiMembership.hui_group_id == hui_group_id,
            HuiMembership.is_active == True
        ).all()
        total_slots = sum(m.slot_count or 1 for m in memberships)
        total_collection = hui_group.amount_per_cycle * total_slots
        owner_fee = calculate_owner_fee(total_collection, hui_group.fee_type, hui_group.fee_value)
        distribution_amount = total_collection - owner_fee
        
        receiver_slot_count = receiver_membership.slot_count or 1
        receiver_contribution = hui_group.amount_per_cycle * receiver_slot_count
        net_received = distribution_amount - receiver_contribution
        
        message = f"""
🎉 <b>THÔNG BÁO NGƯỜI HỐT - KỲ {hui_group.current_cycle}/{hui_group.total_cycles}</b>

📌 <b>Dây hụi:</b> {hui_group.name}
👤 <b>Người hốt kỳ này:</b> {receiver.name}

💵 <b>Chi tiết tài chính:</b>
  • Tổng thu từ {total_slots} chân: {total_collection:,.0f}₫
  • Phí chủ hụi: {owner_fee:,.0f}₫
  • Tiền {receiver.name} đóng ({receiver_slot_count} chân): {receiver_contribution:,.0f}₫

💰 <b>Số tiền {receiver.name} nhận thực:</b> {net_received:,.0f}₫

━━━━━━━━━━━━━━━━━━━━━
📲 <b>{receiver.name}</b> vui lòng gửi <b>MÃ QR ngân hàng</b> vào group để chủ hụi chuyển tiền nhé!
━━━━━━━━━━━━━━━━━━━━━

⏰ Ngày đến hạn: {current_schedule.due_date.strftime('%d/%m/%Y') if current_schedule.due_date else 'N/A'}
"""
        
        bot = Bot(token=app_settings.telegram_bot_token)
        await bot.send_message(
            chat_id=hui_group.telegram_group_id,
            text=message,
            parse_mode="HTML"
        )
        
        return {"success": True, "message": f"Đã gửi yêu cầu QR cho {receiver.name} vào group"}
    
    except Exception as e:
        logger.error(f"Error requesting QR from receiver: {e}")
        return {"success": False, "message": str(e)}
