"""Tests de felisa.core.queue.

Usan tmp_path para no tocar la cola real del usuario en ~/.felisa/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from felisa.core import queue as q


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(q, "QUEUE_DIR", tmp_path)
    monkeypatch.setattr(q, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(q, "LOCK_PATH", tmp_path / "queue.lock")


def test_empty_queue_returns_empty_list() -> None:
    assert q.list_pending() == []
    assert q.count() == 0


def test_enqueue_and_list() -> None:
    item = q.QueueItem(texto="primer item")
    q.enqueue(item)
    pending = q.list_pending()
    assert len(pending) == 1
    assert pending[0].texto == "primer item"
    assert pending[0].id == item.id
    assert q.count() == 1


def test_enqueue_preserves_overrides() -> None:
    item = q.QueueItem(
        texto="con override",
        tipo_override="patron",
        espacio_override="simplistic",
    )
    q.enqueue(item)
    pending = q.list_pending()
    assert pending[0].tipo_override == "patron"
    assert pending[0].espacio_override == "simplistic"


def test_remove_existing() -> None:
    item = q.QueueItem(texto="para borrar")
    q.enqueue(item)
    assert q.remove(item.id) is True
    assert q.list_pending() == []


def test_remove_nonexistent() -> None:
    assert q.remove("no-existe") is False


def test_update_replaces_item() -> None:
    item = q.QueueItem(texto="original")
    q.enqueue(item)
    item.attempts = 3
    item.last_error = "ollama down"
    q.update(item)
    pending = q.list_pending()
    assert len(pending) == 1
    assert pending[0].attempts == 3
    assert pending[0].last_error == "ollama down"


def test_multiple_items_order_preserved() -> None:
    for i in range(5):
        q.enqueue(q.QueueItem(texto=f"item-{i}"))
    pending = q.list_pending()
    textos = [p.texto for p in pending]
    assert textos == ["item-0", "item-1", "item-2", "item-3", "item-4"]
