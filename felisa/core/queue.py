"""Cola offline persistida en `~/.felisa/queue.json`.

El CLI escribe items aca cuando no puede procesar de inmediato (Ollama caido,
Anthropic timeout, Postgres inaccesible). El daemon recorre la cola cada N
segundos reintentando los items pendientes.

Formato: array JSON. Cada item:
{
  "id": "uuid",
  "texto": "texto crudo",
  "tipo_override": "patron" | null,
  "espacio_override": "<space_id>" | null,
  "captured_at": "ISO 8601",
  "attempts": 0,
  "last_error": "string" | null
}
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

QUEUE_DIR = Path.home() / ".felisa"
QUEUE_PATH = QUEUE_DIR / "queue.json"
LOCK_PATH = QUEUE_DIR / "queue.lock"


@dataclass(slots=True)
class QueueItem:
    texto: str
    tipo_override: str | None = None
    espacio_override: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    captured_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    attempts: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> QueueItem:
        return cls(**data)


def _ensure_dir() -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def _file_lock():
    """Lock simple basado en archivo. No es perfecto pero alcanza para un solo daemon + algunos CLI procesos."""
    _ensure_dir()
    import fcntl

    with open(LOCK_PATH, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _load() -> list[QueueItem]:
    if not QUEUE_PATH.exists():
        return []
    raw = QUEUE_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    items = json.loads(raw)
    return [QueueItem.from_dict(d) for d in items]


def _save(items: list[QueueItem]) -> None:
    _ensure_dir()
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, QUEUE_PATH)


def enqueue(item: QueueItem) -> None:
    with _file_lock():
        items = _load()
        items.append(item)
        _save(items)


def list_pending() -> list[QueueItem]:
    with _file_lock():
        return _load()


def remove(item_id: str) -> bool:
    with _file_lock():
        items = _load()
        filtered = [i for i in items if i.id != item_id]
        if len(filtered) == len(items):
            return False
        _save(filtered)
        return True


def update(item: QueueItem) -> None:
    with _file_lock():
        items = _load()
        replaced = False
        for idx, existing in enumerate(items):
            if existing.id == item.id:
                items[idx] = item
                replaced = True
                break
        if not replaced:
            items.append(item)
        _save(items)


def count() -> int:
    with _file_lock():
        return len(_load())
