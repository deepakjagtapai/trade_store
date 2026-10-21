from datetime import date


class TradeStoreError(Exception):
    error_code = "TRADE_STORE_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LowerVersionError(TradeStoreError):
    error_code = "LOWER_VERSION"

    def __init__(self, trade_id: str, incoming_version: int, max_version: int) -> None:
        self.trade_id = trade_id
        self.incoming_version = incoming_version
        self.max_version = max_version
        super().__init__(
            f"Trade {trade_id!r} version {incoming_version} is lower than the current "
            f"maximum stored version {max_version}"
        )


class MaturityInPastError(TradeStoreError):
    error_code = "MATURITY_IN_PAST"

    def __init__(self, maturity_date: date, today: date) -> None:
        self.maturity_date = maturity_date
        self.today = today
        super().__init__(
            f"Maturity date {maturity_date.isoformat()} is earlier than today ({today.isoformat()})"
        )


class TradeNotFoundError(TradeStoreError):
    error_code = "TRADE_NOT_FOUND"

    def __init__(self, trade_id: str) -> None:
        self.trade_id = trade_id
        super().__init__(f"Trade {trade_id!r} not found")
