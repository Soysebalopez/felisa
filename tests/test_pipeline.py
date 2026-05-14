"""Tests del pipeline (structure + embed + insert).

Mockean structuring/embeddings/db para verificar la logica de orquestacion del
pipeline en aislamiento (sin Haiku, sin Cloudflare, sin Postgres).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from felisa.core import pipeline
from felisa.core.structuring import StructuredMemory


def _structured(tags: list[str] | None = None) -> StructuredMemory:
    return StructuredMemory(
        contenido_estructurado="fake",
        tipo="global",
        space_id="global",
        proyecto=None,
        tags=tags if tags is not None else [],
    )


def test_skip_unclassified_returns_none_without_inserting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline.structuring, "structure",
        lambda texto: _structured(tags=["sin-clasificar"]),
    )
    embedded = []
    inserted = []
    monkeypatch.setattr(
        pipeline.embeddings, "embed", lambda t: embedded.append(t) or [0.0] * 384,
    )
    monkeypatch.setattr(
        pipeline.db, "insert_memory", lambda **kw: inserted.append(kw) or uuid4(),
    )

    memory_id, structured = pipeline.process("hola test", skip_unclassified=True)
    assert memory_id is None
    assert structured.tags == ["sin-clasificar"]
    assert embedded == []
    assert inserted == []


def test_skip_unclassified_off_inserts_anyway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin la flag (default), incluso sin-clasificar se guarda — comportamiento del CLI mem."""
    fake_id = uuid4()
    monkeypatch.setattr(
        pipeline.structuring, "structure",
        lambda texto: _structured(tags=["sin-clasificar"]),
    )
    monkeypatch.setattr(pipeline.embeddings, "embed", lambda t: [0.0] * 384)
    inserted = []
    monkeypatch.setattr(
        pipeline.db, "insert_memory", lambda **kw: inserted.append(kw) or fake_id,
    )

    memory_id, structured = pipeline.process("hola test")
    assert memory_id == fake_id
    assert len(inserted) == 1


def test_classified_memory_proceeds_normally_with_skip_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con la flag, una memoria bien clasificada igual se guarda."""
    fake_id = uuid4()
    monkeypatch.setattr(
        pipeline.structuring, "structure",
        lambda texto: _structured(tags=["pgvector", "railway"]),
    )
    monkeypatch.setattr(pipeline.embeddings, "embed", lambda t: [0.1] * 384)
    monkeypatch.setattr(pipeline.db, "insert_memory", lambda **kw: fake_id)

    memory_id, _ = pipeline.process("decidi pgvector", skip_unclassified=True)
    assert memory_id == fake_id
