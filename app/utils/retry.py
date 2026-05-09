"""Retry decorators for async and sync functions with exponential backoff."""

from __future__ import annotations

import asyncio
import functools
import time
from pathlib import Path
from typing import Any, Callable, Sequence, Type

from app.utils.logger import get_logger

log = get_logger(__name__)


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Sequence[Type[BaseException]] = (Exception,),
    screenshots_path: Path | None = None,
):
    """Decorator that retries an async function with exponential backoff.

    Args:
        max_attempts: Maximum number of total attempts (including the first).
        delay: Initial delay in seconds between attempts.
        backoff: Multiplier applied to delay after each failure.
        exceptions: Tuple of exception types that trigger a retry.
        screenshots_path: If provided, captures a Playwright screenshot on final
            failure when a ``page`` argument is detected in the wrapped function's
            call args.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            last_exc: BaseException | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except tuple(exceptions) as exc:  # type: ignore[misc]
                    last_exc = exc
                    log.warning(
                        "Attempt {attempt}/{max} failed for {func}: {exc}",
                        attempt=attempt,
                        max=max_attempts,
                        func=func.__qualname__,
                        exc=repr(exc),
                    )

                    if attempt == max_attempts:
                        # Final failure — optionally capture screenshot
                        if screenshots_path is not None:
                            page = _find_playwright_page(args, kwargs)
                            if page is not None:
                                await _capture_screenshot_async(
                                    page, func.__qualname__, screenshots_path
                                )
                        log.error(
                            "All {max} attempts exhausted for {func}",
                            max=max_attempts,
                            func=func.__qualname__,
                        )
                        raise

                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


def retry_sync(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Sequence[Type[BaseException]] = (Exception,),
    screenshots_path: Path | None = None,
):
    """Decorator that retries a synchronous function with exponential backoff.

    Args:
        max_attempts: Maximum number of total attempts (including the first).
        delay: Initial delay in seconds between attempts.
        backoff: Multiplier applied to delay after each failure.
        exceptions: Tuple of exception types that trigger a retry.
        screenshots_path: Reserved for API parity with retry_async; Playwright
            screenshot capture is not performed in sync context.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            last_exc: BaseException | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except tuple(exceptions) as exc:  # type: ignore[misc]
                    last_exc = exc
                    log.warning(
                        "Attempt {attempt}/{max} failed for {func}: {exc}",
                        attempt=attempt,
                        max=max_attempts,
                        func=func.__qualname__,
                        exc=repr(exc),
                    )

                    if attempt == max_attempts:
                        log.error(
                            "All {max} attempts exhausted for {func}",
                            max=max_attempts,
                            func=func.__qualname__,
                        )
                        raise

                    time.sleep(current_delay)
                    current_delay *= backoff

            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_playwright_page(args: tuple, kwargs: dict) -> Any | None:
    """Search positional and keyword arguments for a Playwright Page instance."""
    try:
        from playwright.async_api import Page  # type: ignore

        for arg in args:
            if isinstance(arg, Page):
                return arg
        for v in kwargs.values():
            if isinstance(v, Page):
                return v
    except ImportError:
        pass
    return None


async def _capture_screenshot_async(page: Any, func_name: str, dest: Path) -> None:
    """Save a Playwright screenshot to *dest* directory on best-effort basis."""
    try:
        dest.mkdir(parents=True, exist_ok=True)
        safe_name = func_name.replace(".", "_").replace("<", "").replace(">", "")
        ts = int(time.time())
        path = dest / f"failure_{safe_name}_{ts}.png"
        await page.screenshot(path=str(path), full_page=True)
        log.info("Failure screenshot saved to {path}", path=path)
    except Exception as exc:
        log.warning("Could not capture failure screenshot: {exc}", exc=exc)
