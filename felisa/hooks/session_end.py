"""Hook de Claude Code que se ejecuta al cerrar una sesion.

Claude Code invoca este script con un payload JSON via stdin con la forma:

    {"session_id": "...", "transcript_path": "/path/to/transcript.jsonl", ...}

Leemos el transcript, lo pasamos a Haiku con el prompt de deteccion, filtramos
los candidatos con confianza suficiente, y los anexamos a la cola de propuestas
(`~/.felisa/proposals.json`). El daemon (corriendo via LaunchAgent) se encarga
de mandarlas por Telegram con confirmacion inline.

Tolerante a fallos: si algo se rompe (Anthropic timeout, JSON corrupto, falta
de creds), loggeamos a `~/.felisa/hook.log` y salimos con codigo 0. Romper el
hook bloquearia el cierre de la sesion en Claude Code, lo cual es peor que
perder una captura.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path

from felisa.core import proposals
from felisa.core.hook_detection import DetectionError, detect
from felisa.core.proposals import Proposal

LOG_PATH = Path.home() / ".felisa" / "hook.log"
MAX_TRANSCRIPT_CHARS = 80_000

log = logging.getLogger("felisa.hooks.session_end")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _read_payload(stream) -> dict:
    raw = stream.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _read_transcript(transcript_path: Path) -> str:
    """Convierte el JSONL del transcript a un texto plano legible para Haiku.

    Cada linea es un evento. Nos interesan los turns de usuario y asistente con
    contenido textual; ignoramos eventos de tool use, system, etc.
    """
    lines: list[str] = []
    with transcript_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            text = _extract_text(event)
            if text:
                role = event.get("type") or event.get("role") or "?"
                lines.append(f"[{role}] {text}")
    full = "\n\n".join(lines)
    # Truncar si el transcript es enorme; Haiku tiene contexto amplio pero no
    # queremos quemar tokens en sesiones marathon.
    if len(full) > MAX_TRANSCRIPT_CHARS:
        return full[-MAX_TRANSCRIPT_CHARS:]
    return full


def _extract_text(event: dict) -> str:
    """Saca el texto de un evento del transcript de Claude Code.

    El formato exacto depende de la version; manejamos las variantes mas
    comunes: `content` como string, como lista de bloques `{type, text}`, o
    `message.content` con la misma estructura.
    """
    content = event.get("content")
    if content is None:
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def run(payload: dict) -> int:
    transcript_path_raw = payload.get("transcript_path")
    if not transcript_path_raw:
        log.info("payload sin transcript_path, nada que procesar")
        return 0
    transcript_path = Path(transcript_path_raw).expanduser()
    if not transcript_path.exists():
        log.warning("transcript_path no existe: %s", transcript_path)
        return 0

    try:
        transcript_text = _read_transcript(transcript_path)
    except OSError as exc:
        log.warning("no pude leer transcript %s: %s", transcript_path, exc)
        return 0

    if not transcript_text.strip():
        log.info("transcript vacio, nada que procesar")
        return 0

    try:
        candidates = detect(transcript_text)
    except DetectionError as exc:
        log.info("Haiku detection fallo: %s", exc)
        return 0
    except Exception as exc:
        log.warning("error inesperado llamando a Haiku: %s", exc)
        return 0

    if not candidates:
        log.info("session %s: 0 candidatos", payload.get("session_id", "?"))
        return 0

    source = "hook:session_end"
    for c in candidates:
        proposal = Proposal(
            texto=c.texto,
            contexto=c.contexto,
            tipo_sugerido=c.tipo_sugerido,
            confianza=c.confianza,
            source=source,
        )
        proposals.add(proposal)
    log.info(
        "session %s: %d candidatos agregados a la queue",
        payload.get("session_id", "?"), len(candidates),
    )
    return 0


def main() -> int:
    _setup_logging()
    try:
        payload = _read_payload(sys.stdin)
    except json.JSONDecodeError as exc:
        log.warning("stdin no es JSON valido: %s", exc)
        return 0
    except Exception as exc:
        log.warning("fallo leyendo stdin: %s", exc)
        return 0
    try:
        return run(payload)
    except Exception as exc:
        log.exception("error inesperado en hook: %s", exc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
