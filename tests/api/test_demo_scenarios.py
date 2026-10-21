"""Replays every UI scenario preset (app/static/scenarios.json) through the demo-mode API.

The browser console loads the same file, so these tests guarantee each preset produces the
outcome its description promises.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app
from app.config import Settings
from app.main import create_app

SCENARIOS_FILE = Path(app.__file__).parent / "static" / "scenarios.json"
DATA: dict[str, Any] = json.loads(SCENARIOS_FILE.read_text())
SCENARIOS: dict[str, dict[str, Any]] = {s["id"]: s for s in DATA["scenarios"]}
STORE_FIELDS = ("trade_id", "version", "counterparty_id", "portfolio_id", "expired")


@pytest.fixture
def demo_client() -> Iterator[TestClient]:
    settings = Settings(database_url="sqlite://", enable_scheduler=False, demo_mode=True)
    with TestClient(create_app(settings)) as client:
        yield client


def store_rows(client: TestClient, fields: tuple[str, ...]) -> list[tuple[Any, ...]]:
    return sorted(tuple(t[f] for f in fields) for t in client.get("/trades").json())


def expected_rows(rows: list[dict[str, Any]]) -> tuple[tuple[str, ...], list[tuple[Any, ...]]]:
    fields = tuple(f for f in STORE_FIELDS if f in rows[0]) if rows else ("trade_id",)
    return fields, sorted(tuple(r[f] for f in fields) for r in rows)


def outcome(response: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"status": response.status_code}
    if response.status_code >= 400 and response.status_code != 422:
        result["error_code"] = response.json()["error_code"]
    return result


def run_scenario(client: TestClient, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    if scenario.get("reset"):
        client.delete("/admin/trades")
    if scenario["date"] is None:
        client.delete("/admin/clock")
    else:
        client.put("/admin/clock", json={"today": scenario["date"]})
    return [outcome(client.post("/trades", json=t)) for t in scenario["trades"]]


def test_every_scenario_has_the_fields_the_ui_needs() -> None:
    for scenario in DATA["scenarios"]:
        assert {"id", "title", "description", "date", "trades", "expected"} <= set(scenario)
        assert len(scenario["expected"]) == len(scenario["trades"]), scenario["id"]
    assert [s["id"] for s in DATA["scenarios"]] == [
        "sample-real-date",
        "lower-version",
        "same-version",
        "higher-version",
        "maturity",
        "expiry-step-1",
        "expiry-step-2",
    ]


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_scenario_produces_its_documented_outcome(
    demo_client: TestClient, scenario_id: str
) -> None:
    scenario = SCENARIOS[scenario_id]
    if prerequisite := scenario.get("requires"):
        run_scenario(demo_client, SCENARIOS[prerequisite])

    assert run_scenario(demo_client, scenario) == scenario["expected"]

    if sweep := scenario.get("follow_up_sweep"):
        result = demo_client.post("/admin/expire-trades").json()
        assert result["expired_count"] == sweep["expired_count"]
    if "expected_store" in scenario:
        fields, rows = expected_rows(scenario["expected_store"])
        assert store_rows(demo_client, fields) == rows


def test_sample_table_scenario_contains_the_four_sample_rows_in_order() -> None:
    trades = SCENARIOS["sample-real-date"]["trades"]

    assert [(t["trade_id"], t["version"], t["maturity_date"]) for t in trades] == [
        ("T1", 1, "20/05/2020"),
        ("T2", 2, "20/05/2021"),
        ("T2", 1, "20/05/2021"),
        ("T3", 3, "20/05/2014"),
    ]


def test_guided_demo_rebuilds_the_sample_table(demo_client: TestClient) -> None:
    demo = DATA["guided_demo"]
    demo_client.put("/admin/clock", json={"today": "2030-01-01"})
    demo_client.post(
        "/trades",
        json={
            "trade_id": "LEFTOVER",
            "version": 1,
            "counterparty_id": "X",
            "portfolio_id": "X",
            "maturity_date": "2031-01-01",
        },
    )

    for step in demo["steps"]:
        action = step["action"]
        if action == "reset":
            demo_client.delete("/admin/trades")
        elif action == "set_date":
            demo_client.put("/admin/clock", json={"today": step["date"]})
        elif action == "submit":
            assert outcome(demo_client.post("/trades", json=step["trade"])) == step["expected"]
        elif action == "sweep":
            result = demo_client.post("/admin/expire-trades").json()
            assert result["expired_count"] == step["expected"]["expired_count"]
        else:  # pragma: no cover - guards against typos in scenarios.json
            pytest.fail(f"unknown guided demo action {action!r}")

    fields = ("trade_id", "version", "counterparty_id", "portfolio_id", "created_date", "expired")
    assert store_rows(demo_client, fields) == [
        ("T1", 1, "CP-1", "B1", "2015-03-15", False),
        ("T2", 1, "CP-1", "B1", "2015-03-14", False),
        ("T2", 2, "CP-2", "B1", "2015-03-15", False),
        ("T3", 3, "CP-3", "B2", "2014-05-01", True),
    ]
    assert demo_client.get("/admin/clock").json()["today"] == "2015-03-15"
    assert demo["final_message"]
