"""Tests del orquestador async del daemon: drainer + telegram con asyncio.gather.

Mockean ambas coroutines para no levantar pipeline / Telegram reales.
"""

from __future__ import annotations

import asyncio

import pytest

from felisa.daemon import main as daemon


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
