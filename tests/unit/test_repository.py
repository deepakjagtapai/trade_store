from datetime import date, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import TradeORM
from app.repository import TradeRepository
from tests.conftest import TODAY


def make_trade(
    trade_id: str = "T1",
    version: int = 1,
    maturity: date = TODAY + timedelta(days=30),
    expired: bool = False,
) -> TradeORM:
    return TradeORM(
        trade_id=trade_id,
        version=version,
        counterparty_id="CP-1",
        portfolio_id="B1",
        maturity_date=maturity,
        created_date=TODAY,
        expired=expired,
    )


def test_trades_table_schema(session: Session) -> None:
    inspector = inspect(session.get_bind())

    assert inspector.get_pk_constraint("trades")["constrained_columns"] == ["trade_id", "version"]
    indexed = {col for ix in inspector.get_indexes("trades") for col in ix["column_names"]}
    assert {"trade_id", "maturity_date", "expired"} <= indexed


def test_repr_identifies_the_trade() -> None:
    assert repr(make_trade("T7", 3)).startswith("TradeORM(trade_id='T7', version=3")


def test_version_below_one_violates_db_constraint(repo: TradeRepository) -> None:
    with pytest.raises(IntegrityError):
        repo.add(make_trade(version=0))


def test_expired_defaults_to_false(repo: TradeRepository, session: Session) -> None:
    trade = make_trade()
    del trade.expired
    repo.add(trade)
    session.commit()

    stored = repo.get("T1", 1)
    assert stored is not None
    assert stored.expired is False


def test_add_and_get(repo: TradeRepository) -> None:
    repo.add(make_trade())

    stored = repo.get("T1", 1)

    assert stored is not None
    assert (stored.trade_id, stored.version, stored.counterparty_id) == ("T1", 1, "CP-1")


def test_add_duplicate_key_raises_integrity_error(repo: TradeRepository) -> None:
    repo.add(make_trade())

    with pytest.raises(IntegrityError):
        repo.add(make_trade())


def test_get_missing_returns_none(repo: TradeRepository) -> None:
    repo.add(make_trade())

    assert repo.get("T1", 2) is None
    assert repo.get("NOPE", 1) is None


def test_get_latest_returns_highest_version(repo: TradeRepository) -> None:
    for version in (2, 5, 1):
        repo.add(make_trade(version=version))
    repo.add(make_trade(trade_id="T2", version=9))

    latest = repo.get_latest("T1")

    assert latest is not None
    assert latest.version == 5


def test_get_latest_missing_returns_none(repo: TradeRepository) -> None:
    assert repo.get_latest("T1") is None


def test_get_max_version(repo: TradeRepository) -> None:
    assert repo.get_max_version("T1") is None

    repo.add(make_trade(version=3))
    repo.add(make_trade(version=1))
    repo.add(make_trade(trade_id="T2", version=7))

    assert repo.get_max_version("T1") == 3


def test_lock_trade_returns_existing_versions(repo: TradeRepository) -> None:
    assert repo.lock_trade("T1") == []

    repo.add(make_trade(version=2))
    repo.add(make_trade(version=1))

    assert repo.lock_trade("T1") == [1, 2]


def test_list_versions_ordered_ascending(repo: TradeRepository) -> None:
    for version in (3, 1, 2):
        repo.add(make_trade(version=version))
    repo.add(make_trade(trade_id="T2", version=1))

    assert [t.version for t in repo.list_versions("T1")] == [1, 2, 3]
    assert repo.list_versions("NOPE") == []


def test_list_all_with_expired_filter(repo: TradeRepository) -> None:
    repo.add(make_trade("T2", 1))
    repo.add(make_trade("T1", 2, expired=True))
    repo.add(make_trade("T1", 1))

    all_keys = [(t.trade_id, t.version) for t in repo.list_all()]
    expired_keys = [(t.trade_id, t.version) for t in repo.list_all(expired=True)]
    active_keys = [(t.trade_id, t.version) for t in repo.list_all(expired=False)]

    assert all_keys == [("T1", 1), ("T1", 2), ("T2", 1)]
    assert expired_keys == [("T1", 2)]
    assert active_keys == [("T1", 1), ("T2", 1)]


def test_mark_expired_only_touches_matured_unexpired_trades(
    repo: TradeRepository, session: Session
) -> None:
    repo.add(make_trade("PAST1", maturity=TODAY - timedelta(days=1)))
    repo.add(make_trade("PAST2", maturity=TODAY - timedelta(days=400)))
    repo.add(make_trade("ALREADY", maturity=TODAY - timedelta(days=10), expired=True))
    repo.add(make_trade("TODAY", maturity=TODAY))
    repo.add(make_trade("FUTURE", maturity=TODAY + timedelta(days=1)))
    session.commit()

    count = repo.mark_expired(TODAY)
    session.commit()
    session.expire_all()

    assert count == 2
    flags = {t.trade_id: t.expired for t in repo.list_all()}
    assert flags == {
        "ALREADY": True,
        "FUTURE": False,
        "PAST1": True,
        "PAST2": True,
        "TODAY": False,
    }


def test_mark_expired_is_idempotent(repo: TradeRepository, session: Session) -> None:
    repo.add(make_trade(maturity=TODAY - timedelta(days=1)))
    session.commit()

    assert repo.mark_expired(TODAY) == 1
    assert repo.mark_expired(TODAY) == 0


def test_mark_expired_with_no_trades_returns_zero(repo: TradeRepository) -> None:
    assert repo.mark_expired(TODAY) == 0


def test_delete_all_removes_every_trade_and_returns_the_count(
    repo: TradeRepository, session: Session
) -> None:
    repo.add(make_trade("T1", 1))
    repo.add(make_trade("T1", 2))
    repo.add(make_trade("T2", 1, expired=True))
    session.commit()

    assert repo.delete_all() == 3
    session.commit()

    assert repo.list_all() == []


def test_delete_all_on_empty_store_returns_zero(repo: TradeRepository) -> None:
    assert repo.delete_all() == 0
