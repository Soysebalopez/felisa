"""Cliente HTTP minimal a la Bot API de Telegram.

Una sola dependencia (httpx async). Cubre exactamente lo que usa el bot:
getUpdates con long polling, sendMessage, getFile y descarga binaria. Errores
de red levantan TelegramUnavailable (recuperable). Errores HTTP especificos
levantan TelegramAuthError (401, no reintentar) o respetan retry_after (429).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
DEFAULT_LONG_POLL_TIMEOUT = 25
HTTP_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=DEFAULT_LONG_POLL_TIMEOUT + 10,
    write=10.0,
    pool=10.0,
)


class TelegramUnavailable(RuntimeError):
    """Fallo de red, timeout o 5xx. El caller debe reintentar con backoff."""


class TelegramAuthError(RuntimeError):
    """401 de Telegram: token revocado o invalido. No reintentar."""


@dataclass(slots=True)
class RateLimited(Exception):
    retry_after: int


class TelegramAPI:
    """Cliente async a la Bot API. Una instancia = una sesion HTTP reusable."""

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "TelegramAPI":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self._token}/{method}"

    def _file_url(self, file_path: str) -> str:
        return f"{API_BASE}/file/bot{self._token}/{file_path}"

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(self._url(method), json=payload)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TelegramUnavailable(f"red caida llamando {method}: {exc}") from exc

        if response.status_code == 401:
            raise TelegramAuthError(
                f"Telegram 401 en {method}: el token esta revocado o es invalido"
            )
        if response.status_code == 429:
            retry = int(response.json().get("parameters", {}).get("retry_after", 1))
            raise RateLimited(retry_after=retry)
        if response.status_code >= 500:
            raise TelegramUnavailable(
                f"Telegram {response.status_code} en {method}: error temporal"
            )

        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise TelegramUnavailable(
                f"Telegram devolvio ok=false en {method}: {body.get('description')}"
            )
        return body["result"]

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int = DEFAULT_LONG_POLL_TIMEOUT,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        return await self._call("getUpdates", payload)

    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return await self._call("sendMessage", payload)

    async def get_file(self, file_id: str) -> dict[str, Any]:
        return await self._call("getFile", {"file_id": file_id})

    async def download_file(self, file_path: str) -> bytes:
        try:
            response = await self._client.get(self._file_url(file_path))
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TelegramUnavailable(f"red caida bajando {file_path}: {exc}") from exc
        if response.status_code >= 500:
            raise TelegramUnavailable(
                f"Telegram {response.status_code} bajando {file_path}"
            )
        response.raise_for_status()
        return response.content
