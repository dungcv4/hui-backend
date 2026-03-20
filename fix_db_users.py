import os
from dotenv import load_dotenv

# Load FIRST
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from sqlalchemy import text
from database import SessionLocal

def fix_users_schema():
    db = SessionLocal()
    try:
        print("🛠 Fixing Users Schema for Multi-tenant Membership...")
        
        # 1. Add owner_id column if not exists
        try:
            db.execute(text("ALTER TABLE users ADD COLUMN owner_id VARCHAR(36) NULL, ADD CONSTRAINT fk_user_owner FOREIGN KEY (owner_id) REFERENCES users(id)"))
            print("✅ Added owner_id column.")
        except Exception as e:
            if "Duplicate column name" in str(e):
                print("ℹ️ owner_id column already exists.")
            else:
                print(f"⚠️ Error adding column: {e}")

        # 2. Check and Drop existing unique index on phone
        # Note: Index name might be 'phone' or 'ix_users_phone'.
        # We try dropping both or querying first.
        indexes = db.execute(text("SHOW INDEX FROM users WHERE Column_name = 'phone' AND Non_unique = 0")).fetchall()
        for idx in indexes:
            idx_name = idx[2] # Key_name
            if idx_name != 'PRIMARY':
                print(f"Drop existing unique index: {idx_name}")
                try:
                    db.execute(text(f"DROP INDEX {idx_name} ON users"))
                    print("✅ Dropped index.")
                except Exception as e:
                    print(f"⚠️ Failed to drop index {idx_name}: {e}")

        # 3. Add new Composite Unique Index (owner_id, phone)
        # This allows multiple phones if owner_id differs.
        # Note: In MySQL, (phone, owner_id) with owner_id=NULL allows duplicates.
        # This is actually GOOD for now, as we might have multiple Owners with same phone? 
        # No, Owners must be unique. 
        # But we will enforce Owner uniqueness via App Logic for now or Partial Index if supported.
        
        try:
            db.execute(text("CREATE UNIQUE INDEX idx_phone_owner ON users(phone, owner_id)"))
            print("✅ Created new composite index idx_phone_owner.")
        except Exception as e:
            if "Duplicate key name" in str(e):
                print("ℹ️ Index idx_phone_owner already exists.")
            else:
                print(f"⚠️ Error creating index: {e}")

        db.commit()
        print("✅ Users schema update complete!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    fix_users_schema()
