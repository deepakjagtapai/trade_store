import json
import logging

from app.logging_config import JsonFormatter, configure_logging


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.service", logging.INFO, __file__, 1, "Trade %s", ("T1",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_message_and_extra_fields() -> None:
    line = JsonFormatter().format(make_record(trade_id="T1", version=2, outcome="inserted"))

    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.service"
    assert payload["message"] == "Trade T1"
    assert payload["trade_id"] == "T1"
    assert payload["version"] == 2
    assert payload["outcome"] == "inserted"
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_includes_exception_info() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("app", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())

    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exc_info"]


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("WARNING")

    app_logger = logging.getLogger("app")
    json_handlers = [h for h in app_logger.handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(json_handlers) == 1
    assert app_logger.level == logging.WARNING
    configure_logging("INFO")
