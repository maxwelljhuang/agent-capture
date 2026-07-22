"""Env-driven configuration for the enforcement engine.

Every setting is overridable via ``ENFORCEMENT_<NAME>``. The verdict service
and CLI read from here; tests construct ``Settings(...)`` directly.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ENFORCEMENT_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    # --- rules --------------------------------------------------------------
    rules_path: Path | None = Field(
        default=None,
        description="Path to the bank-authored rule YAML loaded by the verdict service.",
    )

    # --- verdict service ----------------------------------------------------
    listen_host: str = "0.0.0.0"
    listen_port: int = 8475
    service_token: str | None = Field(
        default=None,
        description="If set, /verdict requires `Authorization: Bearer <service_token>`.",
    )
    verdict_timeout_ms: int = 150

    # --- hold queue (fail-to-human) -----------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://enforcement:enforcement@localhost:5432/enforcement",
        description="DSN for the hold-queue store.",
    )
    hold_timeout_s: int = 3600
    reviewer_token: str | None = Field(
        default=None,
        description="If set, the /holds review API requires `Authorization: Bearer <reviewer_token>`.",
    )
    reviewer_customer: str | None = Field(
        default=None,
        description=(
            "Bind the reviewer token to one tenant. If set, the /holds review API "
            "only lists/resolves holds for this `end_customer_id`. Leave unset only "
            "for an admin/single-tenant deployment (cross-tenant reviewer)."
        ),
    )
    # Recorder-client polling of a held action's resolution.
    hold_poll_interval_ms: int = 500
    max_hold_wait_s: int = 3600

    # --- ops ----------------------------------------------------------------
    log_level: str = "info"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide singleton. Override in tests via :func:`set_settings`."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Replace the process settings (tests only). Pass ``None`` to reset."""
    global _settings
    _settings = settings
