import threading
from datetime import timedelta
from unittest.mock import ANY, MagicMock, patch

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.clock import FixedClock
from app.config import Settings
from app.db import create_session_factory
from app.main import create_app
from app.models import TradeORM
from app.scheduler import (
    EXPIRY_JOB_ID,
    STARTUP_JOB_ID,
    create_scheduler,
    run_expiry_job,
    start_scheduler,
)
from tests.conftest import TODAY


def cron_fields(trigger: CronTrigger) -> dict[str, str]:
    return {field.name: str(field) for field in trigger.fields}


@pytest.mark.parametrize("hour", [0, 3, 23])
def test_daily_job_is_registered_at_configured_hour(engine: Engine, hour: int) -> None:
    settings = Settings(enable_scheduler=True, expiry_cron_hour=hour)

    scheduler = create_scheduler(settings, create_session_factory(engine), FixedClock(TODAY))

    job = scheduler.get_job(EXPIRY_JOB_ID)
    assert job is not None
    assert job.func is run_expiry_job
    assert isinstance(job.trigger, CronTrigger)
    fields = cron_fields(job.trigger)
    assert (fields["hour"], fields["minute"], fields["day"]) == (str(hour), "0", "*")


def test_startup_sweep_job_is_registered(engine: Engine) -> None:
    scheduler = create_scheduler(Settings(), create_session_factory(engine), FixedClock(TODAY))

    job = scheduler.get_job(STARTUP_JOB_ID)
    assert job is not None
    assert job.func is run_expiry_job
    assert isinstance(job.trigger, DateTrigger)


def test_job_function_calls_expire_matured_trades() -> None:
    session = MagicMock()
    session_factory = MagicMock(return_value=session)
    clock = FixedClock(TODAY)

    with patch("app.scheduler.TradeService") as service_cls:
        service_cls.return_value.expire_matured_trades.return_value = 7

        result = run_expiry_job(session_factory, clock)

    assert result == 7
    service_cls.assert_called_once_with(session.__enter__.return_value, ANY, clock)
    service_cls.return_value.expire_matured_trades.assert_called_once_with()
    session.__exit__.assert_called_once()


def test_job_function_expires_trades_in_the_database(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(
            TradeORM(
                trade_id="T1",
                version=1,
                counterparty_id="CP-1",
                portfolio_id="B1",
                maturity_date=TODAY - timedelta(days=1),
                created_date=TODAY - timedelta(days=10),
            )
        )
        session.commit()

    assert run_expiry_job(factory, FixedClock(TODAY)) == 1

    with factory() as session:
        stored = session.get(TradeORM, ("T1", 1))
        assert stored is not None
        assert stored.expired is True


def test_scheduler_not_started_when_disabled(engine: Engine) -> None:
    with patch("app.scheduler.BackgroundScheduler") as scheduler_cls:
        result = start_scheduler(
            Settings(enable_scheduler=False), create_session_factory(engine), FixedClock(TODAY)
        )

    assert result is None
    scheduler_cls.assert_not_called()


def test_enabled_scheduler_starts_and_runs_startup_sweep(engine: Engine) -> None:
    ran = threading.Event()

    with patch("app.scheduler.run_expiry_job", side_effect=lambda *_: ran.set()):
        scheduler = start_scheduler(
            Settings(enable_scheduler=True), create_session_factory(engine), FixedClock(TODAY)
        )
        assert scheduler is not None
        try:
            assert scheduler.running
            assert ran.wait(timeout=5), "startup sweep did not run"
            assert scheduler.get_job(EXPIRY_JOB_ID) is not None
        finally:
            scheduler.shutdown(wait=True)


def test_lifespan_starts_and_stops_scheduler() -> None:
    settings = Settings(database_url="sqlite://", enable_scheduler=True)

    with patch("app.main.start_scheduler") as start:
        app = create_app(settings)
        with TestClient(app):
            start.assert_called_once_with(settings, app.state.session_factory, app.state.clock)
            start.return_value.shutdown.assert_not_called()

    start.return_value.shutdown.assert_called_once_with(wait=False)


def test_lifespan_with_scheduler_disabled_starts_nothing() -> None:
    settings = Settings(database_url="sqlite://", enable_scheduler=False)

    with (
        patch("app.scheduler.BackgroundScheduler") as scheduler_cls,
        TestClient(create_app(settings)) as client,
    ):
        assert client.get("/health").status_code == 200

    scheduler_cls.assert_not_called()
