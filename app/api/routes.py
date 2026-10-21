from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse, RedirectResponse

from app.api.dependencies import (
    get_app_settings,
    get_clock,
    get_switchable_clock,
    get_trade_service,
)
from app.clock import Clock, SwitchableClock
from app.config import Settings
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
from app.service import TradeService

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

ServiceDep = Annotated[TradeService, Depends(get_trade_service)]
ClockDep = Annotated[Clock, Depends(get_clock)]
SwitchableClockDep = Annotated[SwitchableClock, Depends(get_switchable_clock)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Trade not found"},
}

health_router = APIRouter(tags=["health"])
trades_router = APIRouter(prefix="/trades", tags=["trades"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
# Registered only when DEMO_MODE=true.
demo_router = APIRouter(prefix="/admin", tags=["demo"])
# Registered only when ENABLE_UI=true.
ui_router = APIRouter(tags=["ui"])


@health_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@trades_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Maturity date is earlier than today",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Version is lower than the highest stored version",
        },
    },
)
def create_trade(trade_in: TradeIn, service: ServiceDep) -> TradeOut:
    """Store a trade. A same-version trade replaces the stored record; higher versions add one."""
    return TradeOut.model_validate(service.store_trade(trade_in))


@trades_router.get("")
def list_trades(service: ServiceDep, expired: bool | None = None) -> list[TradeOut]:
    return [TradeOut.model_validate(t) for t in service.list_trades(expired)]


@trades_router.get("/{trade_id}", responses=_NOT_FOUND)
def get_trade(trade_id: str, service: ServiceDep) -> TradeOut:
    """Return the highest stored version of a trade."""
    return TradeOut.model_validate(service.get_latest(trade_id))


@trades_router.get("/{trade_id}/versions", responses=_NOT_FOUND)
def get_trade_versions(trade_id: str, service: ServiceDep) -> list[TradeOut]:
    return [TradeOut.model_validate(t) for t in service.get_versions(trade_id)]


@admin_router.post("/expire-trades")
def expire_trades(service: ServiceDep, clock: ClockDep) -> ExpiryResult:
    """Run the expiry sweep immediately (the scheduler also runs it daily)."""
    return ExpiryResult(expired_count=service.expire_matured_trades(), run_date=clock.today())


def _clock_state(clock: SwitchableClock) -> ClockState:
    return ClockState(today=clock.today(), overridden=clock.override is not None, demo_mode=True)


@demo_router.get("/clock")
def get_demo_clock(clock: SwitchableClockDep) -> ClockState:
    """Current business date and whether it is simulated."""
    return _clock_state(clock)


@demo_router.put("/clock")
def set_demo_clock(body: ClockOverrideIn, clock: SwitchableClockDep) -> ClockState:
    """Simulate "today" (ISO or DD/MM/YYYY) for every rule and the expiry sweep."""
    clock.set_override(body.today)
    return _clock_state(clock)


@demo_router.delete("/clock")
def clear_demo_clock(clock: SwitchableClockDep) -> ClockState:
    """Go back to the real current date."""
    clock.clear_override()
    return _clock_state(clock)


@demo_router.delete("/trades")
def reset_trades(service: ServiceDep) -> ResetResult:
    """Delete every stored trade."""
    return ResetResult(deleted_count=service.reset_store())


@ui_router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui")


@ui_router.get("/ui", include_in_schema=False)
def ui_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@ui_router.get("/config/ui")
def ui_config(settings: SettingsDep) -> UiConfig:
    """Tells the manual-testing page which controls to show."""
    return UiConfig(demo_mode=settings.demo_mode)
