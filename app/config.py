from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./trade_store.db"
    enable_scheduler: bool = True
    expiry_cron_hour: Annotated[int, Field(ge=0, le=23)] = 0
    log_level: str = "INFO"
    demo_mode: bool = False
    enable_ui: bool = True

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
