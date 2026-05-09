"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All application settings, sourced from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    # Railway provides plain postgresql:// — we normalise to the async driver here
    database_url: str = "postgresql+asyncpg://invoice_user:invoice_pass@localhost:5432/invoice_db"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalise_database_url(cls, v: str) -> str:
        """Ensure the URL always uses the asyncpg driver.

        Railway injects DATABASE_URL as postgresql:// or postgres://.
        SQLAlchemy async engine requires postgresql+asyncpg://.
        """
        if v.startswith("postgres://"):
            v = "postgresql+asyncpg://" + v[len("postgres://"):]
        elif v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── IMAP ─────────────────────────────────────────────────────────────────
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_pass: str = ""

    # ── SMTP ─────────────────────────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""

    # ── Slack ─────────────────────────────────────────────────────────────────
    slack_webhook_url: str = ""

    # ── Accounting Portal ────────────────────────────────────────────────────
    accounting_portal_url: str = "https://accounting.example.com"
    accounting_user: str = ""
    accounting_pass: str = ""

    # ── File Paths ────────────────────────────────────────────────────────────
    invoice_watch_folder: str = "/data/invoices/incoming"
    screenshots_dir: str = "/data/screenshots"

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Computed Properties ───────────────────────────────────────────────────
    @property
    def screenshots_path(self) -> Path:
        """Return screenshots directory as a resolved Path object."""
        p = Path(self.screenshots_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def watch_folder_path(self) -> Path:
        """Return invoice watch folder as a resolved Path object."""
        p = Path(self.invoice_watch_folder)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sync_database_url(self) -> str:
        """Synchronous psycopg2 URL for Celery tasks (no async engine)."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

    @property
    def celery_broker_url(self) -> str:
        """Redis URL used as Celery broker."""
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        """Redis URL used as Celery result backend."""
        return self.redis_url

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
