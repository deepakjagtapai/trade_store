"""Replays the sample trade table from the problem statement through the HTTP API."""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.clock import FixedClock, SystemClock

pytestmark = pytest.mark.regression

# (trade_id, version, counterparty_id, portfolio_id, maturity_date) exactly as in the table.
SAMPLE_TABLE = [
    ("T1", 1, "CP-1", "B1", "20/05/2020"),
    ("T2", 2, "CP-2", "B1", "20/05/2021"),
    ("T2", 1, "CP-1", "B1", "20/05/2021"),
    ("T3", 3, "CP-3", "B2", "20/05/2014"),
]


def trade(
    trade_id: str, version: int, counterparty: str, portfolio: str, maturity: str
) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "version": version,
        "counterparty_id": counterparty,
        "portfolio_id": portfolio,
        "maturity_date": maturity,
    }


@pytest.mark.parametrize("row", SAMPLE_TABLE, ids=[f"{r[0]}-v{r[1]}" for r in SAMPLE_TABLE])
def test_scenario_a_every_sample_row_is_rejected_with_the_real_date(
    client: TestClient, clock: FixedClock, row: tuple[str, int, str, str, str]
) -> None:
    clock.set(SystemClock().today())

    response = client.post("/trades", json=trade(*row))

    assert response.status_code == 400
    assert response.json()["error_code"] == "MATURITY_IN_PAST"
    assert client.get("/trades").json() == []


def test_scenario_a_store_is_empty_after_posting_the_whole_table(
    client: TestClient, clock: FixedClock
) -> None:
    clock.set(SystemClock().today())

    codes = [client.post("/trades", json=trade(*row)).status_code for row in SAMPLE_TABLE]

    assert codes == [400, 400, 400, 400]
    assert client.get("/trades").json() == []


def test_scenario_b_replay_table_in_time_order(client: TestClient, clock: FixedClock) -> None:
    clock.set(date(2015, 3, 14))
    t2_v1 = client.post("/trades", json=trade("T2", 1, "CP-1", "B1", "2021-05-20"))
    assert t2_v1.status_code == 201
    assert t2_v1.json()["created_date"] == "2015-03-14"
    assert t2_v1.json()["expired"] is False

    clock.set(date(2015, 3, 15))
    t2_v2 = client.post("/trades", json=trade("T2", 2, "CP-2", "B1", "2021-05-20"))
    assert t2_v2.status_code == 201
    assert t2_v2.json()["created_date"] == "2015-03-15"

    lower = client.post("/trades", json=trade("T2", 1, "CP-1", "B1", "2021-05-20"))
    assert lower.status_code == 409
    assert lower.json()["error_code"] == "LOWER_VERSION"

    replaced = client.post("/trades", json=trade("T2", 2, "CP-2", "B2", "2021-05-20"))
    assert replaced.status_code == 201
    latest = client.get("/trades/T2").json()
    assert (latest["version"], latest["portfolio_id"]) == (2, "B2")

    t1 = client.post("/trades", json=trade("T1", 1, "CP-1", "B1", "2020-05-20"))
    assert t1.status_code == 201

    t3 = client.post("/trades", json=trade("T3", 3, "CP-3", "B2", "2014-05-20"))
    assert t3.status_code == 400
    assert t3.json()["error_code"] == "MATURITY_IN_PAST"

    stored = [
        (t["trade_id"], t["version"], t["counterparty_id"], t["portfolio_id"], t["created_date"])
        for t in client.get("/trades").json()
    ]
    assert stored == [
        ("T1", 1, "CP-1", "B1", "2015-03-15"),
        ("T2", 1, "CP-1", "B1", "2015-03-14"),
        ("T2", 2, "CP-2", "B2", "2015-03-15"),
    ]
    assert [t["version"] for t in client.get("/trades/T2/versions").json()] == [1, 2]


def test_scenario_c_t3_expires_automatically(client: TestClient, clock: FixedClock) -> None:
    clock.set(date(2014, 5, 1))
    created = client.post("/trades", json=trade("T3", 3, "CP-3", "B2", "20/05/2014"))
    assert created.status_code == 201
    assert created.json()["expired"] is False

    clock.set(date(2014, 5, 20))
    on_maturity = client.post("/admin/expire-trades").json()
    assert on_maturity == {"expired_count": 0, "run_date": "2014-05-20"}
    assert client.get("/trades/T3").json()["expired"] is False

    clock.set(date(2014, 5, 21))
    after_maturity = client.post("/admin/expire-trades").json()
    assert after_maturity == {"expired_count": 1, "run_date": "2014-05-21"}
    assert client.get("/trades/T3").json()["expired"] is True

    expired = client.get("/trades?expired=true").json()
    assert [(t["trade_id"], t["version"]) for t in expired] == [("T3", 3)]
    assert client.get("/trades?expired=false").json() == []


@pytest.mark.parametrize(
    ("run_date", "expected_count", "expired_ids"),
    [
        (date(2020, 5, 20), 0, []),
        (date(2020, 5, 21), 1, ["T1"]),
        (date(2021, 5, 21), 3, ["T1", "T2", "T2"]),
    ],
)
def test_scenario_c_sweeping_the_table_as_time_passes(
    client: TestClient,
    clock: FixedClock,
    run_date: date,
    expected_count: int,
    expired_ids: list[str],
) -> None:
    t1_v1, t2_v2, t2_v1, _t3 = SAMPLE_TABLE
    clock.set(date(2015, 3, 14))
    for row in (t1_v1, t2_v1, t2_v2):
        assert client.post("/trades", json=trade(*row)).status_code == 201

    clock.set(run_date)
    result = client.post("/admin/expire-trades").json()

    assert result["expired_count"] == expected_count
    assert [t["trade_id"] for t in client.get("/trades?expired=true").json()] == expired_ids
