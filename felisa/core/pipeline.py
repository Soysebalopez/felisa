"""Pipeline de captura: structuring + embedding + insert.

Usado por la CLI (sincrono) y por el daemon (retry de cola).
"""

from __future__ import annotations

import logging
from uuid import UUID

from . import db, embeddings, structuring
from .embeddings import EmbeddingUnavailable
from .structuring import StructuredMemory, StructuringError

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    """Falla recuperable: el caller decide si reintentar o encolar."""


def process(
    texto: str,
    *,
    tipo_override: str | None = None,
    espacio_override: str | None = None,
    skip_unclassified: bool = False,
) -> tuple[UUID | None, StructuredMemory]:
    """Estructura, embeda e inserta. Devuelve (uuid, structured).

    Si `skip_unclassified` y Haiku devolvio `tags=['sin-clasificar']`, corta
    despues de estructurar y devuelve `(None, structured)` sin embed ni insert.
    Lo usa el bot de Telegram para no guardar saludos / preguntas casuales como
    memorias (el CLI `mem` no activa este flag — ahi el usuario sabe lo que esta
    mandando).

    Excepciones:
    - EmbeddingUnavailable: Ollama no responde → caller debe encolar
    - StructuringError: Haiku invalido → caller debe encolar
    - PipelineError: error generico envolviendo causas DB
    """
    structured = structuring.structure(texto)

    if tipo_override:
        if tipo_override not in structuring.VALID_TIPOS:
            raise PipelineError(
                f"tipo_override invalido: {tipo_override!r}. "
                f"Validos: {sorted(structuring.VALID_TIPOS)}"
            )
        structured.tipo = tipo_override

    if espacio_override:
        valid_spaces = {s.id for s in db.list_spaces()}
        if espacio_override not in valid_spaces:
            raise PipelineError(
                f"espacio_override invalido: {espacio_override!r}. "
                f"Disponibles: {sorted(valid_spaces)}"
            )
        structured.space_id = espacio_override

    if skip_unclassified and "sin-clasificar" in structured.tags:
        log.info("descartando memoria sin-clasificar: %r", texto[:60])
        return None, structured

    embedding = embeddings.embed(structured.contenido_estructurado)

    memory_id = db.insert_memory(
        contenido=texto,
        contenido_estructurado=structured.contenido_estructurado,
        tipo=structured.tipo,
        space_id=structured.space_id,
        proyecto=structured.proyecto,
        tags=structured.tags,
        embedding=embedding,
    )
    return memory_id, structured
