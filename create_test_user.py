import sys
import os
from dotenv import load_dotenv
from passlib.context import CryptContext
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, UserRole, Base

# Load env
load_dotenv()

# Setup DB
DATABASE_URL = "sqlite:///./sql_app.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

# Setup Password Hasher
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def create_user():
    phone = "0999999999"
    password = "password123"
    
    # Check if exists
    existing = db.query(User).filter(User.phone == phone).first()
    if existing:
        print(f"User {phone} already exists")
        # Update password just in case
        existing.password_hash = pwd_context.hash(password)
        db.commit()
        print(f"Updated password for {phone} to {password}")
        return

    new_user = User(
        phone=phone,
        name="Test User",
        email="test@example.com",
        role=UserRole.OWNER,
        password_hash=pwd_context.hash(password)
    )
    db.add(new_user)
    db.commit()
    print(f"Created user {phone} with password {password}")

if __name__ == "__main__":
    create_user()
