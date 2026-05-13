"""Tests de operaciones CRUD sobre spaces.

Usan un id de prueba con prefijo `pytest_` y limpian en fixture.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator

import pytest

from felisa.core import db


def _random_embedding() -> list[float]:
    rng = random.Random()
    return [rng.uniform(-1.0, 1.0) for _ in range(768)]


@pytest.fixture
def test_space() -> Iterator[str]:
    """Genera un id unico y lo borra al final del test."""
    sid = f"pytest_{uuid.uuid4().hex[:8]}"
    yield sid
    # Cleanup robusto
    try:
        db.delete_space(sid, force=True)
    except Exception:
        pass


def test_create_space_basic(test_space: str) -> None:
    space = db.create_space(test_space, "Pytest space", descripcion="para tests")
    assert space.id == test_space
    assert space.nombre == "Pytest space"
    assert space.activo is True
    assert space.es_global is False


def test_create_space_validates_id() -> None:
    with pytest.raises(ValueError, match="id invalido"):
        db.create_space("Invalid Space", "x")
    with pytest.raises(ValueError, match="id invalido"):
        db.create_space("UPPERCASE", "x")
    with pytest.raises(ValueError, match="id invalido"):
        db.create_space("123starts_with_num", "x")
    with pytest.raises(ValueError, match="id invalido"):
        db.create_space("con espacio", "x")


def test_archive_unarchive(test_space: str) -> None:
    db.create_space(test_space, "x")
    assert db.archive_space(test_space) is True
    actives = {s.id for s in db.list_spaces()}
    assert test_space not in actives
    todos = {s.id for s in db.list_spaces(activos_solamente=False)}
    assert test_space in todos
    assert db.unarchive_space(test_space) is True
    actives_again = {s.id for s in db.list_spaces()}
    assert test_space in actives_again


def test_archive_protected_raises() -> None:
    with pytest.raises(db.SpaceProtected):
        db.archive_space("global")


def test_count_memories_in_space(test_space: str) -> None:
    db.create_space(test_space, "x")
    assert db.count_memories_in_space(test_space) == 0
    mid = db.insert_memory(
        contenido="memoria de test",
        space_id=test_space,
        tipo="global",
        embedding=_random_embedding(),
    )
    assert db.count_memories_in_space(test_space) == 1
    db.delete_memory(mid)


def test_delete_space_empty(test_space: str) -> None:
    db.create_space(test_space, "x")
    deleted = db.delete_space(test_space)
    assert deleted == 0
    todos = {s.id for s in db.list_spaces(activos_solamente=False)}
    assert test_space not in todos


def test_delete_space_with_memories_blocks(test_space: str) -> None:
    db.create_space(test_space, "x")
    db.insert_memory(
        contenido="bloquea borrado",
        space_id=test_space,
        tipo="global",
        embedding=_random_embedding(),
    )
    with pytest.raises(db.SpaceNotEmpty):
        db.delete_space(test_space)
    # cleanup hecho por la fixture con force=True


def test_delete_space_with_force_cascades(test_space: str) -> None:
    db.create_space(test_space, "x")
    db.insert_memory(
        contenido="se va con todo",
        space_id=test_space,
        tipo="global",
        embedding=_random_embedding(),
    )
    n = db.delete_space(test_space, force=True)
    assert n == 1


def test_delete_protected_raises() -> None:
    with pytest.raises(db.SpaceProtected):
        db.delete_space("global", force=True)
