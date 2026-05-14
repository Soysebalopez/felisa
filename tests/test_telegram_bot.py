"""Tests de felisa.telegram.bot.

Mockean el cliente HTTP con una clase fake controlable. Sin red real, sin
credenciales reales. Cada test es chico y deterministico.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from uuid import uuid4

import pytest

from felisa.core import queue
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.structuring import StructuredMemory
from felisa.telegram import bot as bot_module
from felisa.telegram.api import RateLimited, TelegramAuthError, TelegramUnavailable
from felisa.telegram.bot import TelegramBot

CHAT_ID = 12345


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue, "QUEUE_DIR", tmp_path)
    monkeypatch.setattr(queue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(queue, "LOCK_PATH", tmp_path / "queue.lock")


def _structured(tipo: str = "decision_tecnica", space: str = "whitebay") -> StructuredMemory:
    return StructuredMemory(
        contenido_estructurado="fake",
        tipo=tipo,
        space_id=space,
        proyecto=None,
        tags=[],
    )


def _text_update(update_id: int, text: str, chat_id: int = CHAT_ID) -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


def _voice_update(update_id: int, file_id: str = "audio-1", chat_id: int = CHAT_ID) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "voice": {"file_id": file_id, "mime_type": "audio/ogg"},
        },
    }


class FakeAPI:
    """Cliente fake con cola controlable de respuestas a get_updates."""

    def __init__(self) -> None:
        self._update_batches: list = []
        self.sent: list[dict] = []
        self.file_info: dict = {"file_path": "voice/audio-1.ogg"}
        self.file_bytes: bytes = b"fake-audio-bytes"

    def queue_updates(self, *batches: list[dict]) -> None:
        for batch in batches:
            self._update_batches.append(("ok", batch))

    def queue_exception(self, exc: BaseException) -> None:
        self._update_batches.append(("raise", exc))

    async def get_updates(self, *, offset, allowed_updates=None, timeout=25):
        if not self._update_batches:
            # Cola agotada: devolvemos lista vacia, igual que Telegram cuando el long
            # poll vence sin updates. El yield evita busy-loop al event loop.
            await asyncio.sleep(0)
            return []
        kind, value = self._update_batches.pop(0)
        if kind == "raise":
            raise value
        return value

    async def send_message(self, *, chat_id, text, parse_mode=None):
        self.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
        return {"message_id": len(self.sent)}

    async def send_chat_action(self, *, chat_id, action="typing"):
        if not hasattr(self, "actions"):
            self.actions = []
        self.actions.append({"chat_id": chat_id, "action": action})
        return True

    async def get_file(self, file_id):
        return self.file_info

    async def download_file(self, file_path):
        return self.file_bytes


class FakeAgent:
    """Doble de felisa.core.agent.Agent para tests del flujo bot ↔ agente."""

    def __init__(self, reply: str = "ok"):
        self._reply = reply
        self._exc: BaseException | None = None
        self.calls: list[str] = []

    def set_reply(self, reply: str) -> None:
        self._reply = reply

    def set_raise(self, exc: BaseException) -> None:
        self._exc = exc

    def chat(self, user_input: str) -> str:
        self.calls.append(user_input)
        if self._exc:
            raise self._exc
        return self._reply


async def _run_until_quiet(bot: TelegramBot, *, timeout: float = 0.5) -> None:
    """Lanza el loop del bot y lo cancela cuando ya proceso todos los updates encolados."""
    task = asyncio.create_task(bot.run())
    await asyncio.sleep(timeout)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_filter_rejects_foreign_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI()
    api.queue_updates([_text_update(1, "no soy yo", chat_id=99999)])

    called = []
    monkeypatch.setattr(
        bot_module.pipeline, "process",
        lambda texto, **kw: called.append(texto) or (uuid4(), _structured()),
    )

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")
    await _run_until_quiet(bot)

    assert called == []
    assert api.sent == []
    # offset igual se guarda para no re-recibir el update
    assert (tmp_path / "off").read_text() == "1"


async def test_text_message_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI()
    api.queue_updates([_text_update(7, "decidi usar Postgres con pgvector")])

    received: list[str] = []

    def fake_process(texto, **_kw):
        received.append(texto)
        return uuid4(), _structured(tipo="decision_tecnica", space="whitebay")

    monkeypatch.setattr(bot_module.pipeline, "process", fake_process)

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")
    await _run_until_quiet(bot)

    assert received == ["decidi usar Postgres con pgvector"]
    assert len(api.sent) == 1
    assert api.sent[0]["text"] == "Guardado · decision_tecnica · whitebay"
    assert (tmp_path / "off").read_text() == "7"


async def test_offset_persisted_between_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offset_path = tmp_path / "off"
    offset_path.write_text("42")

    api = FakeAPI()
    api.queue_updates([_text_update(43, "siguiente")])

    seen_offsets: list[int | None] = []
    real_get_updates = api.get_updates

    async def spy(**kwargs):
        seen_offsets.append(kwargs.get("offset"))
        return await real_get_updates(**kwargs)

    api.get_updates = spy  # type: ignore[assignment]
    monkeypatch.setattr(
        bot_module.pipeline, "process",
        lambda texto, **kw: (uuid4(), _structured()),
    )

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=offset_path)
    await _run_until_quiet(bot)

    assert seen_offsets[0] == 43  # offset persistido + 1
    assert offset_path.read_text() == "43"


async def test_unclassified_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cuando Haiku marca sin-clasificar, pipeline.process devuelve (None, _)
    y el bot responde sin guardar."""
    api = FakeAPI()
    api.queue_updates([_text_update(1, "hola")])

    received: list[str] = []

    def fake_process(texto, *, skip_unclassified=False, **_kw):
        received.append(texto)
        # Simulamos el comportamiento real del pipeline con la flag activa
        if skip_unclassified:
            return None, _structured(tipo="global", space="global")
        return uuid4(), _structured()

    monkeypatch.setattr(bot_module.pipeline, "process", fake_process)

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")
    await _run_until_quiet(bot)

    assert received == ["hola"]  # pipeline fue llamado
    assert len(api.sent) == 1
    assert "no entendi" in api.sent[0]["text"].lower()


async def test_pipeline_failure_enqueues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI()
    api.queue_updates([_text_update(1, "embedding caido")])

    def boom(texto, **_kw):
        raise EmbeddingUnavailable("cloudflare timeout")

    monkeypatch.setattr(bot_module.pipeline, "process", boom)

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")
    await _run_until_quiet(bot)

    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].texto == "embedding caido"
    assert any("encole" in s["text"].lower() for s in api.sent)


async def test_429_respects_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI()
    api.queue_exception(RateLimited(retry_after=1))
    api.queue_updates([_text_update(1, "post 429")])

    monkeypatch.setattr(
        bot_module.pipeline, "process",
        lambda texto, **_kw: (uuid4(), _structured()),
    )

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def spy_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)  # no demorar el test

    monkeypatch.setattr(asyncio, "sleep", spy_sleep)

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")

    async def _drive():
        task = asyncio.create_task(bot.run())
        # Esperamos a que el bot consuma el RateLimited y luego el update real.
        # En lugar de yield-count fijo (flaky en CI por scheduling), esperamos
        # a la condicion observable con un timeout robusto.
        for _ in range(200):
            await real_sleep(0.01)
            if api.sent:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await _drive()
    assert 1 in sleeps  # respeto el retry_after=1
    assert len(api.sent) == 1
    assert api.sent[0]["text"] == "Guardado · decision_tecnica · whitebay"


async def test_401_stops_polling(tmp_path: Path) -> None:
    api = FakeAPI()
    api.queue_exception(TelegramAuthError("token revocado"))

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")
    with pytest.raises(TelegramAuthError):
        await bot.run()


async def test_network_failure_uses_backoff_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI()
    api.queue_exception(TelegramUnavailable("red caida"))
    api.queue_updates([_text_update(1, "post network err")])

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def spy_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", spy_sleep)
    monkeypatch.setattr(
        bot_module.pipeline, "process",
        lambda texto, **_kw: (uuid4(), _structured()),
    )

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")

    async def _drive():
        task = asyncio.create_task(bot.run())
        # Esperar a la condicion observable (sent no vacio) en lugar de
        # yield-count fijo, que es flaky en CI por scheduling.
        for _ in range(200):
            await real_sleep(0.01)
            if api.sent:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await _drive()
    assert 1 in sleeps  # primer backoff de la secuencia
    assert any(s["text"].startswith("Guardado") for s in api.sent)


async def test_voice_without_transcribe_explains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI()
    api.queue_updates([_voice_update(1)])

    called = []
    monkeypatch.setattr(
        bot_module.pipeline, "process",
        lambda texto, **kw: called.append(texto) or (uuid4(), _structured()),
    )

    bot = TelegramBot(api, chat_id=CHAT_ID, offset_path=tmp_path / "off")
    await _run_until_quiet(bot)

    assert called == []  # no se proceso pipeline
    assert len(api.sent) == 1
    assert "voz" in api.sent[0]["text"].lower()


async def test_agent_handles_text_and_replies(
    tmp_path: Path,
) -> None:
    api = FakeAPI()
    api.queue_updates([_text_update(1, "que decisiones tengo sobre Felisa?")])
    agent = FakeAgent(reply="Tenes 3 decisiones sobre Felisa: pgvector, Railway, fastmcp.")

    bot = TelegramBot(api, chat_id=CHAT_ID, agent=agent, offset_path=tmp_path / "off")
    await _run_until_quiet(bot)

    assert agent.calls == ["que decisiones tengo sobre Felisa?"]
    assert len(api.sent) == 1
    assert "pgvector" in api.sent[0]["text"]


async def test_agent_handles_voice_with_transcript(
    tmp_path: Path,
) -> None:
    api = FakeAPI()
    api.queue_updates([_voice_update(1)])

    async def fake_transcribe(audio_bytes: bytes, mime: str) -> str:
        return "decidi usar pgvector para Felisa"

    agent = FakeAgent(reply="Guardado · decision_tecnica · whitebay")
    bot = TelegramBot(
        api, chat_id=CHAT_ID, agent=agent,
        transcribe=fake_transcribe, offset_path=tmp_path / "off",
    )
    await _run_until_quiet(bot)

    assert agent.calls == ["decidi usar pgvector para Felisa"]
    assert len(api.sent) == 1
    # Para voz se agrega preview en italica del transcript original
    assert api.sent[0]["text"].startswith("Guardado · decision_tecnica · whitebay")
    assert "_decidi usar pgvector" in api.sent[0]["text"]


async def test_typing_action_sent_during_agent_call(
    tmp_path: Path,
) -> None:
    api = FakeAPI()
    api.queue_updates([_text_update(1, "hola")])

    class SlowAgent(FakeAgent):
        def chat(self, user_input):
            # Simula latencia real del agente — duracion suficiente para varios typings.
            import time
            time.sleep(0.05)
            return super().chat(user_input)

    agent = SlowAgent(reply="hola que tal")

    # Bajamos TYPING_INTERVAL para que el typing dispare durante el chat lento
    import felisa.telegram.bot as bot_mod
    original = bot_mod.TYPING_INTERVAL
    bot_mod.TYPING_INTERVAL = 0.01
    try:
        bot = TelegramBot(api, chat_id=CHAT_ID, agent=agent, offset_path=tmp_path / "off")
        await _run_until_quiet(bot)
    finally:
        bot_mod.TYPING_INTERVAL = original

    assert getattr(api, "actions", []), "esperaba al menos una llamada a send_chat_action"
    assert all(a["action"] == "typing" for a in api.actions)


async def test_agent_recoverable_error_enqueues(
    tmp_path: Path,
) -> None:
    from felisa.core.embeddings import EmbeddingUnavailable
    api = FakeAPI()
    api.queue_updates([_text_update(1, "cosa que falla")])
    agent = FakeAgent()
    agent.set_raise(EmbeddingUnavailable("cloudflare down"))

    bot = TelegramBot(api, chat_id=CHAT_ID, agent=agent, offset_path=tmp_path / "off")
    await _run_until_quiet(bot)

    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].texto == "cosa que falla"
    assert any("encole" in s["text"].lower() for s in api.sent)


async def test_voice_with_transcribe_processes_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI()
    api.queue_updates([_voice_update(1)])

    received: list[str] = []

    def fake_process(texto, **_kw):
        received.append(texto)
        return uuid4(), _structured(tipo="patron", space="global")

    monkeypatch.setattr(bot_module.pipeline, "process", fake_process)

    async def fake_transcribe(audio_bytes: bytes, mime: str) -> str:
        assert audio_bytes == b"fake-audio-bytes"
        assert mime == "audio/ogg"
        return "esto es un patron transcripto"

    bot = TelegramBot(
        api, chat_id=CHAT_ID, transcribe=fake_transcribe, offset_path=tmp_path / "off",
    )
    await _run_until_quiet(bot)

    assert received == ["esto es un patron transcripto"]
    assert len(api.sent) == 1
    assert api.sent[0]["text"].startswith("Guardado por voz · patron · global")
