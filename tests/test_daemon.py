"""Tests de felisa.daemon.main.

Mockean pipeline.process para no llamar Haiku/Ollama/Postgres reales.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from felisa.core import queue
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.queue import QueueItem
from felisa.core.structuring import StructuredMemory
from felisa.daemon import main as daemon


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue, "QUEUE_DIR", tmp_path)
    monkeypatch.setattr(queue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(queue, "LOCK_PATH", tmp_path / "queue.lock")


def _fake_structured() -> StructuredMemory:
    return StructuredMemory(
        contenido_estructurado="fake",
        tipo="decision_tecnica",
        space_id="global",
        proyecto=None,
        tags=[],
    )


def test_run_once_empty_queue() -> None:
    summary = daemon.run_once()
    assert summary == {"total": 0, "processed": 0, "failed": 0, "skipped": 0}


def test_run_once_processes_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_id = uuid4()
    monkeypatch.setattr(
        daemon.pipeline, "process",
        lambda texto, **kw: (fake_id, _fake_structured()),
    )
    queue.enqueue(QueueItem(texto="cosa para procesar"))
    queue.enqueue(QueueItem(texto="otra cosa"))

    summary = daemon.run_once()
    assert summary["processed"] == 2
    assert summary["failed"] == 0
    assert queue.count() == 0


def test_run_once_failure_keeps_in_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(texto, **kw):
        raise EmbeddingUnavailable("ollama down")

    monkeypatch.setattr(daemon.pipeline, "process", boom)
    queue.enqueue(QueueItem(texto="va a fallar"))

    summary = daemon.run_once()
    assert summary["processed"] == 0
    assert summary["failed"] == 1
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].attempts == 1
    assert "EmbeddingUnavailable" in pending[0].last_error


def test_max_attempts_skips_old_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon.pipeline, "process", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    item = QueueItem(texto="agotado", attempts=10)
    queue.enqueue(item)

    summary = daemon.run_once(max_attempts=10)
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    assert queue.count() == 1


def test_eventual_success_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def flaky(texto, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise EmbeddingUnavailable("aun no")
        return (uuid4(), _fake_structured())

    monkeypatch.setattr(daemon.pipeline, "process", flaky)
    queue.enqueue(QueueItem(texto="flaky item"))

    daemon.run_once()
    assert queue.count() == 1
    assert queue.list_pending()[0].attempts == 1

    daemon.run_once()
    assert queue.count() == 1
    assert queue.list_pending()[0].attempts == 2

    daemon.run_once()
    assert queue.count() == 0
