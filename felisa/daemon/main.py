"""Felisa daemon — retry loop sobre la cola offline.

Loop infinito que cada `interval` segundos:
1. Lee items pendientes de `~/.felisa/queue.json`
2. Para cada item con attempts < max_attempts, intenta procesarlo con pipeline.process
3. En exito → remove de queue. En fallo → incrementa attempts y guarda error.

Logging a `~/.felisa/daemon.log` con rotacion (5MB, 3 backups).

Para arrancar como LaunchAgent ver `scripts/com.felisa.daemon.plist`.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import time
from pathlib import Path

from felisa.core import pipeline, queue
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.queue import QueueItem
from felisa.core.structuring import StructuringError

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 10
LOG_PATH = Path.home() / ".felisa" / "daemon.log"

log = logging.getLogger("felisa.daemon")


def _setup_logging(verbose: bool = False) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    if verbose:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s | %(message)s"))
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


def run_forever(
    *,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> None:
    log.info("felisa daemon iniciado. interval=%ds max_attempts=%d", interval, max_attempts)
    _stop = False

    def _on_sig(signum, _frame):
        nonlocal _stop
        log.info("recibida senal %s, terminando limpio", signum)
        _stop = True

    signal.signal(signal.SIGTERM, _on_sig)
    signal.signal(signal.SIGINT, _on_sig)

    while not _stop:
        try:
            summary = run_once(max_attempts=max_attempts)
            if summary["total"] > 0:
                log.info("ciclo: %s", summary)
        except Exception:
            log.exception("error fatal en ciclo, continuo igualmente")

        for _ in range(interval):
            if _stop:
                break
            time.sleep(1)

    log.info("felisa daemon shutdown completo")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="felisa-daemon")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help="segundos entre ciclos (default 60)")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--once", action="store_true", help="procesar una vuelta y salir")
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
