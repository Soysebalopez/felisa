"""TelegramBot: long polling + filtro por chat_id + wiring al pipeline.

El loop principal corre indefinidamente dentro del daemon como una coroutine.
Cancelarlo via SIGTERM/SIGINT termina limpio (offset persistido al disco a
medida que se procesa cada update, no al final).

Errores de red → backoff exponencial. 429 → respeta retry_after. 401 → propaga
para que el daemon caiga y LaunchAgent lo relance.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from felisa.core import pipeline, proposals, queue
from felisa.core.agent import Agent
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.proposals import Proposal
from felisa.core.queue import QueueItem
from felisa.core.structuring import StructuringError

from .api import RateLimited, TelegramAPI, TelegramAuthError, TelegramUnavailable

log = logging.getLogger(__name__)

OFFSET_PATH = Path.home() / ".felisa" / "telegram_offset"
ALLOWED_UPDATES = ["message", "callback_query"]
BACKOFF_SEQUENCE = (1, 2, 5, 15, 30)
TYPING_INTERVAL = 4.0
CALLBACK_PREFIX = "prop"

# Funcion que toma (bytes, mime) y devuelve transcript. Inyectada por el
# daemon cuando whisper.py este disponible (WHI-682).
TranscribeFn = Callable[[bytes, str], Awaitable[str]]


class TelegramBot:
    def __init__(
        self,
        api: TelegramAPI,
        *,
        chat_id: int,
        agent: Agent | None = None,
        transcribe: TranscribeFn | None = None,
        offset_path: Path = OFFSET_PATH,
    ) -> None:
        self._api = api
        self._chat_id = chat_id
        self._agent = agent
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
        try:
            callback = update.get("callback_query")
            if callback is not None:
                await self._handle_callback_query(callback)
                return

            message = update.get("message")
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
        if self._agent is not None:
            await self._chat_with_agent(texto)
        else:
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

        if self._agent is not None:
            await self._chat_with_agent(transcript, voz=True)
        else:
            await self._process_and_confirm(transcript, voz=True)

    async def _chat_with_agent(self, texto: str, *, voz: bool = False) -> None:
        """Pasa el mensaje al agente y manda la respuesta final al usuario.

        Si la respuesta de Telegram va a tardar (Sonnet + tool use puede tomar
        varios segundos), corremos un loop de `sendChatAction(typing)` en
        paralelo para que el usuario vea "escribiendo...".
        """
        assert self._agent is not None
        typing_task = asyncio.create_task(self._typing_loop())
        try:
            reply = await asyncio.to_thread(self._agent.chat, texto)
        except (EmbeddingUnavailable, StructuringError) as exc:
            log.info("agente: tool recuperable fallo (%s), encolando texto crudo", exc)
            queue.enqueue(QueueItem(texto=texto))
            await self._reply("No pude procesar ahora (lo encole para reintento).")
            return
        except Exception as exc:
            log.exception("agente fallo: %s", exc)
            await self._reply("No pude responder (error inesperado).")
            return
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

        if not reply.strip():
            log.warning("agente devolvio respuesta vacia")
            return

        if voz and len(texto) > 0:
            reply = f"{reply}\n\n_{texto[:80].strip()}_"
            await self._reply(reply, parse_mode="Markdown")
        else:
            await self._reply(reply)

    async def _typing_loop(self) -> None:
        try:
            while True:
                with contextlib.suppress(
                    TelegramUnavailable, TelegramAuthError, RateLimited,
                ):
                    await self._api.send_chat_action(chat_id=self._chat_id, action="typing")
                await asyncio.sleep(TYPING_INTERVAL)
        except asyncio.CancelledError:
            raise

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

    async def dispatch_pending_proposals(self) -> int:
        """Envia las propuestas pendientes sin telegram_message_id al chat configurado.

        Cada propuesta se manda con un inline keyboard (Guardar/Descartar/Mas tarde).
        Devuelve cuantas fueron enviadas en esta vuelta. El daemon llama este
        metodo periodicamente como parte de su loop.
        """
        sent = 0
        for proposal in proposals.list_pending():
            if proposal.telegram_message_id is not None:
                continue
            text = _format_proposal_message(proposal)
            keyboard = _build_proposal_keyboard(proposal.id)
            try:
                response = await self._api.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            except (TelegramUnavailable, RateLimited) as exc:
                log.warning("no pude enviar propuesta %s: %s", proposal.id[:8], exc)
                continue
            except TelegramAuthError:
                raise
            message_id = response.get("message_id") if isinstance(response, dict) else None
            if isinstance(message_id, int):
                proposals.set_telegram_message_id(proposal.id, message_id)
                sent += 1
        return sent

    async def _handle_callback_query(self, callback: dict) -> None:
        callback_id = callback.get("id")
        chat = (callback.get("message") or {}).get("chat") or {}
        if chat.get("id") != self._chat_id:
            log.warning(
                "ignorando callback de chat ajeno %s (esperaba %s)",
                chat.get("id"), self._chat_id,
            )
            if isinstance(callback_id, str):
                await self._ack_callback(callback_id)
            return

        data = callback.get("data") or ""
        message = callback.get("message") or {}
        message_id = message.get("message_id")
        proposal_id, action = _parse_callback_data(data)

        if proposal_id is None or action is None:
            log.warning("callback_data invalido: %r", data)
            if isinstance(callback_id, str):
                await self._ack_callback(callback_id, text="no entendi")
            return

        proposal = proposals.get(proposal_id)
        if proposal is None:
            log.warning("callback para propuesta inexistente: %s", proposal_id)
            if isinstance(callback_id, str):
                await self._ack_callback(callback_id, text="propuesta no encontrada")
            return

        if action == "defer":
            if isinstance(callback_id, str):
                await self._ack_callback(callback_id, text="te aviso de nuevo despues")
            return

        if action == "reject":
            proposals.mark(proposal.id, "rejected")
            if isinstance(message_id, int):
                await self._edit_proposal_message(
                    message_id, _format_rejected_message(proposal),
                )
            if isinstance(callback_id, str):
                await self._ack_callback(callback_id, text="descartada")
            return

        if action == "approve":
            try:
                memory_id, structured = await asyncio.to_thread(
                    pipeline.process,
                    proposal.texto,
                    tipo_override=proposal.tipo_sugerido,
                )
            except (EmbeddingUnavailable, StructuringError) as exc:
                log.warning("pipeline fallo aprobando propuesta %s: %s", proposal.id[:8], exc)
                if isinstance(callback_id, str):
                    await self._ack_callback(callback_id, text="no pude guardar")
                return
            except Exception as exc:
                log.exception("pipeline fallo no recuperable: %s", exc)
                if isinstance(callback_id, str):
                    await self._ack_callback(callback_id, text="error guardando")
                return

            if memory_id is None:
                if isinstance(callback_id, str):
                    await self._ack_callback(callback_id, text="no se pudo clasificar")
                return

            proposals.mark(proposal.id, "approved")
            if isinstance(message_id, int):
                await self._edit_proposal_message(
                    message_id,
                    _format_approved_message(proposal, memory_id, structured.space_id),
                )
            if isinstance(callback_id, str):
                await self._ack_callback(callback_id, text="guardada")
            return

        log.warning("accion desconocida en callback: %r", action)
        if isinstance(callback_id, str):
            await self._ack_callback(callback_id)

    async def _edit_proposal_message(self, message_id: int, text: str) -> None:
        try:
            await self._api.edit_message_text(
                chat_id=self._chat_id,
                message_id=message_id,
                text=text,
                parse_mode="Markdown",
            )
        except (TelegramUnavailable, TelegramAuthError, RateLimited) as exc:
            log.warning("no pude editar mensaje %d: %s", message_id, exc)

    async def _ack_callback(self, callback_id: str, *, text: str | None = None) -> None:
        try:
            await self._api.answer_callback_query(callback_query_id=callback_id, text=text)
        except (TelegramUnavailable, TelegramAuthError, RateLimited) as exc:
            log.warning("no pude ack callback %s: %s", callback_id, exc)


def _format_proposal_message(p: Proposal) -> str:
    return (
        "Guardar esta memoria?\n\n"
        f"> {p.texto}\n\n"
        f"_{p.contexto}_\n"
        f"Tipo: {p.tipo_sugerido} · Confianza: {p.confianza:.0%}"
    )


def _format_approved_message(p: Proposal, memory_id, space_id: str) -> str:
    return (
        "Guardada.\n\n"
        f"> {p.texto}\n\n"
        f"_{p.contexto}_\n"
        f"id: `{memory_id}` · espacio: {space_id}"
    )


def _format_rejected_message(p: Proposal) -> str:
    return (
        "Descartada.\n\n"
        f"> {p.texto}\n\n"
        f"_{p.contexto}_"
    )


def _build_proposal_keyboard(proposal_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Guardar", "callback_data": f"{CALLBACK_PREFIX}:{proposal_id}:approve"},
                {"text": "Descartar", "callback_data": f"{CALLBACK_PREFIX}:{proposal_id}:reject"},
                {"text": "Mas tarde", "callback_data": f"{CALLBACK_PREFIX}:{proposal_id}:defer"},
            ]
        ]
    }


def _parse_callback_data(data: str) -> tuple[str | None, str | None]:
    if not data.startswith(f"{CALLBACK_PREFIX}:"):
        return None, None
    parts = data.split(":", 2)
    if len(parts) != 3:
        return None, None
    _, proposal_id, action = parts
    if action not in {"approve", "reject", "defer"}:
        return None, None
    return proposal_id, action
