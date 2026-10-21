from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.dependencies import get_clock
from app.clock import FixedClock
from app.config import Settings
from app.db import Base, create_db_engine, create_session_factory, get_session
from app.main import create_app
from app.repository import TradeRepository
from app.service import TradeService

TODAY = date(2025, 6, 15)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="run browser tests (needs requirements-e2e.txt and Chrome)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-e2e"):
        return
    skip = pytest.mark.skip(reason="browser test: pass --run-e2e (see `make e2e`)")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with create_session_factory(engine)() as session:
        yield session


@pytest.fixture
def repo(session: Session) -> TradeRepository:
    return TradeRepository(session)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(TODAY)


@pytest.fixture
def service(session: Session, repo: TradeRepository, clock: FixedClock) -> TradeService:
    return TradeService(session, repo, clock)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(database_url="sqlite://", enable_scheduler=False)


@pytest.fixture
def client(session: Session, clock: FixedClock, test_settings: Settings) -> Iterator[TestClient]:
    app = create_app(test_settings)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_clock] = lambda: clock
    with TestClient(app) as test_client:
        yield test_client
