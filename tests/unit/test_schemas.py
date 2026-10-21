from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas import (
    ClockOverrideIn,
    ClockState,
    ErrorResponse,
    ExpiryResult,
    ResetResult,
    TradeIn,
    TradeOut,
    UiConfig,
)


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trade_id": "T1",
        "version": 1,
        "counterparty_id": "CP-1",
        "portfolio_id": "B1",
        "maturity_date": "2030-05-20",
    }
    base.update(overrides)
    return base


def test_valid_trade_in() -> None:
    trade = TradeIn(**payload())

    assert trade.trade_id == "T1"
    assert trade.version == 1
    assert trade.counterparty_id == "CP-1"
    assert trade.portfolio_id == "B1"
    assert trade.maturity_date == date(2030, 5, 20)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2030-05-20", date(2030, 5, 20)),
        ("20/05/2030", date(2030, 5, 20)),
        ("01/12/2030", date(2030, 12, 1)),
        (date(2030, 5, 20), date(2030, 5, 20)),
    ],
)
def test_maturity_date_accepts_iso_and_dd_mm_yyyy(raw: Any, expected: date) -> None:
    assert TradeIn(**payload(maturity_date=raw)).maturity_date == expected


@pytest.mark.parametrize(
    "raw",
    ["2030-13-01", "31/02/2030", "20-05-2030", "not-a-date", "", "32/01/2030"],
)
def test_invalid_maturity_date_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        TradeIn(**payload(maturity_date=raw))


@pytest.mark.parametrize("version", [0, -1])
def test_version_below_one_is_rejected(version: int) -> None:
    with pytest.raises(ValidationError):
        TradeIn(**payload(version=version))


@pytest.mark.parametrize("field", ["trade_id", "counterparty_id", "portfolio_id"])
@pytest.mark.parametrize("value", ["", "   "])
def test_empty_strings_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        TradeIn(**payload(**{field: value}))


def test_strings_are_stripped() -> None:
    trade = TradeIn(**payload(trade_id="  T1  ", counterparty_id=" CP-1 ", portfolio_id=" B1"))

    assert (trade.trade_id, trade.counterparty_id, trade.portfolio_id) == ("T1", "CP-1", "B1")


def test_trade_id_longer_than_50_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TradeIn(**payload(trade_id="T" * 51))


def test_trade_id_of_exactly_50_is_accepted() -> None:
    assert TradeIn(**payload(trade_id="T" * 50)).trade_id == "T" * 50


@pytest.mark.parametrize(
    "missing", ["trade_id", "version", "counterparty_id", "portfolio_id", "maturity_date"]
)
def test_missing_required_field_is_rejected(missing: str) -> None:
    data = payload()
    del data[missing]

    with pytest.raises(ValidationError):
        TradeIn(**data)


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TradeIn(**payload(unexpected="x"))


@pytest.mark.parametrize(("field", "value"), [("expired", True), ("created_date", "2015-03-14")])
def test_client_cannot_set_server_managed_fields(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        TradeIn(**payload(**{field: value}))


def test_trade_out_reads_from_orm_attributes() -> None:
    orm_like = SimpleNamespace(
        trade_id="T1",
        version=2,
        counterparty_id="CP-1",
        portfolio_id="B1",
        maturity_date=date(2030, 5, 20),
        created_date=date(2026, 1, 1),
        expired=False,
    )

    out = TradeOut.model_validate(orm_like)

    assert out.model_dump() == {
        "trade_id": "T1",
        "version": 2,
        "counterparty_id": "CP-1",
        "portfolio_id": "B1",
        "maturity_date": date(2030, 5, 20),
        "created_date": date(2026, 1, 1),
        "expired": False,
    }


def test_error_response_and_expiry_result() -> None:
    assert ErrorResponse(error_code="X", message="m").model_dump() == {
        "error_code": "X",
        "message": "m",
    }
    assert ExpiryResult(expired_count=3, run_date=date(2026, 1, 1)).expired_count == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2015-03-14", date(2015, 3, 14)), ("14/03/2015", date(2015, 3, 14))],
)
def test_clock_override_accepts_iso_and_dd_mm_yyyy(raw: str, expected: date) -> None:
    assert ClockOverrideIn(today=raw).today == expected


@pytest.mark.parametrize("payload", [{"today": "31/02/2015"}, {"today": ""}, {}])
def test_clock_override_rejects_invalid_dates(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ClockOverrideIn(**payload)


def test_clock_override_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ClockOverrideIn(today="2015-03-14", overridden=True)


def test_demo_response_schemas() -> None:
    state = ClockState(today=date(2015, 3, 14), overridden=True, demo_mode=True)

    assert state.model_dump(mode="json") == {
        "today": "2015-03-14",
        "overridden": True,
        "demo_mode": True,
    }
    assert ResetResult(deleted_count=4).model_dump() == {"deleted_count": 4}
    assert UiConfig(demo_mode=False).model_dump() == {"demo_mode": False}
