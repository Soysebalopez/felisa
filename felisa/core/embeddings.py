"""Embeddings via Cloudflare Workers AI (`@cf/baai/bge-small-en-v1.5`, 384 dim).

El mismo cliente se usa en la pipeline local de `mem` y en el MCP server desplegado
en Railway. Asi los vectores de captura y los de query viven en el mismo espacio
y la busqueda funciona.

Si la API de Cloudflare no responde (timeout, 5xx, sin internet), `embed()` levanta
`EmbeddingUnavailable`. La cola offline atrapa el caso y reintenta.
"""

from __future__ import annotations

import httpx

from .config import get_cloudflare_account_id, get_cloudflare_token

MODEL = "@cf/baai/bge-small-en-v1.5"
EMBEDDING_DIM = 384
DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=15.0, write=3.0, pool=3.0)


class EmbeddingUnavailable(RuntimeError):
    """Cloudflare no responde, fallo HTTP, o respuesta sin shape esperada."""


def _endpoint() -> str:
    return (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{get_cloudflare_account_id()}/ai/run/{MODEL}"
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_cloudflare_token()}",
        "Content-Type": "application/json",
    }


def embed(texto: str, *, timeout: httpx.Timeout = DEFAULT_TIMEOUT) -> list[float]:
    """Devuelve el embedding como list[float] de longitud 384."""
    if not texto.strip():
        raise ValueError("embed() recibio texto vacio")

    try:
        response = httpx.post(
            _endpoint(),
            headers=_headers(),
            json={"text": [texto]},
            timeout=timeout,
        )
    except httpx.ConnectError as exc:
        raise EmbeddingUnavailable(
            "No se pudo conectar a Cloudflare Workers AI. ¿Hay internet?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise EmbeddingUnavailable(
            f"Timeout llamando Cloudflare Workers AI ({texto[:40]!r}...)"
        ) from exc

    if response.status_code == 401:
        raise EmbeddingUnavailable(
            "Cloudflare 401: token invalido o sin permiso Workers AI"
        )
    if response.status_code == 404:
        raise EmbeddingUnavailable(
            f"Cloudflare 404: account_id o modelo invalido ({MODEL})"
        )
    if response.status_code >= 500:
        raise EmbeddingUnavailable(
            f"Cloudflare {response.status_code}: error temporal del servicio"
        )
    response.raise_for_status()

    payload = response.json()
    if not payload.get("success"):
        errors = payload.get("errors") or [{"message": "unknown"}]
        raise EmbeddingUnavailable(
            f"Cloudflare devolvio success=false: {errors[0].get('message')}"
        )

    result = payload.get("result") or {}
    data = result.get("data") or []
    if not data or not isinstance(data[0], list):
        raise EmbeddingUnavailable(
            f"Respuesta Cloudflare sin embedding en result.data: {payload}"
        )
    vector = data[0]
    if len(vector) != EMBEDDING_DIM:
        raise EmbeddingUnavailable(
            f"Embedding inesperado: dim={len(vector)}, esperaba {EMBEDDING_DIM}"
        )
    return vector


def check_available() -> None:
    """Smoke test contra Cloudflare. Levanta EmbeddingUnavailable si algo falla."""
    embed("ping de salud felisa")
