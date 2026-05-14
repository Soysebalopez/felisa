"""Tests de felisa.core.db contra Railway production.

Cada test crea memorias con tag 'pytest-cleanup' y las borra al final.
No tocar memorias reales del usuario.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator

import pytest

from felisa.core import db

CLEANUP_TAG = "pytest-cleanup"


def _random_embedding(seed: int | None = None) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(384)]


@pytest.fixture
def created_ids() -> Iterator[list[uuid.UUID]]:
    ids: list[uuid.UUID] = []
    yield ids
    for mid in ids:
        db.delete_memory(mid)


def test_list_spaces_returns_seeds() -> None:
    spaces = db.list_spaces()
    ids = {s.id for s in spaces}
    # `global` es el unico espacio que el seed inicial garantiza (sql/001_init.sql).
    # Si el usuario agrego mas espacios (via installer o agente), tambien aparecen.
    assert "global" in ids
    global_space = next(s for s in spaces if s.id == "global")
    assert global_space.es_global is True


def test_insert_and_list(created_ids: list[uuid.UUID]) -> None:
    mid = db.insert_memory(
        contenido="memoria de prueba pytest",
        contenido_estructurado="Memoria de prueba para test de insert.",
        tipo="decision_tecnica",
        space_id="global",
        proyecto="Felisa",
        tags=[CLEANUP_TAG, "test-insert"],
        embedding=_random_embedding(seed=42),
    )
    created_ids.append(mid)
    assert isinstance(mid, uuid.UUID)

    memories = db.list_memories(space="global", limit=50)
    found = next((m for m in memories if m.id == mid), None)
    assert found is not None
    assert found.contenido == "memoria de prueba pytest"
    assert found.tipo == "decision_tecnica"
    assert found.proyecto == "Felisa"
    assert CLEANUP_TAG in found.tags


def test_search_finds_inserted(created_ids: list[uuid.UUID]) -> None:
    emb = _random_embedding(seed=7)
    mid = db.insert_memory(
        contenido="busqueda semantica de prueba",
        tipo="decision_tecnica",
        space_id="global",
        tags=[CLEANUP_TAG, "test-search"],
        embedding=emb,
    )
    created_ids.append(mid)

    hits = db.search_memories(emb, limit=5)
    assert hits, "search debe devolver al menos un hit"
    top = hits[0]
    assert top.memory.id == mid
    assert top.distance < 1e-5, "el mismo embedding deberia tener distancia ~0"
    assert top.similarity > 0.99


def test_filter_by_tipo(created_ids: list[uuid.UUID]) -> None:
    mid = db.insert_memory(
        contenido="patron de prueba",
        tipo="patron",
        space_id="global",
        tags=[CLEANUP_TAG, "test-filter"],
        embedding=_random_embedding(seed=99),
    )
    created_ids.append(mid)

    patrones = db.list_memories(tipo="patron", limit=20)
    decisiones = db.list_memories(tipo="decision_tecnica", limit=20)

    assert any(m.id == mid for m in patrones)
    assert not any(m.id == mid for m in decisiones)


def test_delete_returns_true_when_existed(created_ids: list[uuid.UUID]) -> None:
    mid = db.insert_memory(
        contenido="memoria para borrar",
        tipo="global",
        space_id="global",
        tags=[CLEANUP_TAG, "test-delete"],
        embedding=_random_embedding(seed=1),
    )
    assert db.delete_memory(mid) is True
    assert db.delete_memory(mid) is False
