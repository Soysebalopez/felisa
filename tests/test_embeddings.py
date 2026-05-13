"""Tests de felisa.core.embeddings.

Requieren Ollama corriendo en localhost:11434 con `nomic-embed-text` pulled.
Si Ollama no esta, los tests fallan con mensaje claro.
"""

from __future__ import annotations

import pytest

from felisa.core import embeddings


def test_embed_returns_768_dim() -> None:
    vec = embeddings.embed("decision tecnica de prueba para Felisa")
    assert len(vec) == 768
    assert all(isinstance(x, float) for x in vec)


def test_embed_is_deterministic() -> None:
    a = embeddings.embed("mismo texto deberia dar mismo embedding")
    b = embeddings.embed("mismo texto deberia dar mismo embedding")
    assert a == b


def test_embed_rejects_empty() -> None:
    with pytest.raises(ValueError):
        embeddings.embed("")
    with pytest.raises(ValueError):
        embeddings.embed("   ")


def test_check_available_passes() -> None:
    embeddings.check_available()


def test_unrelated_texts_have_lower_similarity_than_related() -> None:
    base = embeddings.embed("decidimos usar Postgres con pgvector para Felisa")
    related = embeddings.embed("Felisa usa Postgres y pgvector como backend de embeddings")
    unrelated = embeddings.embed("el partido de Boca Juniors del domingo termino empatado")

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb)

    sim_related = cosine(base, related)
    sim_unrelated = cosine(base, unrelated)
    assert sim_related > sim_unrelated
