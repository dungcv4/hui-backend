"""
QR Payments Router - QR code generation and payment list endpoints
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Optional
import urllib.parse

from routers.dependencies import (
    get_db, User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    require_role, get_current_user, logger
)

router = APIRouter(tags=["QR Payments"])


def get_sepay_bank_code(bank_name: str) -> str:
    """Map tên ngân hàng sang mã Sepay"""
    bank_mapping = {
        "vietcombank": "VCB", "vcb": "VCB", "techcombank": "TCB", "tcb": "TCB",
        "mbbank": "MB", "mb": "MB", "vietinbank": "CTG", "bidv": "BIDV",
        "agribank": "AGR", "vpbank": "VPB", "acb": "ACB", "sacombank": "STB",
        "hdbank": "HDB", "tpbank": "TPB", "ocb": "OCB", "msb": "MSB",
        "vib": "VIB", "shb": "SHB", "eximbank": "EIB", "seabank": "SSB",
        "momo": "MOMO", "zalopay": "ZALOPAY", "vnpay": "VNPAY",
    }
    bank_lower = bank_name.lower().strip().replace(" ", "")
    if bank_lower in bank_mapping:
        return bank_mapping[bank_lower]
    for key, code in bank_mapping.items():
        if key in bank_lower or bank_lower in key:
            return code
    return bank_name.upper()


@router.get("/payments/{payment_id}/qr-info")
async def get_payment_qr_info(
    payment_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lấy thông tin thanh toán để tạo QR code"""
    try:
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Không tìm thấy thanh toán")
        
        membership = db.query(HuiMembership).filter(HuiMembership.id == payment.membership_id).first()
        member = db.query(Member).filter(Member.id == membership.member_id).first() if membership else None
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == payment.hui_group_id).first()
        schedule = db.query(HuiSchedule).filter(HuiSchedule.id == payment.schedule_id).first() if payment.schedule_id else None
        
        payment_code = membership.payment_code if membership else payment.reference_code
        transfer_content = f"{payment_code} Ky{schedule.cycle_number if schedule else ''} {hui_group.name if hui_group else ''}"
        
        bank_name = hui_group.bank_name if hui_group else None
        account_no = hui_group.bank_account_number if hui_group else None
        account_name = hui_group.bank_account_name if hui_group else None
        
        sepay_qr_url = None
        if account_no and bank_name:
            encoded_content = urllib.parse.quote(transfer_content)
            bank_code = get_sepay_bank_code(bank_name)
            sepay_qr_url = f"https://qr.sepay.vn/img?acc={account_no}&bank={bank_code}&amount={int(payment.amount)}&des={encoded_content}&template=compact"
        
        return {
            "payment_id": payment.id,
            "status": payment.payment_status.value,
            "amount": payment.amount,
            "amount_formatted": f"{payment.amount:,.0f} đ",
            "due_date": payment.due_date.isoformat() if payment.due_date else None,
            "member": {
                "id": member.id if member else None,
                "name": member.name if member else "N/A",
                "phone": member.phone if member else None
            },
            "hui_group": {
                "id": hui_group.id if hui_group else None,
                "name": hui_group.name if hui_group else "N/A",
                "cycle": schedule.cycle_number if schedule else None
            },
            "bank_info": {
                "bank_name": bank_name,
                "account_number": account_no,
                "account_name": account_name,
                "is_configured": bool(account_no and bank_name)
            },
            "payment_code": payment_code,
            "transfer_content": transfer_content,
            "qr_url": sepay_qr_url,
            "copy_text": f"💰 THANH TOÁN HỤI\n\n📌 Số tiền: {payment.amount:,.0f}đ\n🏦 Ngân hàng: {bank_name or 'Chưa cấu hình'}\n💳 STK: {account_no or 'N/A'}\n👤 Chủ TK: {account_name or 'N/A'}\n📝 Nội dung: {transfer_content}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payment QR info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hui-groups/{group_id}/payment-list")
async def get_group_payment_list(
    group_id: str,
    schedule_id: Optional[str] = None,
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy danh sách thanh toán của dây hụi với QR info"""
    try:
        hui_group = db.query(HuiGroup).filter(HuiGroup.id == group_id).first()
        if not hui_group:
            raise HTTPException(status_code=404, detail="Không tìm thấy dây hụi")
        
        if not schedule_id:
            schedule = db.query(HuiSchedule).filter(
                HuiSchedule.hui_group_id == group_id,
                HuiSchedule.is_completed == False
            ).order_by(HuiSchedule.due_date.asc()).first()
        else:
            schedule = db.query(HuiSchedule).filter(HuiSchedule.id == schedule_id).first()
        
        if not schedule:
            return {"payments": [], "schedule": None}
        
        payments = db.query(Payment).filter(Payment.schedule_id == schedule.id).all()
        
        bank_name = hui_group.bank_name
        account_no = hui_group.bank_account_number
        account_name = hui_group.bank_account_name
        bank_code = get_sepay_bank_code(bank_name) if bank_name else None
        
        result = []
        for payment in payments:
            membership = db.query(HuiMembership).filter(HuiMembership.id == payment.membership_id).first()
            member = db.query(Member).filter(Member.id == membership.member_id).first() if membership else None
            
            payment_code = membership.payment_code if membership else payment.reference_code
            transfer_content = f"{payment_code} Ky{schedule.cycle_number} {hui_group.name}"
            
            sepay_qr_url = None
            if account_no and bank_code:
                encoded_content = urllib.parse.quote(transfer_content)
                sepay_qr_url = f"https://qr.sepay.vn/img?acc={account_no}&bank={bank_code}&amount={int(payment.amount)}&des={encoded_content}&template=compact"
            
            result.append({
                "payment_id": payment.id,
                "member_name": member.name if member else "N/A",
                "member_phone": member.phone if member else None,
                "amount": payment.amount,
                "status": payment.payment_status.value,
                "payment_code": payment_code,
                "transfer_content": transfer_content,
                "qr_url": sepay_qr_url,
                "slot_count": membership.slot_count if membership else 1
            })
        
        return {
            "hui_group": {
                "id": hui_group.id,
                "name": hui_group.name,
                "bank_name": bank_name,
                "bank_account": account_no,
                "bank_account_name": account_name,
                "bank_configured": bool(account_no and bank_name)
            },
            "schedule": {
                "id": schedule.id,
                "cycle_number": schedule.cycle_number,
                "due_date": schedule.due_date.isoformat() if schedule.due_date else None
            },
            "payments": result
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting group payment list: {e}")
        raise HTTPException(status_code=500, detail=str(e))
