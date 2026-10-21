import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    STATIC_DIR,
    admin_router,
    demo_router,
    health_router,
    trades_router,
    ui_router,
)
from app.clock import Clock, SwitchableClock, SystemClock
from app.config import Settings, get_settings
from app.db import Base, create_db_engine, create_session_factory
from app.exceptions import (
    LowerVersionError,
    MaturityInPastError,
    TradeNotFoundError,
    TradeStoreError,
)
from app.logging_config import configure_logging
from app.scheduler import start_scheduler
from app.schemas import ErrorResponse

logger = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[TradeStoreError], int] = {
    LowerVersionError: status.HTTP_409_CONFLICT,
    MaturityInPastError: status.HTTP_400_BAD_REQUEST,
    TradeNotFoundError: status.HTTP_404_NOT_FOUND,
}


async def trade_store_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(TradeStoreError, exc)
    return JSONResponse(
        status_code=_STATUS_BY_ERROR.get(type(error), status.HTTP_400_BAD_REQUEST),
        content=ErrorResponse(error_code=error.error_code, message=error.message).model_dump(),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.demo_mode:
            logger.warning(
                "DEMO_MODE is enabled: the business date can be overridden and the store "
                "reset via /admin. Never enable this in production."
            )
        Base.metadata.create_all(engine)
        scheduler = start_scheduler(settings, session_factory, app.state.clock)
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            engine.dispose()

    app = FastAPI(
        title="Trade Store",
        version="1.0.0",
        description="Stores trades while enforcing version, maturity and expiry rules.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    # One instance shared by request handlers and the scheduler thread.
    clock: Clock = SwitchableClock() if settings.demo_mode else SystemClock()
    app.state.clock = clock

    app.add_exception_handler(TradeStoreError, trade_store_error_handler)
    app.include_router(health_router)
    app.include_router(trades_router)
    app.include_router(admin_router)
    if settings.demo_mode:
        app.include_router(demo_router)
    if settings.enable_ui:
        app.include_router(ui_router)
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
