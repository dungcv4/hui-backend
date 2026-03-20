from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import logging
import os
from config import settings
from database import engine
import models

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create tables
models.Base.metadata.create_all(bind=engine)

# FastAPI app
app = FastAPI(
    title="Hui Management System",
    description="Hệ thống quản lý hụi",
    version="1.0.0"
)

# CORS middleware
allowed_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
from routers import (
    auth, users, hui_groups, schedules, memberships, payments,
    dashboard, webhooks, batch_payments, telegram, telegram_bot,
    telegram_messages, bill_sender, notifications, settings as app_settings,
    bills, payouts, qr_payments, slot_transfer, announce, exports, system_admin,
    members, debt,  # Members + Debt router
    customer_auth, customer_portal  # Customer portal
)

# Prefix /api for all routers
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(members.router, prefix="/api")  # New: Members router
app.include_router(hui_groups.router, prefix="/api")  # Hui Groups - prefix defined in router is /hui-groups
app.include_router(schedules.router, prefix="/api")
app.include_router(memberships.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")
app.include_router(batch_payments.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")
app.include_router(telegram_bot.router, prefix="/api")
app.include_router(telegram_messages.router, prefix="/api")
app.include_router(bill_sender.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(app_settings.router, prefix="/api")
app.include_router(bills.router, prefix="/api")
app.include_router(payouts.router, prefix="/api")
app.include_router(qr_payments.router, prefix="/api")
app.include_router(slot_transfer.router, prefix="/api")
app.include_router(announce.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(system_admin.router, prefix="/api")
app.include_router(debt.router, prefix="/api")

# Customer Portal (member-facing)
app.include_router(customer_auth.router, prefix="/api")
app.include_router(customer_portal.router, prefix="/api")

# Startup event - initialize scheduler
@app.on_event("startup")
async def startup_event():
    try:
        from scheduler_service import setup_scheduled_jobs
        setup_scheduled_jobs()
        logger.info("Scheduler initialized on startup")
    except Exception as e:
        logger.error(f"Failed to initialize scheduler: {e}")

# Health check
@app.get("/health")
def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)