import os
from dotenv import load_dotenv

# Load env variables explicitly - MUST BE BEFORE IMPORTS
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from sqlalchemy import text
from database import SessionLocal

def fix_database():
    db = SessionLocal()
    try:
        print("🛠 Fix Database Schema...")
        
        # 1. Update Enum definition in MySQL
        print("Updating users table role Enum...")
        db.execute(text("ALTER TABLE users MODIFY COLUMN role ENUM('system_admin', 'owner', 'staff', 'member') DEFAULT 'member'"))
        
        # 2. Fix the broken admin user (who has role='')
        print("Fixing invalid admin user...")
        # Assuming the broken user is the one we tried to create with 'admin' phone or similar, or just any user with empty role
        result = db.execute(text("UPDATE users SET role = 'system_admin' WHERE role = '' OR role IS NULL"))
        print(f"Updated {result.rowcount} users with invalid role.")
        
        db.commit()
        print("✅ Database cleanup complete!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    fix_database()
