"""Modelos de dominio de Felisa."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class Memory:
    id: UUID
    contenido: str
    contenido_estructurado: str | None
    tipo: str | None
    space_id: str | None
    proyecto: str | None
    tags: list[str] = field(default_factory=list)
    proyectos_relacionados: list[str] = field(default_factory=list)
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> Memory:
        return cls(
            id=row["id"],
            contenido=row["contenido"],
            contenido_estructurado=row.get("contenido_estructurado"),
            tipo=row.get("tipo"),
            space_id=row.get("space_id"),
            proyecto=row.get("proyecto"),
            tags=list(row.get("tags") or []),
            proyectos_relacionados=list(row.get("proyectos_relacionados") or []),
            created_at=row.get("created_at"),
        )


@dataclass(slots=True)
class Space:
    id: str
    nombre: str
    descripcion: str | None = None
    activo: bool = True
    es_global: bool = False

    @classmethod
    def from_row(cls, row: dict) -> Space:
        return cls(
            id=row["id"],
            nombre=row["nombre"],
            descripcion=row.get("descripcion"),
            activo=row.get("activo", True),
            es_global=row.get("es_global", False),
        )


@dataclass(slots=True)
class SearchHit:
    """Resultado de search_memories: memoria + similitud."""

    memory: Memory
    distance: float

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance
