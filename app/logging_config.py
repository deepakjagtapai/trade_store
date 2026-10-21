import json
import logging
from datetime import UTC, datetime
from typing import Any

_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; any ``extra={...}`` fields become top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
    if not any(isinstance(h.formatter, JsonFormatter) for h in app_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        app_logger.addHandler(handler)
