"""Tests de felisa.telegram.whisper.

Mockean httpx.AsyncClient pasando un cliente inyectable. No tocan Groq real.
"""

from __future__ import annotations

import httpx
import pytest

from felisa.telegram import whisper as whisper_module
from felisa.telegram.whisper import WhisperError, transcribe


def _ok_handler(text: str):
    def _h(request: httpx.Request) -> httpx.Response:
        assert "Bearer" in request.headers["Authorization"]
        # multipart body: nos alcanza con verificar que existe
        assert b"whisper-large-v3" in request.content
        return httpx.Response(200, json={"text": text})
    return _h


def _status_handler(status: int):
    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "x"})
    return _h


@pytest.fixture(autouse=True)
def _fake_groq_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whisper_module, "get_groq_key", lambda: "test-key")


async def test_transcribe_happy_path() -> None:
    transport = httpx.MockTransport(_ok_handler("hola mundo desde el audio"))
    async with httpx.AsyncClient(transport=transport) as client:
        text = await transcribe(b"some audio bytes", "audio/ogg", client=client)
    assert text == "hola mundo desde el audio"


async def test_transcribe_strips_whitespace() -> None:
    transport = httpx.MockTransport(_ok_handler("  con espacios  "))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await transcribe(b"x", "audio/ogg", client=client) == "con espacios"


async def test_empty_bytes_rejected() -> None:
    with pytest.raises(WhisperError):
        await transcribe(b"", "audio/ogg")


async def test_audio_too_large_rejected() -> None:
    huge = b"x" * (21 * 1024 * 1024)
    with pytest.raises(WhisperError, match="20 MB"):
        await transcribe(huge, "audio/ogg")


async def test_empty_response_raises() -> None:
    transport = httpx.MockTransport(_ok_handler(""))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WhisperError, match="sin texto"):
            await transcribe(b"x", "audio/ogg", client=client)


async def test_401_raises() -> None:
    transport = httpx.MockTransport(_status_handler(401))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WhisperError, match="401"):
            await transcribe(b"x", "audio/ogg", client=client)


async def test_429_raises() -> None:
    transport = httpx.MockTransport(_status_handler(429))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WhisperError, match="429"):
            await transcribe(b"x", "audio/ogg", client=client)


async def test_5xx_raises() -> None:
    transport = httpx.MockTransport(_status_handler(503))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WhisperError, match="503"):
            await transcribe(b"x", "audio/ogg", client=client)


async def test_network_error_raises() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no DNS")
    transport = httpx.MockTransport(_boom)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WhisperError, match="red caida"):
            await transcribe(b"x", "audio/ogg", client=client)
