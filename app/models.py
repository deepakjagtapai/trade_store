from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TradeORM(Base):
    __tablename__ = "trades"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_trades_version_positive"),)

    trade_id: Mapped[str] = mapped_column(String(50), primary_key=True, index=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    counterparty_id: Mapped[str] = mapped_column(String(50), nullable=False)
    portfolio_id: Mapped[str] = mapped_column(String(50), nullable=False)
    maturity_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_date: Mapped[date] = mapped_column(Date, nullable=False)
    expired: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"TradeORM(trade_id={self.trade_id!r}, version={self.version}, "
            f"maturity_date={self.maturity_date}, expired={self.expired})"
        )
