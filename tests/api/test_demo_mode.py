import logging
from collections.abc import Iterator
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.clock import SwitchableClock, SystemClock
from app.config import Settings
from app.main import create_app


def demo_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "sqlite://",
        "enable_scheduler": False,
        "demo_mode": True,
        **overrides,
    }
    return Settings(**values)


@pytest.fixture
def demo_client() -> Iterator[TestClient]:
    with TestClient(create_app(demo_settings())) as client:
        yield client


def trade(trade_id: str = "T1", version: int = 1, maturity: str = "2021-05-20") -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "version": version,
        "counterparty_id": "CP-1",
        "portfolio_id": "B1",
        "maturity_date": maturity,
    }


def real_today() -> str:
    return SystemClock().today().isoformat()


# --- Demo mode off ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/admin/clock", None),
        ("PUT", "/admin/clock", {"today": "2015-03-14"}),
        ("DELETE", "/admin/clock", None),
        ("DELETE", "/admin/trades", None),
    ],
)
def test_demo_routes_do_not_exist_when_demo_mode_is_off(
    client: TestClient, method: str, path: str, json: dict[str, str] | None
) -> None:
    assert client.request(method, path, json=json).status_code == 404


def test_demo_routes_are_absent_from_openapi_when_demo_mode_is_off(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/admin/clock" not in paths
    assert "/admin/trades" not in paths


def test_non_demo_app_uses_the_system_clock() -> None:
    app = create_app(Settings(database_url="sqlite://", enable_scheduler=False))

    assert type(app.state.clock) is SystemClock


# --- Demo mode on: wiring ---------------------------------------------------------------


def test_demo_app_uses_a_switchable_clock_shared_with_the_scheduler() -> None:
    settings = demo_settings(enable_scheduler=True)

    with patch("app.main.start_scheduler") as start:
        app = create_app(settings)
        with TestClient(app):
            pass

    assert isinstance(app.state.clock, SwitchableClock)
    start.assert_called_once_with(settings, app.state.session_factory, app.state.clock)


def test_demo_mode_logs_a_warning_on_startup(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.main")

    with TestClient(create_app(demo_settings())):
        pass

    warnings = [r for r in caplog.records if r.name == "app.main"]
    assert [r.levelno for r in warnings] == [logging.WARNING]
    assert "DEMO_MODE" in warnings[0].getMessage()


def test_no_demo_warning_when_demo_mode_is_off(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.main")

    with TestClient(create_app(Settings(database_url="sqlite://", enable_scheduler=False))):
        pass

    assert [r for r in caplog.records if r.name == "app.main"] == []


# --- /admin/clock -----------------------------------------------------------------------


def test_get_clock_initially_reports_the_real_date(demo_client: TestClient) -> None:
    response = demo_client.get("/admin/clock")

    assert response.status_code == 200
    assert response.json() == {"today": real_today(), "overridden": False, "demo_mode": True}


@pytest.mark.parametrize("raw", ["2015-03-14", "14/03/2015"])
def test_put_clock_sets_the_simulated_date(demo_client: TestClient, raw: str) -> None:
    response = demo_client.put("/admin/clock", json={"today": raw})

    expected = {"today": "2015-03-14", "overridden": True, "demo_mode": True}
    assert response.status_code == 200
    assert response.json() == expected
    assert demo_client.get("/admin/clock").json() == expected


@pytest.mark.parametrize(
    "body", [{"today": "2015-02-30"}, {"today": "not-a-date"}, {}, {"today": "2015-03-14", "x": 1}]
)
def test_put_clock_with_invalid_body_returns_422(
    demo_client: TestClient, body: dict[str, Any]
) -> None:
    assert demo_client.put("/admin/clock", json=body).status_code == 422
    assert demo_client.get("/admin/clock").json()["overridden"] is False


def test_delete_clock_restores_the_real_date(demo_client: TestClient) -> None:
    demo_client.put("/admin/clock", json={"today": "2015-03-14"})

    response = demo_client.delete("/admin/clock")

    assert response.status_code == 200
    assert response.json() == {"today": real_today(), "overridden": False, "demo_mode": True}


def test_simulated_date_drives_the_trade_rules(demo_client: TestClient) -> None:
    assert demo_client.post("/trades", json=trade()).status_code == 400

    demo_client.put("/admin/clock", json={"today": "2015-03-14"})
    response = demo_client.post("/trades", json=trade())

    assert response.status_code == 201
    assert response.json()["created_date"] == "2015-03-14"


def test_simulated_date_drives_the_expiry_sweep(demo_client: TestClient) -> None:
    demo_client.put("/admin/clock", json={"today": "2014-05-01"})
    demo_client.post("/trades", json=trade("T3", 3, "2014-05-20"))

    demo_client.put("/admin/clock", json={"today": "2014-05-21"})
    response = demo_client.post("/admin/expire-trades")

    assert response.json() == {"expired_count": 1, "run_date": "2014-05-21"}
    assert demo_client.get("/trades/T3").json()["expired"] is True


# --- DELETE /admin/trades ---------------------------------------------------------------


def test_delete_trades_empties_the_store(demo_client: TestClient) -> None:
    demo_client.put("/admin/clock", json={"today": "2015-03-14"})
    for version in (1, 2):
        demo_client.post("/trades", json=trade("T2", version))
    demo_client.post("/trades", json=trade("T1"))

    response = demo_client.delete("/admin/trades")

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 3}
    assert demo_client.get("/trades").json() == []
    assert demo_client.delete("/admin/trades").json() == {"deleted_count": 0}


def test_reset_does_not_touch_the_clock(demo_client: TestClient) -> None:
    demo_client.put("/admin/clock", json={"today": "2015-03-14"})

    demo_client.delete("/admin/trades")

    assert demo_client.get("/admin/clock").json()["today"] == date(2015, 3, 14).isoformat()
