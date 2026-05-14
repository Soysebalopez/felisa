"""Persistencia de clientes y access tokens del OAuth provider.

Sin esto el dict en memoria del proceso muere en cada redeploy de Railway y
claude.ai pierde la sesion. Operaciones sincronas sobre el pool global de
psycopg que ya usa el resto del codigo.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from mcp.server.auth.provider import AccessToken
from mcp.shared.auth import OAuthClientInformationFull

from felisa.core import db


# Schema inline para evitar acoplar el package a la ubicacion de sql/ en disco.
# Es el mismo cuerpo de sql/002_oauth_tokens.sql; mantenerlos sincronizados.
_SCHEMA = """
create table if not exists oauth_clients (
  client_id text primary key,
  info      jsonb       not null,
  created_at timestamptz default now()
);

create table if not exists oauth_tokens (
  token      text primary key,
  client_id  text not null,
  scopes     text[] not null default '{}',
  resource   text,
  expires_at timestamptz,
  created_at timestamptz default now()
);

create index if not exists oauth_tokens_expires_idx
  on oauth_tokens (expires_at);
"""


def init_oauth_tables() -> None:
    """Aplica el schema. Idempotente, seguro al startup."""
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA)


def save_client(client: OAuthClientInformationFull) -> None:
    payload = client.model_dump_json()
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into oauth_clients (client_id, info)
            values (%s, %s::jsonb)
            on conflict (client_id) do update set info = excluded.info
            """,
            (client.client_id, payload),
        )


def load_client(client_id: str) -> OAuthClientInformationFull | None:
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("select info from oauth_clients where client_id = %s", (client_id,))
        row = cur.fetchone()
    if not row:
        return None
    raw = row["info"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    return OAuthClientInformationFull.model_validate(raw)


def save_token(token: AccessToken) -> None:
    expires_at = (
        datetime.fromtimestamp(token.expires_at, tz=timezone.utc)
        if token.expires_at
        else None
    )
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into oauth_tokens (token, client_id, scopes, resource, expires_at)
            values (%s, %s, %s, %s, %s)
            on conflict (token) do update set
                client_id  = excluded.client_id,
                scopes     = excluded.scopes,
                resource   = excluded.resource,
                expires_at = excluded.expires_at
            """,
            (
                token.token,
                token.client_id,
                list(token.scopes or []),
                token.resource,
                expires_at,
            ),
        )


def load_token(raw: str) -> AccessToken | None:
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select token, client_id, scopes, resource, expires_at
              from oauth_tokens where token = %s
            """,
            (raw,),
        )
        row = cur.fetchone()
    if not row:
        return None
    expires_ts: int | None = None
    if row["expires_at"]:
        expires_ts = int(row["expires_at"].timestamp())
    return AccessToken(
        token=row["token"],
        client_id=row["client_id"],
        scopes=list(row["scopes"] or []),
        expires_at=expires_ts,
        resource=row["resource"],
    )


def delete_token(raw: str) -> None:
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("delete from oauth_tokens where token = %s", (raw,))


def purge_expired_tokens() -> int:
    """Borra tokens vencidos. Devuelve cuantos elimino."""
    with db._get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("delete from oauth_tokens where expires_at < now()")
        return cur.rowcount or 0
