"""TelegramBot: long polling + filtro por chat_id + wiring al pipeline.

El loop principal corre indefinidamente dentro del daemon como una coroutine.
Cancelarlo via SIGTERM/SIGINT termina limpio (offset persistido al disco a
medida que se procesa cada update, no al final).

Errores de red → backoff exponencial. 429 → respeta retry_after. 401 → propaga
para que el daemon caiga y LaunchAgent lo relance.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from felisa.core import pipeline, queue
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.queue import QueueItem
from felisa.core.structuring import StructuringError

from .api import RateLimited, TelegramAPI, TelegramAuthError, TelegramUnavailable

log = logging.getLogger(__name__)

OFFSET_PATH = Path.home() / ".felisa" / "telegram_offset"
ALLOWED_UPDATES = ["message"]
BACKOFF_SEQUENCE = (1, 2, 5, 15, 30)

# Funcion que toma (bytes, mime) y devuelve transcript. Inyectada por el
# daemon cuando whisper.py este disponible (WHI-682).
TranscribeFn = Callable[[bytes, str], Awaitable[str]]


class TelegramBot:
    def __init__(
        self,
        api: TelegramAPI,
        *,
        chat_id: int,
        transcribe: TranscribeFn | None = None,
        offset_path: Path = OFFSET_PATH,
    ) -> None:
        self._api = api
        self._chat_id = chat_id
        self._transcribe = transcribe
        self._offset_path = offset_path
        self._offset: int | None = self._load_offset()

    def _load_offset(self) -> int | None:
        if not self._offset_path.exists():
            return None
        raw = self._offset_path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None

    def _save_offset(self, update_id: int) -> None:
        self._offset_path.parent.mkdir(parents=True, exist_ok=True)
        self._offset_path.write_text(str(update_id), encoding="utf-8")
        self._offset = update_id

    async def run(self) -> None:
        log.info("telegram bot iniciado (chat_id=%s, offset=%s)", self._chat_id, self._offset)
        backoff_idx = 0
        while True:
            try:
                # +1 sobre el ultimo procesado: Telegram considera ese update_id ya ack-ed.
                next_offset = (self._offset + 1) if self._offset is not None else None
                updates = await self._api.get_updates(
                    offset=next_offset,
                    allowed_updates=ALLOWED_UPDATES,
                )
            except RateLimited as rl:
                log.warning("telegram 429: durmiendo %ds", rl.retry_after)
                await asyncio.sleep(rl.retry_after)
                continue
            except TelegramAuthError:
                raise
            except TelegramUnavailable as exc:
                delay = BACKOFF_SEQUENCE[min(backoff_idx, len(BACKOFF_SEQUENCE) - 1)]
                backoff_idx += 1
                log.warning("telegram unavailable (%s); retry en %ds", exc, delay)
                await asyncio.sleep(delay)
                continue

            backoff_idx = 0
            for update in updates:
                await self._handle_update(update)

    async def _handle_update(self, update: dict) -> None:
        update_id = update.get("update_id")
        message = update.get("message")
        try:
            if message is None:
                return

            incoming_chat_id = message.get("chat", {}).get("id")
            if incoming_chat_id != self._chat_id:
                log.warning(
                    "ignorando mensaje de chat ajeno %s (esperaba %s)",
                    incoming_chat_id, self._chat_id,
                )
                return

            if "text" in message:
                await self._handle_text(message["text"])
            elif "voice" in message or "audio" in message:
                await self._handle_voice(message.get("voice") or message["audio"])
            else:
                await self._reply(
                    "No entendi ese mensaje. Mandame texto o un mensaje de voz."
                )
        finally:
            if isinstance(update_id, int):
                self._save_offset(update_id)

    async def _handle_text(self, texto: str) -> None:
        texto = texto.strip()
        if not texto:
            return
        await self._process_and_confirm(texto)

    async def _handle_voice(self, voice: dict) -> None:
        if self._transcribe is None:
            log.info("voice recibido pero transcribe no esta cableado; respondo aviso")
            await self._reply(
                "Recibi un mensaje de voz pero la transcripcion todavia no esta disponible."
            )
            return

        file_id = voice.get("file_id")
        mime = voice.get("mime_type", "audio/ogg")
        if not file_id:
            return

        try:
            info = await self._api.get_file(file_id)
            file_path = info.get("file_path")
            if not file_path:
                await self._reply("No pude bajar el audio (sin file_path).")
                return
            audio_bytes = await self._api.download_file(file_path)
        except TelegramUnavailable as exc:
            log.warning("fallo bajando audio: %s", exc)
            await self._reply("No pude bajar el audio. Probas de nuevo en un rato?")
            return

        try:
            transcript = await self._transcribe(audio_bytes, mime)
        except Exception as exc:
            log.warning("fallo transcribiendo audio: %s", exc)
            await self._reply("No pude transcribir el audio.")
            return

        if not transcript.strip():
            await self._reply("La transcripcion vino vacia.")
            return

        await self._process_and_confirm(transcript, voz=True)

    async def _process_and_confirm(self, texto: str, *, voz: bool = False) -> None:
        try:
            memory_id, structured = await asyncio.to_thread(
                pipeline.process, texto, skip_unclassified=True,
            )
        except (EmbeddingUnavailable, StructuringError) as exc:
            log.info("pipeline fallo recuperable, encolando: %s", exc)
            queue.enqueue(QueueItem(texto=texto))
            await self._reply(
                "No pude guardar ahora (lo encole para reintento)."
            )
            return
        except Exception as exc:
            log.exception("pipeline fallo no recuperable: %s", exc)
            await self._reply("No pude guardar (error inesperado).")
            return

        if memory_id is None:
            await self._reply("No entendi esto como memoria, no lo guarde.")
            return

        prefix = "Guardado por voz" if voz else "Guardado"
        preview = (
            f"\n\n_{texto[:80].strip()}_" if voz and len(texto) > 0 else ""
        )
        await self._reply(
            f"{prefix} · {structured.tipo} · {structured.space_id}{preview}",
            parse_mode="Markdown" if voz else None,
        )

    async def _reply(self, text: str, *, parse_mode: str | None = None) -> None:
        try:
            await self._api.send_message(chat_id=self._chat_id, text=text, parse_mode=parse_mode)
        except (TelegramUnavailable, TelegramAuthError, RateLimited) as exc:
            log.warning("no pude responder a Telegram: %s", exc)
