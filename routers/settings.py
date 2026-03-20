"""
Settings Router - Global settings endpoints
Bank config, system settings
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel as PydanticBaseModel
from typing import Optional

from routers.dependencies import (
    get_db, User, UserRole, GlobalBankConfig, require_role, logger
)

router = APIRouter(prefix="/settings", tags=["Settings"])


def get_sepay_bank_code(bank_name: str) -> str:
    """Map tên ngân hàng sang mã Sepay"""
    bank_mapping = {
        "vietcombank": "VCB", "vcb": "VCB", "techcombank": "TCB", "tcb": "TCB",
        "mbbank": "MB", "mb": "MB", "vietinbank": "CTG", "bidv": "BIDV",
        "agribank": "AGR", "vpbank": "VPB", "acb": "ACB", "sacombank": "STB",
        "hdbank": "HDB", "tpbank": "TPB", "ocb": "OCB", "msb": "MSB",
    }
    return bank_mapping.get(bank_name.lower().replace(" ", ""), bank_name.upper()[:3])


class GlobalBankConfigRequest(PydanticBaseModel):
    bank_name: str
    bank_code: Optional[str] = None
    account_number: str
    account_name: Optional[str] = None
    qr_template: Optional[str] = "compact"


@router.get("/bank")
async def get_global_bank_config(
    current_user: User = Depends(require_role([UserRole.OWNER, UserRole.STAFF])),
    db: Session = Depends(get_db)
):
    """Lấy config số TK chung của chủ hụi"""
    try:
        config = db.query(GlobalBankConfig).filter(
            GlobalBankConfig.owner_id == current_user.id,
            GlobalBankConfig.is_active == True
        ).first()
        
        if not config:
            return {"configured": False, "config": None}
        
        return {
            "configured": True,
            "config": {
                "id": config.id,
                "bank_name": config.bank_name,
                "bank_code": config.bank_code,
                "account_number": config.account_number,
                "account_name": config.account_name,
                "qr_template": config.qr_template
            }
        }
    except Exception as e:
        logger.error(f"Error getting bank config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bank")
async def save_global_bank_config(
    request: GlobalBankConfigRequest,
    current_user: User = Depends(require_role([UserRole.OWNER])),
    db: Session = Depends(get_db)
):
    """Lưu/cập nhật config số TK chung"""
    try:
        bank_code = request.bank_code or get_sepay_bank_code(request.bank_name)
        
        existing = db.query(GlobalBankConfig).filter(
            GlobalBankConfig.owner_id == current_user.id
        ).first()
        
        if existing:
            existing.bank_name = request.bank_name
            existing.bank_code = bank_code
            existing.account_number = request.account_number
            existing.account_name = request.account_name
            existing.qr_template = request.qr_template
            existing.is_active = True
        else:
            config = GlobalBankConfig(
                owner_id=current_user.id,
                bank_name=request.bank_name,
                bank_code=bank_code,
                account_number=request.account_number,
                account_name=request.account_name,
                qr_template=request.qr_template
            )
            db.add(config)
        
        db.commit()
        
        return {"success": True, "message": "Đã lưu cấu hình ngân hàng"}
    
    except Exception as e:
        logger.error(f"Error saving bank config: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
