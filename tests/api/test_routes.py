from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.clock import FixedClock, SystemClock
from app.config import Settings
from app.main import create_app
from tests.conftest import TODAY

FUTURE = (TODAY + timedelta(days=365)).isoformat()


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "trade_id": "T1",
        "version": 1,
        "counterparty_id": "CP-1",
        "portfolio_id": "B1",
        "maturity_date": FUTURE,
    }
    data.update(overrides)
    return data


def expected_out(**overrides: Any) -> dict[str, Any]:
    out = body(created_date=TODAY.isoformat(), expired=False)
    out.update(overrides)
    return out


# --- /health ----------------------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_docs_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/trades", "/trades/{trade_id}", "/trades/{trade_id}/versions"} <= set(paths)
    assert {"201", "400", "409", "422"} <= set(paths["/trades"]["post"]["responses"])


# --- POST /trades -----------------------------------------------------------------------


def test_create_trade_returns_201_with_server_fields(client: TestClient) -> None:
    response = client.post("/trades", json=body())

    assert response.status_code == 201
    assert response.json() == expected_out()


def test_create_trade_accepts_dd_mm_yyyy(client: TestClient) -> None:
    response = client.post("/trades", json=body(maturity_date="20/05/2030"))

    assert response.status_code == 201
    assert response.json()["maturity_date"] == "2030-05-20"


def test_create_trade_with_maturity_today_returns_201(client: TestClient) -> None:
    response = client.post("/trades", json=body(maturity_date=TODAY.isoformat()))

    assert response.status_code == 201


def test_create_trade_with_past_maturity_returns_400(client: TestClient) -> None:
    yesterday = (TODAY - timedelta(days=1)).isoformat()

    response = client.post("/trades", json=body(maturity_date=yesterday))

    assert response.status_code == 400
    assert response.json() == {
        "error_code": "MATURITY_IN_PAST",
        "message": f"Maturity date {yesterday} is earlier than today ({TODAY.isoformat()})",
    }
    assert client.get("/trades").json() == []


def test_create_lower_version_returns_409(client: TestClient) -> None:
    client.post("/trades", json=body(version=2))

    response = client.post("/trades", json=body(version=1))

    assert response.status_code == 409
    assert response.json() == {
        "error_code": "LOWER_VERSION",
        "message": "Trade 'T1' version 1 is lower than the current maximum stored version 2",
    }


def test_create_same_version_replaces(client: TestClient, clock: FixedClock) -> None:
    client.post("/trades", json=body())
    clock.set(TODAY + timedelta(days=1))

    response = client.post("/trades", json=body(portfolio_id="B2"))

    assert response.status_code == 201
    replaced = expected_out(portfolio_id="B2", created_date=(TODAY + timedelta(days=1)).isoformat())
    assert response.json() == replaced
    assert client.get("/trades/T1/versions").json() == [replaced]


def test_create_higher_version_keeps_history(client: TestClient) -> None:
    client.post("/trades", json=body(version=1))

    response = client.post("/trades", json=body(version=2, counterparty_id="CP-2"))

    assert response.status_code == 201
    assert [t["version"] for t in client.get("/trades/T1/versions").json()] == [1, 2]


@pytest.mark.parametrize(
    "payload",
    [
        body(version=0),
        body(trade_id=""),
        body(maturity_date="2030-02-30"),
        body(expired=True),
        body(created_date="2015-01-01"),
        {"trade_id": "T1"},
    ],
    ids=["version-0", "empty-id", "bad-date", "set-expired", "set-created", "missing-fields"],
)
def test_create_trade_invalid_payload_returns_422(
    client: TestClient, payload: dict[str, Any]
) -> None:
    response = client.post("/trades", json=payload)

    assert response.status_code == 422
    assert client.get("/trades").json() == []


# --- GET /trades ------------------------------------------------------------------------


@pytest.fixture
def mixed_trades(client: TestClient, clock: FixedClock) -> None:
    client.post("/trades", json=body(trade_id="T1", maturity_date=TODAY.isoformat()))
    client.post("/trades", json=body(trade_id="T2"))
    clock.set(TODAY + timedelta(days=1))
    client.post("/admin/expire-trades")


def test_list_trades_empty(client: TestClient) -> None:
    response = client.get("/trades")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.usefixtures("mixed_trades")
@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [("", ["T1", "T2"]), ("?expired=true", ["T1"]), ("?expired=false", ["T2"])],
)
def test_list_trades_expired_filter(
    client: TestClient, query: str, expected_ids: list[str]
) -> None:
    response = client.get(f"/trades{query}")

    assert response.status_code == 200
    assert [t["trade_id"] for t in response.json()] == expected_ids


def test_list_trades_invalid_filter_returns_422(client: TestClient) -> None:
    assert client.get("/trades?expired=maybe").status_code == 422


# --- GET /trades/{id} and /versions ------------------------------------------------------


def test_get_trade_returns_latest_version(client: TestClient) -> None:
    client.post("/trades", json=body(version=1))
    client.post("/trades", json=body(version=3, counterparty_id="CP-3"))

    response = client.get("/trades/T1")

    assert response.status_code == 200
    assert response.json() == expected_out(version=3, counterparty_id="CP-3")


def test_get_versions_returns_all_ascending(client: TestClient) -> None:
    for version in (1, 2, 4):
        client.post("/trades", json=body(version=version))

    response = client.get("/trades/T1/versions")

    assert response.status_code == 200
    assert [t["version"] for t in response.json()] == [1, 2, 4]


@pytest.mark.parametrize("path", ["/trades/NOPE", "/trades/NOPE/versions"])
def test_unknown_trade_returns_404(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"error_code": "TRADE_NOT_FOUND", "message": "Trade 'NOPE' not found"}


# --- POST /admin/expire-trades ----------------------------------------------------------


def test_expire_trades_endpoint(client: TestClient, clock: FixedClock) -> None:
    client.post("/trades", json=body(maturity_date=TODAY.isoformat()))

    first = client.post("/admin/expire-trades")
    clock.set(TODAY + timedelta(days=1))
    second = client.post("/admin/expire-trades")
    third = client.post("/admin/expire-trades")

    assert first.status_code == 200
    assert first.json() == {"expired_count": 0, "run_date": TODAY.isoformat()}
    assert second.json() == {
        "expired_count": 1,
        "run_date": (TODAY + timedelta(days=1)).isoformat(),
    }
    assert third.json()["expired_count"] == 0
    assert client.get("/trades/T1").json()["expired"] is True


# --- Wiring without overrides ----------------------------------------------------------


def test_app_works_end_to_end_with_real_dependencies(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'store.db'}", enable_scheduler=False)

    with TestClient(create_app(settings)) as client:
        created = client.post("/trades", json=body(maturity_date="2999-01-01"))
        fetched = client.get("/trades/T1")

    assert created.status_code == 201
    assert created.json()["created_date"] == SystemClock().today().isoformat()
    assert fetched.json() == created.json()
