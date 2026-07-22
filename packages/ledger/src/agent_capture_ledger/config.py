"""Env-driven configuration for the ledger.

Every setting is overridable via ``LEDGER_<NAME>`` env vars. Production
deployments mount a single env file or a Kubernetes Secret; tests construct
``Settings(...)`` directly. There is no config file format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEDGER_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    # --- database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://ledger_app:ledger_app@localhost:5432/ledger",
        description="DSN for the application role (INSERT, SELECT only).",
    )
    database_url_retention: str | None = Field(
        default=None,
        description="DSN for the retention role (the only role allowed to DELETE).",
    )
    database_url_migrate: str | None = Field(
        default=None,
        description="DSN for a privileged role used by `ledger db migrate` (CREATE on public).",
    )
    database_pool_size: int = 10
    database_pool_max_overflow: int = 20

    # --- server -------------------------------------------------------------
    listen_host: str = "0.0.0.0"
    listen_port: int = 8443
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None

    # --- query / dashboard --------------------------------------------------
    controls_path: Path | None = Field(
        default=None,
        description="YAML control catalog for /stats.controls. None → built-in default catalog.",
    )

    # --- attestation --------------------------------------------------------
    signing_key_path: Path | None = None
    # KMS-backed Ed25519 signer (preferred): full key-version resource name.
    # When set, takes precedence over signing_key_path. Requires the `kms` extra.
    signing_kms_key: str | None = None
    signing_key_id: str = "primary"
    attestation_interval_seconds: int = 300
    attestation_sink: str = "file:///var/lib/ledger/attestations"

    # --- retention ----------------------------------------------------------
    retention_transient_days: int = 7
    retention_standard_days: int = 90
    retention_extended_days: int = 2555
    retention_hour_utc: int = 3

    # --- enforcement (layer 5) ----------------------------------------------
    # When set, the ingest path runs *advisory* enforcement evaluation over
    # accepted gated spans (never blocks ingest). Requires the
    # ``agent-capture-enforcement`` package to be installed; absent it, this is
    # a no-op even when set.
    enforcement_rules_path: Path | None = None

    # --- backpressure & ops -------------------------------------------------
    backpressure_inflight_high: int = 5000
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    metrics_enabled: bool = True

    # --- internal flags -----------------------------------------------------
    schema_version_supported: str = "1.0.0"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide singleton. Override in tests via ``set_settings``."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Replace the process settings. Tests only."""
    global _settings
    _settings = settings
