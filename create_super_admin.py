import sys
import os
from dotenv import load_dotenv

# Load env variables explicitly for script execution
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from database import SessionLocal
from models import User, UserRole
from passlib.context import CryptContext

# Password handling
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def create_super_admin():
    db = SessionLocal()
    try:
        print("--- TẠO TÀI KHOẢN SYSTEM ADMIN ---")
        phone = input("Nhập số điện thoại (username): ").strip()
        if not phone:
            print("Lỗi: Số điện thoại không được để trống")
            return

        # Check existing
        existing = db.query(User).filter(User.phone == phone).first()
        if existing:
            print(f"Lỗi: User {phone} đã tồn tại!")
            return

        name = input("Nhập tên hiển thị: ").strip() or "System Admin"
        password = input("Nhập mật khẩu: ").strip()
        if not password:
            print("Lỗi: Mật khẩu không được để trống")
            return

        # Create
        hashed_password = pwd_context.hash(password)
        admin_user = User(
            phone=phone,
            name=name,
            password_hash=hashed_password,
            role=UserRole.SYSTEM_ADMIN,
            is_active=True
        )
        
        db.add(admin_user)
        db.commit()
        
        print(f"\n✅ Đã tạo thành công tài khoản ADMIN: {phone}")
        print("Bạn có thể dùng tài khoản này để đăng nhập vào trang quản trị.")
        
    except Exception as e:
        print(f"Lỗi: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    create_super_admin()
