"""Transcripcion de audio via Groq Whisper.

El bot llama `transcribe(audio_bytes, mime)` cuando recibe un voice/audio
message. Groq corre whisper-large-v3 mucho mas rapido que cualquier opcion
local (~0.5s vs ~10s en M1). El resultado entra al pipeline normal igual que
un mensaje de texto.
"""

from __future__ import annotations

import logging

import httpx

from felisa.core.config import get_groq_key

log = logging.getLogger(__name__)

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
MAX_FILE_BYTES = 20 * 1024 * 1024
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


class WhisperError(RuntimeError):
    """Falla transcribiendo. El caller decide si avisar al usuario o reintentar."""


async def transcribe(
    audio_bytes: bytes,
    mime: str,
    *,
    language: str = "es",
    client: httpx.AsyncClient | None = None,
) -> str:
    if not audio_bytes:
        raise WhisperError("transcribe recibio bytes vacios")
    if len(audio_bytes) > MAX_FILE_BYTES:
        raise WhisperError(
            f"audio de {len(audio_bytes) / 1024 / 1024:.1f} MB excede el limite de 20 MB"
        )

    owns = client is None
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    try:
        try:
            response = await http.post(
                GROQ_ENDPOINT,
                headers={"Authorization": f"Bearer {get_groq_key()}"},
                files={"file": ("audio.ogg", audio_bytes, mime)},
                data={"model": MODEL, "language": language},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise WhisperError(f"red caida llamando Groq Whisper: {exc}") from exc

        if response.status_code == 401:
            raise WhisperError("Groq 401: GROQ_API_KEY invalida o revocada")
        if response.status_code == 429:
            raise WhisperError("Groq 429: rate limit / quota agotada")
        if response.status_code >= 500:
            raise WhisperError(f"Groq {response.status_code}: error temporal")
        response.raise_for_status()

        body = response.json()
        text = (body.get("text") or "").strip()
        if not text:
            raise WhisperError(f"Groq devolvio respuesta sin texto: {body}")
        return text
    finally:
        if owns:
            await http.aclose()
