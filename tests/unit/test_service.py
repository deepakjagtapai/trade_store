import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import FixedClock
from app.db import Base, create_db_engine, create_session_factory
from app.exceptions import (
    LowerVersionError,
    MaturityInPastError,
    TradeNotFoundError,
    TradeStoreError,
)
from app.models import TradeORM
from app.repository import TradeRepository
from app.schemas import TradeIn
from app.service import TradeService
from tests.conftest import TODAY

FUTURE = TODAY + timedelta(days=365)


def trade_in(
    trade_id: str = "T1",
    version: int = 1,
    maturity: date = FUTURE,
    counterparty: str = "CP-1",
    portfolio: str = "B1",
) -> TradeIn:
    return TradeIn(
        trade_id=trade_id,
        version=version,
        counterparty_id=counterparty,
        portfolio_id=portfolio,
        maturity_date=maturity,
    )


def all_rows(session: Session) -> list[tuple[Any, ...]]:
    session.expire_all()
    rows = session.scalars(select(TradeORM).order_by(TradeORM.trade_id, TradeORM.version))
    return [
        (
            t.trade_id,
            t.version,
            t.counterparty_id,
            t.portfolio_id,
            t.maturity_date,
            t.created_date,
            t.expired,
        )
        for t in rows
    ]


# --- Exceptions -------------------------------------------------------------------------


def test_exception_codes_and_messages() -> None:
    lower = LowerVersionError("T1", incoming_version=1, max_version=3)
    past = MaturityInPastError(date(2020, 5, 20), date(2025, 6, 15))
    missing = TradeNotFoundError("T9")

    assert all(isinstance(e, TradeStoreError) for e in (lower, past, missing))
    assert lower.error_code == "LOWER_VERSION"
    assert "T1" in lower.message
    assert "1" in lower.message
    assert "3" in lower.message
    assert past.error_code == "MATURITY_IN_PAST"
    assert "2020-05-20" in past.message
    assert "2025-06-15" in past.message
    assert missing.error_code == "TRADE_NOT_FOUND"
    assert "T9" in str(missing)


# --- Rule 2: maturity -------------------------------------------------------------------


def test_new_trade_with_future_maturity_is_stored(service: TradeService, session: Session) -> None:
    stored = service.store_trade(trade_in())

    assert (stored.trade_id, stored.version) == ("T1", 1)
    assert stored.created_date == TODAY
    assert stored.expired is False
    assert all_rows(session) == [("T1", 1, "CP-1", "B1", FUTURE, TODAY, False)]


def test_maturity_equal_to_today_is_accepted(service: TradeService) -> None:
    stored = service.store_trade(trade_in(maturity=TODAY))

    assert stored.maturity_date == TODAY
    assert stored.expired is False


def test_maturity_yesterday_is_rejected_and_nothing_stored(
    service: TradeService, session: Session
) -> None:
    yesterday = TODAY - timedelta(days=1)

    with pytest.raises(MaturityInPastError) as exc_info:
        service.store_trade(trade_in(maturity=yesterday))

    assert exc_info.value.maturity_date == yesterday
    assert exc_info.value.today == TODAY
    assert all_rows(session) == []


def test_maturity_check_happens_before_version_check(
    service: TradeService, session: Session
) -> None:
    service.store_trade(trade_in(version=3))
    before = all_rows(session)

    with pytest.raises(MaturityInPastError):
        service.store_trade(trade_in(version=1, maturity=TODAY - timedelta(days=1)))

    assert all_rows(session) == before


# --- Rule 1: versions -------------------------------------------------------------------


def test_higher_version_is_stored_as_new_record(service: TradeService, session: Session) -> None:
    service.store_trade(trade_in(version=1, counterparty="CP-1"))

    stored = service.store_trade(trade_in(version=2, counterparty="CP-2"))

    assert stored.version == 2
    assert [(r[1], r[2]) for r in all_rows(session)] == [(1, "CP-1"), (2, "CP-2")]


def test_same_version_replaces_existing_record(
    service: TradeService, session: Session, clock: FixedClock
) -> None:
    service.store_trade(trade_in(version=1))
    new_maturity = FUTURE + timedelta(days=10)
    clock.set(TODAY + timedelta(days=1))

    stored = service.store_trade(
        trade_in(version=1, counterparty="CP-9", portfolio="B2", maturity=new_maturity)
    )

    assert stored.counterparty_id == "CP-9"
    assert all_rows(session) == [
        ("T1", 1, "CP-9", "B2", new_maturity, TODAY + timedelta(days=1), False)
    ]


def test_same_version_replace_resets_expired_flag(
    service: TradeService, session: Session, clock: FixedClock
) -> None:
    service.store_trade(trade_in(maturity=TODAY))
    clock.set(TODAY + timedelta(days=1))
    service.expire_matured_trades()

    stored = service.store_trade(trade_in(maturity=FUTURE))

    assert stored.expired is False
    assert all_rows(session)[0][-1] is False


def test_same_version_as_max_is_replaced_when_history_exists(
    service: TradeService, session: Session
) -> None:
    service.store_trade(trade_in(version=1))
    service.store_trade(trade_in(version=2))

    service.store_trade(trade_in(version=2, portfolio="B2"))

    assert [(r[1], r[3]) for r in all_rows(session)] == [(1, "B1"), (2, "B2")]


def test_lower_version_is_rejected_and_nothing_changes(
    service: TradeService, session: Session
) -> None:
    service.store_trade(trade_in(version=1))
    service.store_trade(trade_in(version=2))
    before = all_rows(session)

    with pytest.raises(LowerVersionError) as exc_info:
        service.store_trade(trade_in(version=1, counterparty="CP-X"))

    assert exc_info.value.trade_id == "T1"
    assert exc_info.value.incoming_version == 1
    assert exc_info.value.max_version == 2
    assert all_rows(session) == before


def test_lower_version_rejected_even_when_intermediate_version_missing(
    service: TradeService, session: Session
) -> None:
    service.store_trade(trade_in(version=3))

    with pytest.raises(LowerVersionError):
        service.store_trade(trade_in(version=2))

    assert [r[1] for r in all_rows(session)] == [3]


def test_versions_are_tracked_per_trade_id(service: TradeService) -> None:
    service.store_trade(trade_in("T1", version=5))

    stored = service.store_trade(trade_in("T2", version=1))

    assert (stored.trade_id, stored.version) == ("T2", 1)


def test_rows_are_locked_before_reading_max_version(
    session: Session, repo: TradeRepository, clock: FixedClock
) -> None:
    spy = MagicMock(wraps=repo)
    TradeService(session, spy, clock).store_trade(trade_in())

    called = [c[0] for c in spy.method_calls]
    assert called.index("lock_trade") < called.index("get_max_version")
    spy.lock_trade.assert_called_once_with("T1")


# --- Transactions -----------------------------------------------------------------------


def test_failed_store_rolls_back(
    session: Session, repo: TradeRepository, clock: FixedClock
) -> None:
    service = TradeService(session, repo, clock)
    service.store_trade(trade_in("T1"))
    before = all_rows(session)

    with (
        patch.object(repo, "add", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        service.store_trade(trade_in("T2"))

    assert all_rows(session) == before
    assert service.store_trade(trade_in("T3")).trade_id == "T3"


def test_failed_replace_rolls_back_in_memory_changes(
    session: Session, repo: TradeRepository, clock: FixedClock
) -> None:
    service = TradeService(session, repo, clock)
    service.store_trade(trade_in(portfolio="B1"))

    with (
        patch.object(session, "commit", side_effect=RuntimeError("db down")),
        pytest.raises(RuntimeError),
    ):
        service.store_trade(trade_in(portfolio="B2"))

    assert all_rows(session)[0][3] == "B1"


def test_concurrent_insert_of_same_key_is_retried_once(tmp_path: Path) -> None:
    """Another writer inserts the same (trade_id, version) between our check and our insert."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'race.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    clock = FixedClock(TODAY)

    class RacingRepository(TradeRepository):
        raced = False

        def add(self, trade: TradeORM) -> TradeORM:
            if not self.raced:
                self.raced = True
                with factory() as competitor:
                    TradeService(competitor, TradeRepository(competitor), clock).store_trade(
                        trade_in(counterparty="CP-COMPETITOR")
                    )
            return super().add(trade)

    with factory() as session:
        repo = RacingRepository(session)
        stored = TradeService(session, repo, clock).store_trade(trade_in(counterparty="CP-MINE"))

        assert repo.raced
        assert stored.counterparty_id == "CP-MINE"
        assert [(r[1], r[2]) for r in all_rows(session)] == [(1, "CP-MINE")]
    engine.dispose()


def test_integrity_error_on_retry_propagates(
    session: Session, repo: TradeRepository, clock: FixedClock
) -> None:
    error = IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    with (
        patch.object(repo, "add", side_effect=[error, error]) as add,
        pytest.raises(IntegrityError),
    ):
        TradeService(session, repo, clock).store_trade(trade_in())

    assert add.call_count == 2
    assert all_rows(session) == []


# --- Logging ----------------------------------------------------------------------------


def test_accepted_and_rejected_trades_are_logged(
    service: TradeService, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.service")

    service.store_trade(trade_in(version=2))
    service.store_trade(trade_in(version=2))
    with pytest.raises(LowerVersionError):
        service.store_trade(trade_in(version=1))
    with pytest.raises(MaturityInPastError):
        service.store_trade(trade_in(version=3, maturity=TODAY - timedelta(days=1)))

    outcomes = [
        (r.trade_id, r.version, r.outcome, getattr(r, "error_code", None))
        for r in caplog.records
        if hasattr(r, "outcome")
    ]
    assert outcomes == [
        ("T1", 2, "inserted", None),
        ("T1", 2, "replaced", None),
        ("T1", 1, "rejected", "LOWER_VERSION"),
        ("T1", 3, "rejected", "MATURITY_IN_PAST"),
    ]


# --- Queries ----------------------------------------------------------------------------


def test_get_latest_returns_highest_version(service: TradeService) -> None:
    service.store_trade(trade_in(version=1))
    service.store_trade(trade_in(version=4))

    assert service.get_latest("T1").version == 4


def test_get_latest_missing_raises(service: TradeService) -> None:
    with pytest.raises(TradeNotFoundError):
        service.get_latest("NOPE")


def test_get_versions_ascending(service: TradeService) -> None:
    for version in (1, 3):
        service.store_trade(trade_in(version=version))

    assert [t.version for t in service.get_versions("T1")] == [1, 3]


def test_get_versions_missing_raises(service: TradeService) -> None:
    with pytest.raises(TradeNotFoundError):
        service.get_versions("NOPE")


def test_list_trades_with_filter(service: TradeService, clock: FixedClock) -> None:
    service.store_trade(trade_in("T1", maturity=TODAY))
    service.store_trade(trade_in("T2", maturity=FUTURE))
    clock.set(TODAY + timedelta(days=1))
    service.expire_matured_trades()

    assert [t.trade_id for t in service.list_trades(None)] == ["T1", "T2"]
    assert [t.trade_id for t in service.list_trades(expired=True)] == ["T1"]
    assert [t.trade_id for t in service.list_trades(expired=False)] == ["T2"]


# --- Rule 3: expiry ---------------------------------------------------------------------


def test_expire_matured_trades_marks_only_past_maturities_and_is_idempotent(
    service: TradeService, clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    service.store_trade(trade_in("PAST", maturity=TODAY + timedelta(days=1)))
    service.store_trade(trade_in("TODAY", maturity=TODAY + timedelta(days=2)))
    service.store_trade(trade_in("FUTURE", maturity=TODAY + timedelta(days=3)))
    clock.set(TODAY + timedelta(days=2))
    caplog.set_level(logging.INFO, logger="app.service")

    assert service.expire_matured_trades() == 1
    assert service.expire_matured_trades() == 0

    flags = {t.trade_id: t.expired for t in service.list_trades(None)}
    assert flags == {"PAST": True, "TODAY": False, "FUTURE": False}
    sweeps = [r for r in caplog.records if hasattr(r, "expired_count")]
    assert [(r.run_date, r.expired_count) for r in sweeps] == [
        ((TODAY + timedelta(days=2)).isoformat(), 1),
        ((TODAY + timedelta(days=2)).isoformat(), 0),
    ]


def test_failed_expiry_sweep_rolls_back(
    session: Session, repo: TradeRepository, clock: FixedClock
) -> None:
    service = TradeService(session, repo, clock)
    service.store_trade(trade_in(maturity=TODAY))
    clock.set(TODAY + timedelta(days=1))

    with (
        patch.object(session, "commit", side_effect=RuntimeError("db down")),
        pytest.raises(RuntimeError),
    ):
        service.expire_matured_trades()

    assert all_rows(session)[0][-1] is False


def test_advancing_clock_past_maturity_then_sweeping_expires_trade(
    service: TradeService, clock: FixedClock
) -> None:
    service.store_trade(trade_in(maturity=TODAY))
    assert service.expire_matured_trades() == 0
    assert service.get_latest("T1").expired is False

    clock.set(TODAY + timedelta(days=1))

    assert service.expire_matured_trades() == 1
    assert service.get_latest("T1").expired is True


# --- Demo-mode reset --------------------------------------------------------------------


def test_reset_store_deletes_everything_and_logs(
    service: TradeService, session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    service.store_trade(trade_in("T1", version=1))
    service.store_trade(trade_in("T1", version=2))
    service.store_trade(trade_in("T2"))
    caplog.set_level(logging.INFO, logger="app.service")

    assert service.reset_store() == 3

    assert all_rows(session) == []
    resets = [r for r in caplog.records if hasattr(r, "deleted_count")]
    assert [(r.levelno, r.deleted_count) for r in resets] == [(logging.WARNING, 3)]


def test_failed_reset_rolls_back(
    session: Session, repo: TradeRepository, clock: FixedClock
) -> None:
    service = TradeService(session, repo, clock)
    service.store_trade(trade_in())

    with (
        patch.object(session, "commit", side_effect=RuntimeError("db down")),
        pytest.raises(RuntimeError),
    ):
        service.reset_store()

    assert len(all_rows(session)) == 1
