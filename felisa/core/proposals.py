"""Cola de propuestas de memoria persistida en `~/.felisa/proposals.json`.

El hook de Claude Code (`felisa/hooks/session_end.py`) escribe candidatos detectados
por Haiku al cerrar cada sesion. El daemon los recoge y los expone via Telegram
(o `mem propuestas` desde CLI) para que el usuario apruebe o descarte uno por uno.

Formato: array JSON. Cada propuesta:
{
  "id": "uuid",
  "texto": "frase concisa que se guardaria como memoria",
  "contexto": "1 frase de por que importa",
  "tipo_sugerido": "decision_tecnica | patron | framework | modo_trabajo | contexto_proyecto | global",
  "confianza": 0.78,
  "source": "hook:session_end" | "hook:stop" | "manual",
  "created_at": "ISO 8601 UTC",
  "expires_at": "ISO 8601 UTC",
  "status": "pending" | "approved" | "rejected" | "expired",
  "telegram_message_id": null | int
}
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

PROPOSALS_DIR = Path.home() / ".felisa"
PROPOSALS_PATH = PROPOSALS_DIR / "proposals.json"
LOCK_PATH = PROPOSALS_DIR / "proposals.lock"

DEFAULT_TTL = timedelta(hours=24)

ProposalStatus = Literal["pending", "approved", "rejected", "expired"]
VALID_STATUSES: frozenset[str] = frozenset(
    ("pending", "approved", "rejected", "expired")
)


@dataclass(slots=True)
class Proposal:
    texto: str
    contexto: str
    tipo_sugerido: str
    confianza: float
    source: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    expires_at: str = field(
        default_factory=lambda: (datetime.now(UTC) + DEFAULT_TTL).isoformat()
    )
    status: str = "pending"
    telegram_message_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Proposal:
        return cls(**data)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        ref = now or datetime.now(UTC)
        return datetime.fromisoformat(self.expires_at) <= ref


def _ensure_dir() -> None:
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def _file_lock():
    """Lock basado en archivo para coordinar hook + daemon + CLI sobre el mismo JSON."""
    _ensure_dir()
    import fcntl

    with open(LOCK_PATH, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _load() -> list[Proposal]:
    if not PROPOSALS_PATH.exists():
        return []
    raw = PROPOSALS_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    items = json.loads(raw)
    return [Proposal.from_dict(d) for d in items]


def _save(items: list[Proposal]) -> None:
    _ensure_dir()
    tmp = PROPOSALS_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, PROPOSALS_PATH)


def add(proposal: Proposal) -> None:
    """Append idempotente: si el id ya existe, no duplica."""
    with _file_lock():
        items = _load()
        if any(i.id == proposal.id for i in items):
            return
        items.append(proposal)
        _save(items)


def list_all() -> list[Proposal]:
    with _file_lock():
        return _load()


def list_pending() -> list[Proposal]:
    """Propuestas con status=pending y no vencidas. Las vencidas siguen en disco
    hasta que algun caller las marque con `expire_old()`."""
    now = datetime.now(UTC)
    with _file_lock():
        return [
            p for p in _load() if p.status == "pending" and not p.is_expired(now=now)
        ]


def get(proposal_id: str) -> Proposal | None:
    with _file_lock():
        for p in _load():
            if p.id == proposal_id:
                return p
        return None


def mark(proposal_id: str, status: ProposalStatus) -> bool:
    """Cambia el status de una propuesta. Devuelve False si no existe."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status invalido: {status!r}. Validos: {sorted(VALID_STATUSES)}")
    with _file_lock():
        items = _load()
        for idx, p in enumerate(items):
            if p.id == proposal_id:
                items[idx].status = status
                _save(items)
                return True
        return False


def set_telegram_message_id(proposal_id: str, message_id: int) -> bool:
    """Persiste el message_id de Telegram para poder editar el mensaje cuando llegue el callback."""
    with _file_lock():
        items = _load()
        for idx, p in enumerate(items):
            if p.id == proposal_id:
                items[idx].telegram_message_id = message_id
                _save(items)
                return True
        return False


def expire_old() -> int:
    """Marca como `expired` todas las pendientes vencidas. Devuelve cuantas fueron afectadas."""
    now = datetime.now(UTC)
    with _file_lock():
        items = _load()
        affected = 0
        for idx, p in enumerate(items):
            if p.status == "pending" and p.is_expired(now=now):
                items[idx].status = "expired"
                affected += 1
        if affected:
            _save(items)
        return affected


def count(*, status: ProposalStatus | None = None) -> int:
    with _file_lock():
        items = _load()
        if status is None:
            return len(items)
        return sum(1 for p in items if p.status == status)
