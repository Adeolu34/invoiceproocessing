"""Tests for retry decorators — async and sync variants."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.utils.retry import retry_async, retry_sync


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self) -> None:
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01)
        async def always_succeeds() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await always_succeeds()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self) -> None:
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01, backoff=1.0)
        async def fails_twice() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"

        result = await fails_twice()
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self) -> None:
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01, backoff=1.0)
        async def always_fails() -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("always bad")

        with pytest.raises(RuntimeError, match="always bad"):
            await always_fails()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_only_retries_specified_exceptions(self) -> None:
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        async def raises_type_error() -> None:
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            await raises_type_error()

        # Should not retry — TypeError is not in exceptions tuple
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff_delay(self) -> None:
        delays: list[float] = []
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds: float) -> None:
            delays.append(seconds)

        call_count = 0

        @retry_async(max_attempts=4, delay=0.1, backoff=2.0)
        async def always_fails() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("fail")

        import unittest.mock as mock
        with mock.patch("asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(ValueError):
                await always_fails()

        assert len(delays) == 3  # 3 sleeps for 4 attempts
        assert delays[0] == pytest.approx(0.1, rel=1e-3)
        assert delays[1] == pytest.approx(0.2, rel=1e-3)
        assert delays[2] == pytest.approx(0.4, rel=1e-3)

    @pytest.mark.asyncio
    async def test_preserves_function_return_value(self) -> None:
        @retry_async(max_attempts=2, delay=0.01)
        async def returns_dict() -> dict:
            return {"key": "value", "number": 42}

        result = await returns_dict()
        assert result == {"key": "value", "number": 42}


class TestRetrySync:
    def test_succeeds_first_attempt(self) -> None:
        call_count = 0

        @retry_sync(max_attempts=3, delay=0.01)
        def always_succeeds() -> int:
            nonlocal call_count
            call_count += 1
            return 99

        assert always_succeeds() == 99
        assert call_count == 1

    def test_retries_then_succeeds(self) -> None:
        call_count = 0

        @retry_sync(max_attempts=3, delay=0.01, backoff=1.0)
        def fails_twice() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("retry me")
            return "done"

        assert fails_twice() == "done"
        assert call_count == 3

    def test_raises_after_max_attempts(self) -> None:
        call_count = 0

        @retry_sync(max_attempts=2, delay=0.01)
        def always_fails() -> None:
            nonlocal call_count
            call_count += 1
            raise IOError("disk full")

        with pytest.raises(IOError, match="disk full"):
            always_fails()

        assert call_count == 2

    def test_does_not_retry_unspecified_exception(self) -> None:
        call_count = 0

        @retry_sync(max_attempts=5, delay=0.01, exceptions=(ValueError,))
        def raises_key_error() -> None:
            nonlocal call_count
            call_count += 1
            raise KeyError("missing")

        with pytest.raises(KeyError):
            raises_key_error()

        assert call_count == 1
