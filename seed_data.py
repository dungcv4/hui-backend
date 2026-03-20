"""
Seed data script for Hui Manager
Creates test data: users, hui groups, memberships, schedules, payments
"""
import sys
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Load env variables explicitly
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from passlib.context import CryptContext

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, engine
from models import (
    User, UserRole, Member, HuiGroup, HuiMembership, HuiSchedule, Payment,
    HuiCycle, HuiMethod, PaymentStatus, PaymentMethod, RiskLevel, Base
)
from utils import generate_payment_code, generate_reference_code, calculate_next_due_date

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def create_seed_data():
    """Create comprehensive test data"""
    # Create tables if not exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        print("🌱 Starting seed data creation...")
        
        # Create users
        print("\n👤 Creating users...")
        
        # Check if owner exists
        owner = db.query(User).filter(User.phone == "0987654321").first()
        if not owner:
            owner = User(
                phone="0987654321",
                name="Nguyễn Văn Chủ",
                email="chu@example.com",
                role=UserRole.OWNER,
                password_hash=pwd_context.hash("123456")
            )
            db.add(owner)
            db.commit()
            print("✓ Created owner")
        else:
            print("✓ Owner already exists, using existing")
        
        members_data = [
            {"phone": "0901234567", "name": "Trần Thị An", "email": "an@example.com"},
            {"phone": "0902345678", "name": "Lê Văn Bình", "email": "binh@example.com"},
            {"phone": "0903456789", "name": "Phạm Thị Cúc", "email": "cuc@example.com"},
            {"phone": "0904567890", "name": "Hoàng Văn Dũng", "email": "dung@example.com"},
            {"phone": "0905678901", "name": "Đỗ Thị Em", "email": "em@example.com"},
            {"phone": "0906789012", "name": "Vũ Văn Phong", "email": "phong@example.com"},
            {"phone": "0907890123", "name": "Mai Thị Giang", "email": "giang@example.com"},
            {"phone": "0908901234", "name": "Bùi Văn Hải", "email": "hai@example.com"},
            {"phone": "0909012345", "name": "Ngô Thị Lan", "email": "lan@example.com"},
            {"phone": "0910123456", "name": "Đinh Văn Khoa", "email": "khoa@example.com"},
        ]
        
        members = []
        for m_data in members_data:
            # Check if member exists in members table
            existing_member = db.query(Member).filter(
                Member.phone == m_data["phone"],
                Member.owner_id == owner.id
            ).first()
            if existing_member:
                members.append(existing_member)
            else:
                member = Member(
                    owner_id=owner.id,
                    phone=m_data["phone"],
                    name=m_data["name"],
                    email=m_data["email"]
                )
                db.add(member)
                members.append(member)
        
        db.commit()
        print(f"✓ Created/found {len(members)} members")
        
        # Create hui groups
        print("\n💰 Creating hui groups...")
        
        # Group 1: Monthly hui - active
        hui1 = HuiGroup(
            name="Hụi Nhóm Bạn Thân 2026",
            owner_id=owner.id,
            amount_per_cycle=1000000,  # 1 triệu/kỳ
            total_members=10,
            cycle_type=HuiCycle.MONTHLY,
            total_cycles=10,
            current_cycle=3,  # Đang ở kỳ 3
            fee_type="percentage",
            fee_value=5,  # 5% phí chủ hụi
            hui_method=HuiMethod.ASSIGNED,
            bank_account_number="0123456789",
            bank_name="Vietcombank",
            bank_account_name="NGUYEN VAN CHU",
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_active=True
        )
        db.add(hui1)
        
        # Group 2: Weekly hui - active
        hui2 = HuiGroup(
            name="Hụi Tuần Đồng Nghiệp",
            owner_id=owner.id,
            amount_per_cycle=500000,  # 500k/kỳ
            total_members=8,
            cycle_type=HuiCycle.WEEKLY,
            total_cycles=8,
            current_cycle=2,
            fee_type="fixed",
            fee_value=50000,  # 50k cố định
            hui_method=HuiMethod.LOTTERY,
            bank_account_number="9876543210",
            bank_name="Techcombank",
            bank_account_name="NGUYEN VAN CHU",
            start_date=datetime(2026, 1, 6, tzinfo=timezone.utc),
            is_active=True
        )
        db.add(hui2)
        
        db.commit()
        print("✓ Created 2 hui groups")
        
        # Create memberships for hui1 (10 members)
        print("\n👥 Creating memberships...")
        memberships1 = []
        for i, member in enumerate(members):
            payment_code = generate_payment_code(hui1.id, member.id)
            membership = HuiMembership(
                hui_group_id=hui1.id,
                member_id=member.id,
                payment_code=payment_code,
                credit_score=100 - (i * 2),  # Varied scores
                risk_level=RiskLevel.LOW if i < 7 else RiskLevel.MEDIUM,
                has_received=i < 2,  # First 2 members already received
                received_cycle=i + 1 if i < 2 else None,
                cccd=f"00123456789{i}",
                address=f"Địa chỉ thành viên {i+1}",
                rebate_percentage=2 if i >= 2 else 0,  # 2% cho người chưa hốt
            )
            db.add(membership)
            memberships1.append(membership)
        
        # Create memberships for hui2 (8 members)
        memberships2 = []
        for i, member in enumerate(members[:8]):
            payment_code = generate_payment_code(hui2.id, member.id)
            membership = HuiMembership(
                hui_group_id=hui2.id,
                member_id=member.id,
                payment_code=payment_code,
                credit_score=95 - i,
                risk_level=RiskLevel.LOW,
                has_received=i == 0,
                received_cycle=1 if i == 0 else None,
                rebate_percentage=1.5,  # 1.5% cho tất cả
            )
            db.add(membership)
            memberships2.append(membership)
        
        db.commit()
        print(f"✓ Created {len(memberships1)} + {len(memberships2)} memberships")
        
        # Create schedules for hui1
        print("\n📅 Creating schedules...")
        schedules1 = []
        for cycle in range(1, hui1.total_cycles + 1):
            due_date = calculate_next_due_date(hui1.start_date, hui1.cycle_type, cycle)
            schedule = HuiSchedule(
                hui_group_id=hui1.id,
                cycle_number=cycle,
                due_date=due_date,
                receiver_membership_id=memberships1[cycle-1].id if cycle <= len(memberships1) else None,
                total_collection=hui1.amount_per_cycle * hui1.total_members if cycle <= 2 else 0,
                owner_fee=(hui1.amount_per_cycle * hui1.total_members * hui1.fee_value / 100) if cycle <= 2 else 0,
                distribution_amount=(hui1.amount_per_cycle * hui1.total_members * (100 - hui1.fee_value) / 100) if cycle <= 2 else 0,
                is_completed=cycle <= 2,
                completed_at=due_date if cycle <= 2 else None
            )
            db.add(schedule)
            schedules1.append(schedule)
        
        db.commit()
        print(f"✓ Created {len(schedules1)} schedules for hui1")
        
        # Create payments for completed cycles
        print("\n💳 Creating payments...")
        payments_created = 0
        
        # Cycle 1 & 2 of hui1 - all paid
        for cycle in range(1, 3):
            schedule = schedules1[cycle - 1]
            for membership in memberships1:
                reference_code = generate_reference_code(hui1.id, membership.member_id, cycle)
                payment = Payment(
                    hui_group_id=hui1.id,
                    membership_id=membership.id,
                    schedule_id=schedule.id,
                    amount=hui1.amount_per_cycle,
                    payment_method=PaymentMethod.BANK_TRANSFER,
                    payment_status=PaymentStatus.VERIFIED,
                    reference_code=reference_code,
                    bank_transaction_ref=f"VCB{cycle}123456{membership.member_id[:6]}",
                    due_date=schedule.due_date,
                    paid_at=schedule.due_date - timedelta(days=1),
                    verified_at=schedule.due_date - timedelta(days=1),
                )
                db.add(payment)
                payments_created += 1
        
        # Cycle 3 of hui1 - some paid, some pending, some overdue
        schedule3 = schedules1[2]
        for i, membership in enumerate(memberships1):
            reference_code = generate_reference_code(hui1.id, membership.member_id, 3)
            
            if i < 6:  # First 6 paid
                payment = Payment(
                    hui_group_id=hui1.id,
                    membership_id=membership.id,
                    schedule_id=schedule3.id,
                    amount=hui1.amount_per_cycle,
                    payment_method=PaymentMethod.BANK_TRANSFER,
                    payment_status=PaymentStatus.VERIFIED,
                    reference_code=reference_code,
                    bank_transaction_ref=f"VCB3123456{membership.member_id[:6]}",
                    due_date=schedule3.due_date,
                    paid_at=datetime.now(timezone.utc) - timedelta(days=2),
                    verified_at=datetime.now(timezone.utc) - timedelta(days=2),
                )
            elif i < 8:  # Next 2 pending
                payment = Payment(
                    hui_group_id=hui1.id,
                    membership_id=membership.id,
                    schedule_id=schedule3.id,
                    amount=hui1.amount_per_cycle,
                    payment_method=PaymentMethod.QR_CODE,
                    payment_status=PaymentStatus.PENDING,
                    reference_code=reference_code,
                    due_date=schedule3.due_date,
                )
            else:  # Last 2 overdue
                payment = Payment(
                    hui_group_id=hui1.id,
                    membership_id=membership.id,
                    schedule_id=schedule3.id,
                    amount=hui1.amount_per_cycle,
                    payment_method=PaymentMethod.BANK_TRANSFER,
                    payment_status=PaymentStatus.OVERDUE,
                    reference_code=reference_code,
                    due_date=schedule3.due_date - timedelta(days=5),  # Overdue 5 days
                )
                # Update membership late stats
                membership.total_late_count += 1
                membership.total_late_amount += hui1.amount_per_cycle
                membership.credit_score -= 5
                if membership.credit_score < 70:
                    membership.risk_level = RiskLevel.MEDIUM
            
            db.add(payment)
            payments_created += 1
        
        db.commit()
        print(f"✓ Created {payments_created} payments")
        
        print("\n✅ Seed data creation completed successfully!")
        print("\n📊 Summary:")
        print(f"  - Users: 1 owner + 10 members")
        print(f"  - Hui Groups: 2 (1 monthly, 1 weekly)")
        print(f"  - Memberships: 18 total")
        print(f"  - Schedules: {len(schedules1)} for hui1")
        print(f"  - Payments: {payments_created} (verified, pending, overdue)")
        print("\n🔑 Login credentials:")
        print("  Owner: 0987654321 / 123456")
        print("  Members: 0901234567-0910123456 / 123456")
        
    except Exception as e:
        print(f"\n❌ Error creating seed data: {str(e)}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("🚀 Hui Manager Seed Data Script")
    print("=" * 50)
    
    # Confirm before running
    response = input("\n⚠️  This will create test data. Continue? (yes/no): ")
    if response.lower() == 'yes':
        create_seed_data()
    else:
        print("❌ Cancelled")
