from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.clock import Clock, SwitchableClock
from app.config import Settings
from app.db import get_session
from app.repository import TradeRepository
from app.service import TradeService


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_switchable_clock(request: Request) -> SwitchableClock:
    # Demo routes are only registered when create_app installed a SwitchableClock.
    return cast(SwitchableClock, request.app.state.clock)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_trade_service(
    session: Annotated[Session, Depends(get_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> TradeService:
    return TradeService(session, TradeRepository(session), clock)
