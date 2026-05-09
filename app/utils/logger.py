"""Centralised Loguru configuration with rotating file handler and JSON support."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger as _logger

from app.config import get_settings

_configured = False


def _configure_logger() -> None:
    """Set up Loguru sinks exactly once."""
    global _configured
    if _configured:
        return

    settings = get_settings()
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    # Remove default sink
    _logger.remove()

    # Coloured console output
    _logger.add(
        sys.stderr,
        level=settings.log_level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        enqueue=True,
    )

    # JSON rotating file sink — 10 MB per file, keep 7 days
    _logger.add(
        str(log_file),
        level=settings.log_level,
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        serialize=True,          # JSON format
        enqueue=True,
        backtrace=True,
        diagnose=False,           # disable in production to avoid leaking locals
    )

    _configured = True


def get_logger(name: str):
    """Return a Loguru logger bound with a `name` context field.

    Usage::

        log = get_logger(__name__)
        log.info("Processing invoice {id}", id=42)
    """
    _configure_logger()
    return _logger.bind(name=name)
