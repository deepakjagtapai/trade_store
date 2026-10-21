import re
from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]

_DD_MM_YYYY = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _parse_dd_mm_yyyy(value: Any) -> Any:
    """Turn a ``DD/MM/YYYY`` string into a date; leave anything else for Pydantic."""
    if isinstance(value, str) and (match := _DD_MM_YYYY.match(value.strip())):
        day, month, year = (int(part) for part in match.groups())
        return date(year, month, day)
    return value


class TradeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_id: Identifier
    version: Annotated[int, Field(ge=1)]
    counterparty_id: Identifier
    portfolio_id: Identifier
    maturity_date: date

    @field_validator("maturity_date", mode="before")
    @classmethod
    def parse_dd_mm_yyyy(cls, value: Any) -> Any:
        return _parse_dd_mm_yyyy(value)


class TradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trade_id: str
    version: int
    counterparty_id: str
    portfolio_id: str
    maturity_date: date
    created_date: date
    expired: bool


class ErrorResponse(BaseModel):
    error_code: str
    message: str


class ExpiryResult(BaseModel):
    expired_count: int
    run_date: date


class ClockOverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    today: date

    @field_validator("today", mode="before")
    @classmethod
    def parse_dd_mm_yyyy(cls, value: Any) -> Any:
        return _parse_dd_mm_yyyy(value)


class ClockState(BaseModel):
    today: date
    overridden: bool
    demo_mode: bool


class ResetResult(BaseModel):
    deleted_count: int


class UiConfig(BaseModel):
    demo_mode: bool
