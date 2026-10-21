from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

SessionLocal = sessionmaker[Session]


class Base(DeclarativeBase):
    pass


def create_db_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            # A single shared connection, otherwise each connection gets its own empty database.
            kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


def create_session_factory(engine: Engine) -> SessionLocal:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    session_factory: SessionLocal = request.app.state.session_factory
    with session_factory() as session:
        yield session
