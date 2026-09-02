"""Environment-driven application configuration.

All settings are read once from the process environment (optionally seeded from a
local ``.env`` file) and cached for the lifetime of the process via
:func:`get_settings`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Attributes are populated from environment variables (case-insensitive) or a
    ``.env`` file sitting next to the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Application ---------------------------------------------------------
    app_name: str = "Weekly Report Generator API"
    api_v1_prefix: str = "/api/v1"
    cors_allow_origins: Annotated[list[str], NoDecode] = ["*"]

    # -- MongoDB ----------------------------------------------------------------
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "weekly_report"
    mongodb_server_selection_timeout_ms: int = 5000

    # -- JWT / security -------------------------------------------------------
    jwt_secret_key: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # -- Bootstrap ---------------------------------------------------------------
    bootstrap_admin_emails: Annotated[list[str], NoDecode] = []

    @field_validator("cors_allow_origins", "bootstrap_admin_emails", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a comma-separated string *or* a real list from the environment."""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("bootstrap_admin_emails", mode="after")
    @classmethod
    def _normalise_emails(cls, value: list[str]) -> list[str]:
        """Lower-case bootstrap admin emails for case-insensitive comparison."""
        return [email.lower() for email in value]

    @property
    def access_token_expire_seconds(self) -> int:
        """Access-token lifetime expressed in seconds (useful for ``expires_in``)."""
        return self.access_token_expire_minutes * 60

    def is_bootstrap_admin(self, email: str) -> bool:
        """Return ``True`` when *email* should be granted the Admin role on signup."""
        return email.lower() in self.bootstrap_admin_emails


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton."""
    return Settings()


settings: Settings = get_settings()
