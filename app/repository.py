from datetime import date
from typing import cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.orm import Session

from app.models import TradeORM


class TradeRepository:
    """Data access for trades. Contains no business rules and never commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, trade_id: str, version: int) -> TradeORM | None:
        return self._session.get(TradeORM, (trade_id, version))

    def get_latest(self, trade_id: str) -> TradeORM | None:
        stmt = (
            select(TradeORM)
            .where(TradeORM.trade_id == trade_id)
            .order_by(TradeORM.version.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def get_max_version(self, trade_id: str) -> int | None:
        stmt = select(func.max(TradeORM.version)).where(TradeORM.trade_id == trade_id)
        return self._session.scalar(stmt)

    def lock_trade(self, trade_id: str) -> list[int]:
        """Row-lock every version of ``trade_id`` (``SELECT ... FOR UPDATE``).

        Renders as a plain SELECT on SQLite, which serialises writers itself.
        """
        stmt = (
            select(TradeORM.version)
            .where(TradeORM.trade_id == trade_id)
            .order_by(TradeORM.version)
            .with_for_update()
        )
        return list(self._session.scalars(stmt))

    def list_versions(self, trade_id: str) -> list[TradeORM]:
        stmt = select(TradeORM).where(TradeORM.trade_id == trade_id).order_by(TradeORM.version)
        return list(self._session.scalars(stmt))

    def list_all(self, expired: bool | None = None) -> list[TradeORM]:
        stmt = select(TradeORM).order_by(TradeORM.trade_id, TradeORM.version)
        if expired is not None:
            stmt = stmt.where(TradeORM.expired.is_(expired))
        return list(self._session.scalars(stmt))

    def add(self, trade: TradeORM) -> TradeORM:
        self._session.add(trade)
        self._session.flush()
        return trade

    def mark_expired(self, today: date) -> int:
        stmt = (
            update(TradeORM)
            .where(TradeORM.maturity_date < today, TradeORM.expired.is_(False))
            .values(expired=True)
        )
        result = cast(CursorResult[tuple[()]], self._session.execute(stmt))
        return result.rowcount

    def delete_all(self) -> int:
        result = cast(CursorResult[tuple[()]], self._session.execute(delete(TradeORM)))
        return result.rowcount
