import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.clock import Clock
from app.config import Settings
from app.db import SessionLocal
from app.repository import TradeRepository
from app.service import TradeService

logger = logging.getLogger(__name__)

EXPIRY_JOB_ID = "expire-matured-trades"
STARTUP_JOB_ID = "expire-matured-trades-startup"


def run_expiry_job(session_factory: SessionLocal, clock: Clock) -> int:
    with session_factory() as session:
        return TradeService(session, TradeRepository(session), clock).expire_matured_trades()


def create_scheduler(
    settings: Settings, session_factory: SessionLocal, clock: Clock
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    job_options: dict[str, Any] = {
        "args": (session_factory, clock),
        "max_instances": 1,
        "coalesce": True,
        "replace_existing": True,
    }
    scheduler.add_job(
        run_expiry_job,
        CronTrigger(hour=settings.expiry_cron_hour, minute=0),
        id=EXPIRY_JOB_ID,
        misfire_grace_time=3600,
        **job_options,
    )
    # A DateTrigger without run_date fires immediately once the scheduler starts.
    scheduler.add_job(run_expiry_job, DateTrigger(), id=STARTUP_JOB_ID, **job_options)
    return scheduler


def start_scheduler(
    settings: Settings, session_factory: SessionLocal, clock: Clock
) -> BackgroundScheduler | None:
    if not settings.enable_scheduler:
        logger.info("Expiry scheduler disabled (ENABLE_SCHEDULER=false)")
        return None
    scheduler = create_scheduler(settings, session_factory, clock)
    scheduler.start()
    logger.info(
        "Expiry scheduler started: startup sweep queued, daily sweep at %02d:00",
        settings.expiry_cron_hour,
    )
    return scheduler
