from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./instatrack.db"
    team_password_hash: str = ""
    session_secret: str = "development-only-change-me"
    session_cookie_name: str = "instatrack_session"
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    secure_cookies: bool = False
    allowed_origins: str = "http://localhost:4173,http://127.0.0.1:4173"
    collector_job_name: str = ""
    local_collector_enabled: bool = False
    collection_delay_seconds: float = 5.0
    collection_max_retries: int = 2
    collector_adapter: str = "scrapling"
    scrapling_reel_delay_seconds: float = 1.0
    scrapling_timeout_ms: int = 45_000
    max_profiles: int = 100
    max_reels_per_profile: int = 30
    app_timezone: str = "Asia/Tbilisi"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
