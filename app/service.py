import logging
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import Clock
from app.exceptions import (
    LowerVersionError,
    MaturityInPastError,
    TradeNotFoundError,
    TradeStoreError,
)
from app.models import TradeORM
from app.repository import TradeRepository
from app.schemas import TradeIn

logger = logging.getLogger(__name__)


class TradeService:
    """Owns every trade business rule. Each public write is a single transaction."""

    def __init__(self, session: Session, repository: TradeRepository, clock: Clock) -> None:
        self._session = session
        self._repo = repository
        self._clock = clock

    def store_trade(self, trade_in: TradeIn) -> TradeORM:
        today = self._clock.today()
        try:
            if trade_in.maturity_date < today:
                raise MaturityInPastError(trade_in.maturity_date, today)
            try:
                trade, outcome = self._upsert(trade_in, today)
            except IntegrityError:
                self._session.rollback()
                logger.warning(
                    "Concurrent insert detected for trade %s v%s, retrying",
                    trade_in.trade_id,
                    trade_in.version,
                    extra={"trade_id": trade_in.trade_id, "version": trade_in.version},
                )
                trade, outcome = self._upsert(trade_in, today)
        except TradeStoreError as exc:
            self._session.rollback()
            logger.info(
                "Trade %s v%s rejected: %s",
                trade_in.trade_id,
                trade_in.version,
                exc.message,
                extra={
                    "trade_id": trade_in.trade_id,
                    "version": trade_in.version,
                    "outcome": "rejected",
                    "error_code": exc.error_code,
                },
            )
            raise
        except Exception:
            self._session.rollback()
            raise

        logger.info(
            "Trade %s v%s %s",
            trade.trade_id,
            trade.version,
            outcome,
            extra={"trade_id": trade.trade_id, "version": trade.version, "outcome": outcome},
        )
        return trade

    def _upsert(self, trade_in: TradeIn, today: date) -> tuple[TradeORM, str]:
        # Lock first, then read the max in a separate statement: under READ COMMITTED the
        # second statement sees versions committed by whoever held the lock before us.
        self._repo.lock_trade(trade_in.trade_id)
        max_version = self._repo.get_max_version(trade_in.trade_id)
        if max_version is not None and trade_in.version < max_version:
            raise LowerVersionError(trade_in.trade_id, trade_in.version, max_version)

        trade = self._repo.get(trade_in.trade_id, trade_in.version)
        if trade is not None:
            trade.counterparty_id = trade_in.counterparty_id
            trade.portfolio_id = trade_in.portfolio_id
            trade.maturity_date = trade_in.maturity_date
            trade.created_date = today
            trade.expired = False
            outcome = "replaced"
        else:
            trade = self._repo.add(
                TradeORM(
                    trade_id=trade_in.trade_id,
                    version=trade_in.version,
                    counterparty_id=trade_in.counterparty_id,
                    portfolio_id=trade_in.portfolio_id,
                    maturity_date=trade_in.maturity_date,
                    created_date=today,
                    expired=False,
                )
            )
            outcome = "inserted"
        self._session.commit()
        return trade, outcome

    def get_latest(self, trade_id: str) -> TradeORM:
        trade = self._repo.get_latest(trade_id)
        if trade is None:
            raise TradeNotFoundError(trade_id)
        return trade

    def get_versions(self, trade_id: str) -> list[TradeORM]:
        versions = self._repo.list_versions(trade_id)
        if not versions:
            raise TradeNotFoundError(trade_id)
        return versions

    def list_trades(self, expired: bool | None = None) -> list[TradeORM]:
        return self._repo.list_all(expired=expired)

    def expire_matured_trades(self) -> int:
        today = self._clock.today()
        try:
            count = self._repo.mark_expired(today)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        logger.info(
            "Expiry sweep for %s marked %d trade(s) expired",
            today.isoformat(),
            count,
            extra={"run_date": today.isoformat(), "expired_count": count},
        )
        return count

    def reset_store(self) -> int:
        """Delete every trade. Only exposed through the demo-mode admin API."""
        try:
            count = self._repo.delete_all()
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        logger.warning(
            "Trade store reset: %d trade(s) deleted", count, extra={"deleted_count": count}
        )
        return count
