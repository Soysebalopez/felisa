"""Tests del tool `create_memory` del MCP server.

Mockean `pipeline.process` para verificar el shape del response y el manejo
de errores (Haiku invalido, Cloudflare caido, contenido vacio, descarte).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from felisa.core import embeddings, pipeline
from felisa.core.structuring import StructuredMemory, StructuringError
from felisa.mcp import server


def _structured() -> StructuredMemory:
    return StructuredMemory(
        contenido_estructurado="Usamos pgvector con indice HNSW.",
        tipo="decision_tecnica",
        space_id="whitebay",
        proyecto="felisa",
        tags=["pgvector", "hnsw"],
    )


def test_create_memory_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_id = uuid4()
    structured = _structured()
    captured: dict = {}

    def fake_process(texto, *, tipo_override=None, espacio_override=None, skip_unclassified=False):
        captured["texto"] = texto
        captured["tipo_override"] = tipo_override
        captured["espacio_override"] = espacio_override
        return fake_id, structured

    monkeypatch.setattr(server.pipeline, "process", fake_process)

    out = server.create_memory("  decidi pgvector  ", space="whitebay", tipo="decision_tecnica")
    assert out["id"] == str(fake_id)
    assert out["tipo"] == "decision_tecnica"
    assert out["space_id"] == "whitebay"
    assert out["proyecto"] == "felisa"
    assert out["tags"] == ["pgvector", "hnsw"]
    assert out["contenido_estructurado"].startswith("Usamos pgvector")
    assert captured["texto"] == "decidi pgvector"
    assert captured["tipo_override"] == "decision_tecnica"
    assert captured["espacio_override"] == "whitebay"


def test_create_memory_autodetect_passes_none_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_process(texto, *, tipo_override=None, espacio_override=None, skip_unclassified=False):
        captured["tipo_override"] = tipo_override
        captured["espacio_override"] = espacio_override
        return uuid4(), _structured()

    monkeypatch.setattr(server.pipeline, "process", fake_process)
    server.create_memory("texto cualquiera")
    assert captured["tipo_override"] is None
    assert captured["espacio_override"] is None


def test_create_memory_empty_content_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_process(*args, **kwargs):
        nonlocal called
        called = True
        return uuid4(), _structured()

    monkeypatch.setattr(server.pipeline, "process", fake_process)
    out = server.create_memory("   ")
    assert out == {"error": "contenido_vacio", "message": "contenido no puede estar vacio"}
    assert called is False


def test_create_memory_embeddings_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_process(*args, **kwargs):
        raise embeddings.EmbeddingUnavailable("cloudflare 503")

    monkeypatch.setattr(server.pipeline, "process", fake_process)
    out = server.create_memory("algo")
    assert out["error"] == "embeddings_unavailable"
    assert "cloudflare" in out["message"]


def test_create_memory_structuring_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_process(*args, **kwargs):
        raise StructuringError("Haiku devolvio JSON invalido")

    monkeypatch.setattr(server.pipeline, "process", fake_process)
    out = server.create_memory("algo")
    assert out["error"] == "structuring_failed"


def test_create_memory_pipeline_error_on_invalid_override(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_process(*args, **kwargs):
        raise pipeline.PipelineError("espacio_override invalido: 'foo'")

    monkeypatch.setattr(server.pipeline, "process", fake_process)
    out = server.create_memory("algo", space="foo")
    assert out["error"] == "pipeline_error"
    assert "foo" in out["message"]


def test_create_memory_returns_skipped_when_pipeline_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si pipeline.process devuelve memory_id=None (sin-clasificar), el tool reporta skipped."""
    def fake_process(*args, **kwargs):
        return None, _structured()

    monkeypatch.setattr(server.pipeline, "process", fake_process)
    out = server.create_memory("hola")
    assert out["error"] == "skipped"
