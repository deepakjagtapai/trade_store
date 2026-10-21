import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATABASE_URL", "ENABLE_SCHEDULER", "EXPIRY_CRON_HOUR", "LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == "sqlite:///./trade_store.db"
    assert settings.enable_scheduler is True
    assert settings.expiry_cron_hour == 0
    assert settings.log_level == "INFO"


def test_values_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/trades")
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    monkeypatch.setenv("EXPIRY_CRON_HOUR", "2")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/trades"
    assert settings.enable_scheduler is False
    assert settings.expiry_cron_hour == 2
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("hour", ["-1", "24"])
def test_cron_hour_must_be_a_valid_hour(monkeypatch: pytest.MonkeyPatch, hour: str) -> None:
    monkeypatch.setenv("EXPIRY_CRON_HOUR", hour)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_demo_mode_is_off_and_ui_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.delenv("ENABLE_UI", raising=False)

    settings = Settings(_env_file=None)

    assert settings.demo_mode is False
    assert settings.enable_ui is True


def test_demo_mode_and_ui_flags_are_read_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("ENABLE_UI", "false")

    settings = Settings(_env_file=None)

    assert settings.demo_mode is True
    assert settings.enable_ui is False
