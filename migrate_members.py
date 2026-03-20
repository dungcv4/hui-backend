"""
Migration script: Migrate member data from users table to new members table

Run this after updating models.py to create the members table and migrate existing data.
"""
import sys
sys.path.append('.')

from sqlalchemy import text
from database import engine, SessionLocal
from models import Base, User, Member
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migration():
    """Migrate member data from users table to members table"""
    
    # Create all tables (including new members table)
    logger.info("Creating tables...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        # Check if members table already has data
        existing_members = db.query(Member).count()
        if existing_members > 0:
            logger.warning(f"Members table already has {existing_members} records. Skipping migration.")
            return
        
        # Find all users with role='member' and owner_id is not null
        logger.info("Finding member users to migrate...")
        
        member_users = db.execute(text("""
            SELECT id, owner_id, phone, name, email, telegram_chat_id, is_active, created_at
            FROM users 
            WHERE role = 'member' AND owner_id IS NOT NULL
        """)).fetchall()
        
        if not member_users:
            logger.info("No member users found to migrate.")
            return
        
        logger.info(f"Found {len(member_users)} member users to migrate.")
        
        # Dictionary to map old user.id -> new member.id
        id_mapping = {}
        
        # Migrate each user to members table
        for user_row in member_users:
            user_id = user_row[0]
            owner_id = user_row[1]
            phone = user_row[2]
            name = user_row[3]
            email = user_row[4]
            telegram_chat_id = user_row[5]
            is_active = user_row[6]
            created_at = user_row[7]
            
            # Check if member with same phone+owner already exists
            existing = db.query(Member).filter(
                Member.phone == phone,
                Member.owner_id == owner_id
            ).first()
            
            if existing:
                logger.warning(f"Member with phone {phone} already exists for owner {owner_id}. Using existing.")
                id_mapping[user_id] = existing.id
                continue
            
            # Create new member (keeping the same ID for easier FK migration)
            new_member = Member(
                id=user_id,  # Keep same ID to simplify FK updates
                owner_id=owner_id,
                phone=phone,
                name=name,
                email=email,
                telegram_chat_id=telegram_chat_id,
                is_active=is_active,
                created_at=created_at
            )
            db.add(new_member)
            id_mapping[user_id] = user_id
            logger.info(f"Migrated member: {name} ({phone})")
        
        db.commit()
        logger.info(f"Successfully migrated {len(id_mapping)} members.")
        
        # Note: Since we kept the same ID, FK updates are not needed
        # The hui_memberships, payment_batches, etc. will still work
        # because member_id values in those tables match the new members.id
        
        logger.info("Migration completed successfully!")
        logger.info("Note: Old member records in 'users' table can be deleted manually after verification.")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Member Table Migration Script")
    print("=" * 60)
    print("\nThis script will:")
    print("1. Create the new 'members' table")
    print("2. Copy data from users (role='member') to members table")
    print("3. Keep the same IDs for seamless FK compatibility")
    print("\nNote: This script is safe to run multiple times.")
    print("=" * 60)
    
    confirm = input("\nDo you want to proceed? (yes/no): ")
    if confirm.lower() == 'yes':
        run_migration()
    else:
        print("Migration cancelled.")
