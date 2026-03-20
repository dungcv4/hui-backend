"""
Scheduled Jobs Service for HuiManager
Handles automatic sending of reminders and reports at configured times
"""
import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = None


def get_scheduler():
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler(timezone='Asia/Ho_Chi_Minh')
    return scheduler


async def send_morning_reminders():
    """Send morning bill reminders (Type 1)"""
    logger.info("Starting morning reminder job...")
    try:
        from database import SessionLocal
        from sqlalchemy import text
        from config import settings
        from telegram_service import send_payment_reminder
        
        if not settings.telegram_bot_token:
            logger.warning("Telegram bot not configured, skipping reminders")
            return
        
        db = SessionLocal()
        
        # Check if reminder is enabled
        enabled = db.execute(text(
            "SELECT setting_value FROM telegram_settings WHERE setting_key = 'reminder_1_enabled'"
        )).scalar()
        
        if enabled != 'true':
            logger.info("Morning reminders disabled, skipping")
            db.close()
            return
        
        # Get pending payments for today with Telegram linked members
        result = db.execute(text("""
            SELECT 
                p.id as payment_id,
                p.amount,
                m.telegram_chat_id,
                m.name as member_name,
                hg.name as hui_group_name,
                hg.total_cycles,
                hg.bank_name,
                hg.bank_account_number,
                hg.bank_account_name,
                hs.cycle_number,
                hs.due_date,
                hm.payment_code
            FROM payments p
            JOIN hui_memberships hm ON p.membership_id = hm.id
            JOIN members m ON hm.member_id = m.id
            JOIN hui_groups hg ON p.hui_group_id = hg.id
            JOIN hui_schedules hs ON p.schedule_id = hs.id
            WHERE p.payment_status = 'PENDING'
            AND DATE(hs.due_date) = CURDATE()
            AND m.telegram_chat_id IS NOT NULL
        """))
        
        sent_count = 0
        for row in result:
            try:
                due_date = row[10] if row[10] else None
                await send_payment_reminder(
                    chat_id=row[2],
                    member_name=row[3],
                    hui_group_name=row[4],
                    cycle_number=row[9],
                    total_cycles=row[5],
                    amount=float(row[1]),
                    due_date=due_date,
                    payment_code=row[11],
                    bank_name=row[6],
                    bank_account=row[7],
                    bank_account_name=row[8],
                    reminder_type=1
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send reminder to {row[3]}: {e}")
        
        logger.info(f"Morning reminders sent: {sent_count}")
        db.close()
        
    except Exception as e:
        logger.error(f"Error in morning reminder job: {e}")


async def send_afternoon_reminders():
    """Send afternoon follow-up reminders (Type 2) - only to unpaid"""
    logger.info("Starting afternoon reminder job...")
    try:
        from database import SessionLocal
        from sqlalchemy import text
        from config import settings
        from telegram_service import send_payment_reminder
        
        if not settings.telegram_bot_token:
            return
        
        db = SessionLocal()
        
        enabled = db.execute(text(
            "SELECT setting_value FROM telegram_settings WHERE setting_key = 'reminder_2_enabled'"
        )).scalar()
        
        if enabled != 'true':
            logger.info("Afternoon reminders disabled, skipping")
            db.close()
            return
        
        # Same query but only pending payments - using members table
        result = db.execute(text("""
            SELECT 
                p.id as payment_id,
                p.amount,
                m.telegram_chat_id,
                m.name as member_name,
                hg.name as hui_group_name,
                hg.total_cycles,
                hg.bank_name,
                hg.bank_account_number,
                hg.bank_account_name,
                hs.cycle_number,
                hs.due_date,
                hm.payment_code
            FROM payments p
            JOIN hui_memberships hm ON p.membership_id = hm.id
            JOIN members m ON hm.member_id = m.id
            JOIN hui_groups hg ON p.hui_group_id = hg.id
            JOIN hui_schedules hs ON p.schedule_id = hs.id
            WHERE p.payment_status = 'PENDING'
            AND DATE(hs.due_date) = CURDATE()
            AND m.telegram_chat_id IS NOT NULL
        """))
        
        sent_count = 0
        for row in result:
            try:
                due_date = row[10] if row[10] else None
                await send_payment_reminder(
                    chat_id=row[2],
                    member_name=row[3],
                    hui_group_name=row[4],
                    cycle_number=row[9],
                    total_cycles=row[5],
                    amount=float(row[1]),
                    due_date=due_date,
                    payment_code=row[11],
                    bank_name=row[6],
                    bank_account=row[7],
                    bank_account_name=row[8],
                    reminder_type=2  # Afternoon type
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send reminder to {row[3]}: {e}")
        
        logger.info(f"Afternoon reminders sent: {sent_count}")
        db.close()
        
    except Exception as e:
        logger.error(f"Error in afternoon reminder job: {e}")


async def send_evening_reminders():
    """Send evening final reminders (Type 3) - only to unpaid"""
    logger.info("Starting evening reminder job...")
    try:
        from database import SessionLocal
        from sqlalchemy import text
        from config import settings
        from telegram_service import send_payment_reminder
        
        if not settings.telegram_bot_token:
            return
        
        db = SessionLocal()
        
        enabled = db.execute(text(
            "SELECT setting_value FROM telegram_settings WHERE setting_key = 'reminder_3_enabled'"
        )).scalar()
        
        if enabled != 'true':
            logger.info("Evening reminders disabled, skipping")
            db.close()
            return
        
        result = db.execute(text("""
            SELECT 
                p.id as payment_id,
                p.amount,
                m.telegram_chat_id,
                m.name as member_name,
                hg.name as hui_group_name,
                hg.total_cycles,
                hg.bank_name,
                hg.bank_account_number,
                hg.bank_account_name,
                hs.cycle_number,
                hs.due_date,
                hm.payment_code
            FROM payments p
            JOIN hui_memberships hm ON p.membership_id = hm.id
            JOIN members m ON hm.member_id = m.id
            JOIN hui_groups hg ON p.hui_group_id = hg.id
            JOIN hui_schedules hs ON p.schedule_id = hs.id
            WHERE p.payment_status = 'PENDING'
            AND DATE(hs.due_date) = CURDATE()
            AND m.telegram_chat_id IS NOT NULL
        """))
        
        sent_count = 0
        for row in result:
            try:
                due_date = row[10] if row[10] else None
                await send_payment_reminder(
                    chat_id=row[2],
                    member_name=row[3],
                    hui_group_name=row[4],
                    cycle_number=row[9],
                    total_cycles=row[5],
                    amount=float(row[1]),
                    due_date=due_date,
                    payment_code=row[11],
                    bank_name=row[6],
                    bank_account=row[7],
                    bank_account_name=row[8],
                    reminder_type=3  # Evening type
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send reminder to {row[3]}: {e}")
        
        logger.info(f"Evening reminders sent: {sent_count}")
        db.close()
        
    except Exception as e:
        logger.error(f"Error in evening reminder job: {e}")


async def check_overdue_payments():
    """Midnight job: quét payments quá hạn → tạo DebtRecord + tính phạt"""
    logger.info("Starting overdue payment check job...")
    try:
        from database import SessionLocal
        from models import User
        from routers.debt import _process_overdue_payments
        
        db = SessionLocal()
        
        # Quét cho tất cả owners
        owners = db.query(User).filter(User.is_active == True, User.role == "owner").all()
        
        total_new = 0
        total_updated = 0
        total_overdue = 0
        
        for owner in owners:
            try:
                result = _process_overdue_payments(db, owner.id)
                total_new += result.get("new_debts", 0)
                total_updated += result.get("updated_debts", 0)
                total_overdue += result.get("overdue_marked", 0)
            except Exception as e:
                logger.error(f"Error processing overdue for owner {owner.id}: {e}")
        
        db.close()
        logger.info(f"Overdue check complete: {total_new} new debts, {total_updated} updated, {total_overdue} marked overdue")
        
    except Exception as e:
        logger.error(f"Error in overdue check job: {e}")


def setup_scheduled_jobs():
    """Setup all scheduled jobs based on settings"""
    from database import SessionLocal
    from sqlalchemy import text
    
    try:
        db = SessionLocal()
        
        # Get settings
        settings_result = db.execute(text("SELECT setting_key, setting_value FROM telegram_settings"))
        settings = {row[0]: row[1] for row in settings_result}
        
        db.close()
        
        # Get configured times
        time_1 = settings.get('reminder_time_1', '08:00')
        time_2 = settings.get('reminder_time_2', '16:00')
        time_3 = settings.get('reminder_time_3', '21:00')
        
        sched = get_scheduler()
        
        # Remove existing jobs
        sched.remove_all_jobs()
        
        # Add midnight overdue check job
        sched.add_job(
            check_overdue_payments,
            CronTrigger(hour=0, minute=5),
            id='overdue_check',
            replace_existing=True
        )
        logger.info("Scheduled overdue check at 00:05")
        
        # Add morning reminder job
        hour_1, minute_1 = map(int, time_1.split(':'))
        sched.add_job(
            send_morning_reminders,
            CronTrigger(hour=hour_1, minute=minute_1),
            id='morning_reminder',
            replace_existing=True
        )
        logger.info(f"Scheduled morning reminder at {time_1}")
        
        # Add afternoon reminder job
        hour_2, minute_2 = map(int, time_2.split(':'))
        sched.add_job(
            send_afternoon_reminders,
            CronTrigger(hour=hour_2, minute=minute_2),
            id='afternoon_reminder',
            replace_existing=True
        )
        logger.info(f"Scheduled afternoon reminder at {time_2}")
        
        # Add evening reminder job
        hour_3, minute_3 = map(int, time_3.split(':'))
        sched.add_job(
            send_evening_reminders,
            CronTrigger(hour=hour_3, minute=minute_3),
            id='evening_reminder',
            replace_existing=True
        )
        logger.info(f"Scheduled evening reminder at {time_3}")
        
        # Start scheduler if not running
        if not sched.running:
            sched.start()
            logger.info("Scheduler started")
        
    except Exception as e:
        logger.error(f"Error setting up scheduled jobs: {e}")


def get_scheduled_jobs_info():
    """Get info about currently scheduled jobs"""
    sched = get_scheduler()
    jobs = []
    for job in sched.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        })
    return {
        "running": sched.running,
        "jobs": jobs
    }
