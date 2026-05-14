"""Tests de persistencia OAuth contra Railway production.

Demuestra que los access tokens y clients sobreviven a un "redeploy"
(simulado descartando la referencia y releyendo desde la DB).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterator

import pytest
from mcp.server.auth.provider import AccessToken
from mcp.shared.auth import OAuthClientInformationFull

from felisa.core import db
from felisa.core.config import MissingCredential
from felisa.mcp import oauth_storage


def _have_db() -> bool:
    try:
        db.list_spaces()
        return True
    except (MissingCredential, Exception):
        return False


pytestmark = pytest.mark.skipif(
    not _have_db(),
    reason="DATABASE_URL no disponible (esperado en CI sin Postgres real)",
)


@pytest.fixture(autouse=True)
def _ensure_schema() -> None:
    oauth_storage.init_oauth_tables()


@pytest.fixture
def created_tokens() -> Iterator[list[str]]:
    tokens: list[str] = []
    yield tokens
    for t in tokens:
        oauth_storage.delete_token(t)


@pytest.fixture
def created_clients() -> Iterator[list[str]]:
    client_ids: list[str] = []
    yield client_ids
    if not client_ids:
        return
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "delete from oauth_clients where client_id = any(%s)",
            (client_ids,),
        )


def _make_client(client_id: str) -> OAuthClientInformationFull:
    return OAuthClientInformationFull.model_validate({
        "client_id": client_id,
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "client_name": "pytest client",
        "scope": "user",
    })


def test_token_survives_redeploy(created_tokens: list[str]) -> None:
    raw = f"mcp_test_{secrets.token_hex(8)}"
    created_tokens.append(raw)
    original = AccessToken(
        token=raw,
        client_id="pytest-client",
        scopes=["user"],
        expires_at=int(time.time()) + 3600,
        resource=None,
    )
    oauth_storage.save_token(original)

    # Simula redeploy: ninguna referencia en memoria, leemos de DB.
    loaded = oauth_storage.load_token(raw)
    assert loaded is not None
    assert loaded.token == raw
    assert loaded.client_id == "pytest-client"
    assert loaded.scopes == ["user"]
    assert loaded.expires_at == original.expires_at


def test_load_missing_token_returns_none() -> None:
    assert oauth_storage.load_token("mcp_does_not_exist_xxx") is None


def test_delete_token(created_tokens: list[str]) -> None:
    raw = f"mcp_test_{secrets.token_hex(8)}"
    created_tokens.append(raw)
    oauth_storage.save_token(AccessToken(
        token=raw, client_id="c", scopes=["user"],
        expires_at=int(time.time()) + 60, resource=None,
    ))
    assert oauth_storage.load_token(raw) is not None
    oauth_storage.delete_token(raw)
    assert oauth_storage.load_token(raw) is None


def test_client_round_trip(created_clients: list[str]) -> None:
    cid = f"pytest-client-{secrets.token_hex(4)}"
    created_clients.append(cid)
    client = _make_client(cid)
    oauth_storage.save_client(client)
    loaded = oauth_storage.load_client(cid)
    assert loaded is not None
    assert loaded.client_id == cid
    assert "https://claude.ai/api/mcp/auth_callback" in [str(u) for u in loaded.redirect_uris]


def test_purge_expired_tokens(created_tokens: list[str]) -> None:
    expired = f"mcp_test_{secrets.token_hex(8)}"
    fresh = f"mcp_test_{secrets.token_hex(8)}"
    created_tokens.extend([expired, fresh])
    oauth_storage.save_token(AccessToken(
        token=expired, client_id="c", scopes=["user"],
        expires_at=int(time.time()) - 60, resource=None,
    ))
    oauth_storage.save_token(AccessToken(
        token=fresh, client_id="c", scopes=["user"],
        expires_at=int(time.time()) + 3600, resource=None,
    ))
    oauth_storage.purge_expired_tokens()
    assert oauth_storage.load_token(expired) is None
    assert oauth_storage.load_token(fresh) is not None
