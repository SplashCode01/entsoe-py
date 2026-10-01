"""Application settings, loaded from environment variables (and .env locally)."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ENTSOE_",  # ENTSOE_API_TOKEN -> api_token
        env_file=".env",
        extra="ignore",
    )

    api_token: SecretStr
    base_url: str = "https://web-api.tp.entsoe.eu/api"
    timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
