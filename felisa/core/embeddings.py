"""Embeddings via Ollama local (`nomic-embed-text`, 768 dim).

Ollama corre en `http://localhost:11434`. El daemon de Felisa asume que
Ollama esta arrancado (via `brew services start ollama`) y que el modelo
fue pulled (`ollama pull nomic-embed-text`).

Si Ollama no responde, `embed()` levanta `EmbeddingUnavailable`. El daemon
captura esa excepcion y encola la memoria offline para reintentar.
"""

from __future__ import annotations

import httpx

OLLAMA_URL = "http://localhost:11434"
MODEL = "nomic-embed-text"
EMBEDDING_DIM = 768
DEFAULT_TIMEOUT = httpx.Timeout(connect=2.0, read=10.0, write=2.0, pool=2.0)


class EmbeddingUnavailable(RuntimeError):
    """Ollama no responde o el modelo no esta disponible. Encolar offline."""


def embed(texto: str, *, timeout: httpx.Timeout = DEFAULT_TIMEOUT) -> list[float]:
    """Devuelve el embedding de `texto` como list[float] de longitud 768."""
    if not texto.strip():
        raise ValueError("embed() recibio texto vacio")
    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": MODEL, "prompt": texto},
            timeout=timeout,
        )
    except httpx.ConnectError as exc:
        raise EmbeddingUnavailable(
            f"Ollama no responde en {OLLAMA_URL}. "
            f"Arrancalo con: brew services start ollama"
        ) from exc
    except httpx.TimeoutException as exc:
        raise EmbeddingUnavailable(
            f"Ollama timeout al generar embedding ({texto[:40]}...)"
        ) from exc

    if response.status_code == 404 or "not found" in response.text.lower():
        raise EmbeddingUnavailable(
            f"Modelo '{MODEL}' no esta disponible en Ollama. "
            f"Pullealo con: ollama pull {MODEL}"
        )
    response.raise_for_status()

    data = response.json()
    embedding = data.get("embedding") or []
    if len(embedding) != EMBEDDING_DIM:
        raise EmbeddingUnavailable(
            f"Embedding inesperado: dim={len(embedding)}, esperaba {EMBEDDING_DIM}. "
            f"Modelo configurado: {MODEL}"
        )
    return embedding


def check_available() -> None:
    """Smoke test: levanta EmbeddingUnavailable con mensaje claro si algo falla."""
    embed("ping de salud felisa")
