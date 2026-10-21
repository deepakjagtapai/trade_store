from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.pool import StaticPool

from app.db import create_db_engine
from app.repository import TradeRepository


def test_in_memory_sqlite_uses_a_single_shared_connection() -> None:
    with patch("app.db.create_engine") as create_engine:
        create_db_engine("sqlite://")

    kwargs = create_engine.call_args.kwargs
    assert kwargs["poolclass"] is StaticPool
    assert kwargs["connect_args"] == {"check_same_thread": False}


def test_file_sqlite_allows_cross_thread_use_without_static_pool() -> None:
    with patch("app.db.create_engine") as create_engine:
        create_db_engine("sqlite:///./trade_store.db")

    kwargs = create_engine.call_args.kwargs
    assert "poolclass" not in kwargs
    assert kwargs["connect_args"] == {"check_same_thread": False}


def test_postgres_url_gets_no_sqlite_specific_options() -> None:
    url = "postgresql+psycopg://user:pw@db:5432/trades"
    with patch("app.db.create_engine") as create_engine:
        create_db_engine(url)

    create_engine.assert_called_once_with(url, pool_pre_ping=True)


def test_lock_trade_emits_select_for_update_on_postgres_only() -> None:
    session = MagicMock()
    session.scalars.return_value = []
    TradeRepository(session).lock_trade("T1")
    stmt = session.scalars.call_args.args[0]

    assert "FOR UPDATE" in str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(stmt.compile(dialect=sqlite.dialect()))
