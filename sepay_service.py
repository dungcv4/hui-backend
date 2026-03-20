import hmac
import hashlib
import qrcode
from io import BytesIO
import base64
import json
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

class SepayService:
    """Service for Sepay payment integration"""
    
    def __init__(self):
        self.app_id = settings.sepay_app_id
        self.secret_key = settings.sepay_secret_key
        self.webhook_secret = settings.sepay_webhook_secret
    
    def generate_qr_code(
        self,
        account_number: str,
        bank_name: str,
        amount: float,
        reference_code: str,
        account_name: str = "Hui Manager"
    ) -> dict:
        """
        Generate QR code for bank transfer payment
        Returns base64 encoded QR code and payment info
        """
        try:
            # Create payment content for Vietnamese banks (VietQR standard)
            # Format: Bank|Account|Amount|Reference
            qr_content = f"{bank_name}|{account_number}|{int(amount)}|{reference_code}"
            
            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_content)
            qr.make(fit=True)
            
            # Create image
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert to base64
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode()
            
            return {
                "qr_code_base64": f"data:image/png;base64,{img_base64}",
                "reference_code": reference_code,
                "amount": amount,
                "account_number": account_number,
                "bank_name": bank_name,
                "account_name": account_name,
                "transfer_content": reference_code
            }
        except Exception as e:
            logger.error(f"Error generating QR code: {str(e)}")
            raise
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify webhook signature using HMAC-SHA256
        """
        try:
            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()
            
            # Use constant-time comparison to prevent timing attacks
            return hmac.compare_digest(expected_signature, signature)
        except Exception as e:
            logger.error(f"Error verifying webhook signature: {str(e)}")
            return False
    
    def extract_reference_code(self, transfer_content: str) -> Optional[str]:
        """
        Extract reference code from bank transfer content
        Format: HUI{group_id}M{member_id}C{cycle}
        """
        try:
            # Look for HUI pattern
            if "HUI" in transfer_content.upper():
                # Extract the reference code pattern
                start_idx = transfer_content.upper().find("HUI")
                # Take next 30 characters as reference code (should be enough)
                potential_ref = transfer_content[start_idx:start_idx+30].split()[0]
                return potential_ref
            return None
        except Exception as e:
            logger.error(f"Error extracting reference code: {str(e)}")
            return None

sepay_service = SepayService()