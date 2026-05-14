"""Tests del orquestador async del daemon: drainer + telegram con asyncio.gather.

Mockean ambas coroutines para no levantar pipeline / Telegram reales.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from felisa.daemon import main as daemon


def test_redact_telegram_token_in_msg() -> None:
    f = daemon._RedactTelegramToken()
    record = logging.LogRecord(
        name="httpx", level=logging.INFO, pathname="", lineno=0,
        msg="POST https://api.telegram.org/bot12345:AAHfake-token_XYZ/getUpdates",
        args=(), exc_info=None,
    )
    assert f.filter(record) is True
    assert "12345:AAHfake-token_XYZ" not in record.msg
    assert "/bot[REDACTED]/getUpdates" in record.msg


def test_redact_telegram_token_with_url_object() -> None:
    """httpx pasa httpx.URL como arg — debe redactarse via __str__ tambien."""
    class _FakeURL:
        def __str__(self) -> str:
            return "https://api.telegram.org/bot9:secret-Token_AB/getMe"

    f = daemon._RedactTelegramToken()
    record = logging.LogRecord(
        name="httpx", level=logging.INFO, pathname="", lineno=0,
        msg="HTTP Request: %s %s",
        args=("POST", _FakeURL()),
        exc_info=None,
    )
    assert f.filter(record) is True
    final = record.getMessage()
    assert "secret-Token_AB" not in final
    assert "/bot[REDACTED]/getMe" in final


def test_redact_passes_through_non_telegram_urls() -> None:
    f = daemon._RedactTelegramToken()
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname="", lineno=0,
        msg="POST https://api.anthropic.com/v1/messages",
        args=(), exc_info=None,
    )
    f.filter(record)
    assert record.msg == "POST https://api.anthropic.com/v1/messages"


async def test_run_async_drainer_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom_drainer(**_kw):
        raise RuntimeError("drainer crash")

    async def silent_telegram():
        await asyncio.Event().wait()  # nunca termina

    monkeypatch.setattr(daemon, "_queue_drainer", boom_drainer)
    monkeypatch.setattr(daemon, "_telegram_loop", silent_telegram)

    with pytest.raises(RuntimeError, match="drainer crash"):
        await daemon._run_async(interval=0, max_attempts=1)


async def test_run_async_telegram_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def silent_drainer(**_kw):
        await asyncio.Event().wait()

    async def boom_telegram():
        raise RuntimeError("telegram auth error")

    monkeypatch.setattr(daemon, "_queue_drainer", silent_drainer)
    monkeypatch.setattr(daemon, "_telegram_loop", boom_telegram)

    with pytest.raises(RuntimeError, match="telegram auth error"):
        await daemon._run_async(interval=0, max_attempts=1)


async def test_run_async_clean_shutdown_via_signal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = {"drainer": False, "telegram": False}

    async def slow_drainer(**_kw):
        started["drainer"] = True
        await asyncio.Event().wait()

    async def slow_telegram():
        started["telegram"] = True
        await asyncio.Event().wait()

    monkeypatch.setattr(daemon, "_queue_drainer", slow_drainer)
    monkeypatch.setattr(daemon, "_telegram_loop", slow_telegram)

    # Cancelar la coroutine externamente simula el handler de senal.
    task = asyncio.create_task(daemon._run_async(interval=0, max_attempts=1))
    await asyncio.sleep(0.05)
    assert started == {"drainer": True, "telegram": True}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
