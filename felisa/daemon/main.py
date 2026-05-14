"""Felisa daemon — drena la cola offline + corre el bot de Telegram.

Cuando arranca como LaunchAgent:
- Una coroutine drena `~/.felisa/queue.json` cada `interval` segundos (pipeline.process
  → en exito: remove, en fallo: incrementa attempts).
- Otra coroutine corre el bot de Telegram con long polling. Si faltan
  credenciales (Keychain), la coroutine queda dormida sin romper el daemon.

Si cualquiera de las dos levanta excepcion no manejada, el daemon termina y
LaunchAgent lo relanza. SIGTERM/SIGINT cancela ambas limpias.

`run_once` y `--once` se mantienen sincronos para no tocar Telegram cuando se
usa el daemon en modo "vaciar cola y salir".
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import logging.handlers
import re
import signal
import sys
from pathlib import Path

from felisa.core import config, pipeline, proposals, queue
from felisa.core.agent import Agent
from felisa.core.config import MissingCredential
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.queue import QueueItem
from felisa.core.structuring import StructuringError
from felisa.telegram.api import (
    RateLimited,
    TelegramAPI,
    TelegramAuthError,
    TelegramUnavailable,
)
from felisa.telegram.bot import TelegramBot
from felisa.telegram.whisper import transcribe as whisper_transcribe

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 10
PROPOSALS_DISPATCH_INTERVAL = 5
PROPOSALS_EXPIRE_INTERVAL = 300
LOG_PATH = Path.home() / ".felisa" / "daemon.log"

log = logging.getLogger("felisa.daemon")

# httpx loguea cada request con el URL completo, y la Bot API de Telegram lleva el
# token embebido en el path (`/bot<token>/method`). Sin redaccion, daemon.log
# guarda el token en cleartext en cada poll cycle.
_TELEGRAM_TOKEN_RE = re.compile(r"/bot(\d+:[A-Za-z0-9_-]+)")


class _RedactTelegramToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Interpolamos msg % args ahora (httpx pasa la URL como objeto, no como str,
        # asi que el sub no la alcanzaria sin invocar __str__). Despues vaciamos args
        # para que el formatter no vuelva a hacer % y rompa.
        try:
            text = record.getMessage()
        except Exception:
            return True
        record.msg = _TELEGRAM_TOKEN_RE.sub("/bot[REDACTED]", text)
        record.args = ()
        return True


def _setup_logging(verbose: bool = False) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    redact = _RedactTelegramToken()
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    )
    handler.addFilter(redact)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    if verbose:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s | %(message)s"))
        stream.addFilter(redact)
        root.addHandler(stream)


def process_item(item: QueueItem, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> bool:
    """Procesa un item de la cola. Devuelve True si se completo (y se removio)."""
    if item.attempts >= max_attempts:
        log.warning(
            "item %s alcanzo max_attempts=%d, no se reintenta. Texto=%r",
            item.id[:8], max_attempts, item.texto[:80],
        )
        return False

    try:
        memory_id, structured = pipeline.process(
            item.texto,
            tipo_override=item.tipo_override,
            espacio_override=item.espacio_override,
        )
    except (EmbeddingUnavailable, StructuringError, Exception) as exc:
        item.attempts += 1
        item.last_error = f"{type(exc).__name__}: {exc}"
        queue.update(item)
        log.info(
            "item %s fallo (attempt %d/%d): %s",
            item.id[:8], item.attempts, max_attempts, item.last_error,
        )
        return False

    queue.remove(item.id)
    log.info(
        "item %s procesado → memoria %s [%s/%s]",
        item.id[:8], memory_id, structured.tipo, structured.space_id,
    )
    return True


def run_once(*, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> dict[str, int]:
    """Procesa todos los pendientes una vuelta. Devuelve resumen para tests."""
    items = queue.list_pending()
    if not items:
        return {"total": 0, "processed": 0, "failed": 0, "skipped": 0}

    processed = failed = skipped = 0
    for item in items:
        if item.attempts >= max_attempts:
            skipped += 1
            continue
        if process_item(item, max_attempts=max_attempts):
            processed += 1
        else:
            failed += 1
    return {
        "total": len(items),
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
    }


async def _queue_drainer(*, interval: int, max_attempts: int) -> None:
    """Drena la cola cada `interval` segundos hasta que sea cancelada."""
    log.info("queue drainer iniciado (interval=%ds max_attempts=%d)", interval, max_attempts)
    while True:
        try:
            summary = await asyncio.to_thread(run_once, max_attempts=max_attempts)
            if summary["total"] > 0:
                log.info("ciclo cola: %s", summary)
        except Exception:
            log.exception("error en ciclo del drainer, continuo")
        await asyncio.sleep(interval)


async def _proposals_dispatcher(bot: TelegramBot) -> None:
    """Envia propuestas pendientes via Telegram cada N segundos."""
    log.info("proposals dispatcher iniciado (interval=%ds)", PROPOSALS_DISPATCH_INTERVAL)
    while True:
        try:
            sent = await bot.dispatch_pending_proposals()
            if sent:
                log.info("dispatcher: %d propuesta(s) enviada(s)", sent)
        except TelegramAuthError:
            raise
        except (TelegramUnavailable, RateLimited) as exc:
            log.warning("dispatcher: telegram no disponible (%s)", exc)
        except Exception:
            log.exception("dispatcher: error inesperado, continuo")
        await asyncio.sleep(PROPOSALS_DISPATCH_INTERVAL)


async def _proposals_expirer() -> None:
    """Marca como expired las propuestas cuyo TTL ya vencio."""
    log.info("proposals expirer iniciado (interval=%ds)", PROPOSALS_EXPIRE_INTERVAL)
    while True:
        try:
            affected = await asyncio.to_thread(proposals.expire_old)
            if affected:
                log.info("expirer: %d propuesta(s) marcada(s) expired", affected)
        except Exception:
            log.exception("expirer: error inesperado, continuo")
        await asyncio.sleep(PROPOSALS_EXPIRE_INTERVAL)


async def _telegram_loop() -> None:
    """Long polling de Telegram + dispatcher de propuestas + expirer.

    Si faltan creds, duerme para siempre sin romper. Si Telegram esta caido o
    el bot levanta excepcion, la propaga al orquestador para que LaunchAgent
    relance el daemon entero.
    """
    try:
        token = config.get_telegram_token()
        chat_id_raw = config.get_telegram_chat_id()
    except MissingCredential as exc:
        log.warning("telegram desactivado: %s", exc)
        await asyncio.Event().wait()
        return  # pragma: no cover

    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        log.error("TELEGRAM_CHAT_ID no es numerico: %r → bot desactivado", chat_id_raw)
        await asyncio.Event().wait()
        return  # pragma: no cover

    try:
        config.get_groq_key()
        transcribe_fn = whisper_transcribe
    except MissingCredential:
        log.warning("groq no disponible: voz desactivada, texto sigue funcionando")
        transcribe_fn = None

    agent = Agent()
    async with TelegramAPI(token) as api:
        bot = TelegramBot(
            api, chat_id=chat_id, agent=agent, transcribe=transcribe_fn,
        )
        async with asyncio.TaskGroup() as tg:
            tg.create_task(bot.run(), name="telegram-poll")
            tg.create_task(_proposals_dispatcher(bot), name="proposals-dispatcher")
            tg.create_task(_proposals_expirer(), name="proposals-expirer")


async def _run_async(*, interval: int, max_attempts: int) -> None:
    """Orquesta drainer + telegram. Si cualquiera levanta, ambos paran."""
    drainer = asyncio.create_task(
        _queue_drainer(interval=interval, max_attempts=max_attempts),
        name="queue-drainer",
    )
    telegram = asyncio.create_task(_telegram_loop(), name="telegram")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_sig(signum: int) -> None:
        log.info("recibida senal %s, terminando limpio", signum)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_sig, sig)

    stop_task = asyncio.create_task(stop_event.wait(), name="stop-waiter")

    try:
        done, _pending = await asyncio.wait(
            {drainer, telegram, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for t in (drainer, telegram, stop_task):
            t.cancel()
        for t in (drainer, telegram, stop_task):
            with contextlib.suppress(asyncio.CancelledError):
                await t

    for t in done:
        if t is stop_task or t.cancelled():
            continue
        exc = t.exception()
        if exc is not None:
            raise exc


def run_forever(
    *,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> None:
    log.info("felisa daemon iniciado")
    asyncio.run(_run_async(interval=interval, max_attempts=max_attempts))
    log.info("felisa daemon shutdown completo")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="felisa-daemon")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help="segundos entre ciclos de la cola (default 60)")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--once", action="store_true",
                        help="procesar una vuelta de la cola y salir (sin Telegram)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(verbose=args.verbose)

    if args.once:
        summary = run_once(max_attempts=args.max_attempts)
        log.info("--once: %s", summary)
        return 0

    run_forever(interval=args.interval, max_attempts=args.max_attempts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
