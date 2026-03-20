import sys
import os
from dotenv import load_dotenv

# Load env variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

print("⏳ Đang kiểm tra khởi tạo Database Models...")

try:
    from database import engine
    import models
    
    # Force initialize mappers
    models.Base.metadata.create_all(bind=engine)
    
    print("✅ Models initialized thành công!")
    print("✅ Enums đã được load.")
    print("✅ Quan hệ User owner-member hợp lệ.")
    
except Exception as e:
    print(f"❌ Lỗi khởi tạo: {e}")
    sys.exit(1)
