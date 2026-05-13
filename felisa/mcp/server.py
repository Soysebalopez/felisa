"""MCP server de Felisa — expone tools read-only sobre la memoria.

Usado por claude.ai para consultar contexto al inicio de cada conversacion.
Transporte: streamable-http (stateless + json_response para Railway).

Auth: OAuth 2.1 con Dynamic Client Registration. Embedimos el Authorization
Server en el mismo proceso (`FelisaOAuthProvider`). El humano autentica
pegando su `FELISA_API_TOKEN` en el formulario de /login.

Run local:
    uv run felisa-mcp-server

Run en Railway:
    Nixpacks autodetecta. startCommand: uv run felisa-mcp-server
    Env vars necesarias:
        DATABASE_URL          (referencia al servicio Postgres)
        FELISA_API_TOKEN      (password del operador para /login)
        CLOUDFLARE_ACCOUNT_ID
        CLOUDFLARE_API_TOKEN
        MCP_PUBLIC_URL        (URL publica del servicio, p.ej. https://x.up.railway.app)
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from typing import Any

import uvicorn
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount

from felisa.core import db, embeddings
from felisa.mcp.oauth_provider import MCP_SCOPE, FelisaOAuthProvider

log = logging.getLogger("felisa.mcp")


def _public_url() -> str:
    """URL publica del MCP server (sin path). Default railway, override env."""
    return os.environ.get(
        "MCP_PUBLIC_URL", "https://felisa-mcp-production.up.railway.app"
    ).rstrip("/")


def _allowed_hosts() -> list[str]:
    base = ["localhost", "127.0.0.1"]
    from urllib.parse import urlparse
    pub = urlparse(_public_url()).hostname
    if pub:
        base.append(pub)
    extra = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if extra:
        base.extend(h.strip() for h in extra.split(",") if h.strip())
    return base


def _allowed_origins() -> list[str]:
    base = [
        "http://localhost",
        "http://127.0.0.1",
        _public_url(),
        "https://claude.ai",
    ]
    extra = os.environ.get("MCP_ALLOWED_ORIGINS", "").strip()
    if extra:
        base.extend(o.strip() for o in extra.split(",") if o.strip())
    return base


# OAuth provider singleton para que custom_route handlers compartan estado
oauth_provider = FelisaOAuthProvider(
    auth_callback_url=f"{_public_url()}/login",
    server_url=_public_url(),
)


mcp = FastMCP(
    "Felisa",
    instructions=(
        "Memoria persistente de Seba (Sebastian Lopez). Tools read-only para "
        "consultar decisiones tecnicas, patrones, modos de trabajo, contexto de "
        "proyectos. Usar `search_memories` cuando el usuario habla de un tema, "
        "`list_spaces` para conocer los espacios disponibles, "
        "`list_recent_memories` para contexto reciente."
    ),
    stateless_http=True,
    json_response=True,
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(_public_url()),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[MCP_SCOPE],
            default_scopes=[MCP_SCOPE],
        ),
        required_scopes=[MCP_SCOPE],
        resource_server_url=None,
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        allowed_origins=_allowed_origins(),
    ),
)


def _serialize_memory(m, *, similarity: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(m.id),
        "tipo": m.tipo,
        "space_id": m.space_id,
        "proyecto": m.proyecto,
        "contenido": m.contenido_estructurado or m.contenido,
        "tags": list(m.tags),
    }
    if m.created_at is not None:
        out["created_at"] = m.created_at.isoformat()
    if similarity is not None:
        out["similarity"] = round(similarity, 3)
    return out


@mcp.tool()
def search_memories(
    query: str,
    space: str | None = None,
    tipo: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Busca memorias por similitud semantica.

    Args:
        query: texto a buscar (lenguaje natural)
        space: filtrar por espacio (whitebay, simplistic, global, ...)
        tipo: filtrar por tipo (decision_tecnica, patron, framework, modo_trabajo, contexto_proyecto, global)
        limit: cantidad maxima de resultados (default 5)
    """
    try:
        emb = embeddings.embed(query)
    except embeddings.EmbeddingUnavailable as exc:
        return [{"error": "embeddings_unavailable", "message": str(exc)}]
    hits = db.search_memories(emb, space=space, tipo=tipo, limit=limit)
    return [_serialize_memory(h.memory, similarity=h.similarity) for h in hits]


@mcp.tool()
def list_recent_memories(
    space: str | None = None,
    tipo: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Lista las memorias mas recientes ordenadas por fecha de captura."""
    memories = db.list_memories(space=space, tipo=tipo, limit=limit)
    return [_serialize_memory(m) for m in memories]


@mcp.tool()
def list_spaces(incluir_inactivos: bool = False) -> list[dict[str, Any]]:
    """Lista los espacios de memoria del usuario."""
    spaces = db.list_spaces(activos_solamente=not incluir_inactivos)
    return [
        {
            "id": s.id,
            "nombre": s.nombre,
            "descripcion": s.descripcion,
            "es_global": s.es_global,
            "activo": s.activo,
        }
        for s in spaces
    ]


@mcp.tool()
def count_memories(space: str | None = None) -> dict[str, Any]:
    """Cuenta memorias totales o de un espacio especifico."""
    if space:
        return {"space": space, "count": db.count_memories_in_space(space)}
    return {"total": db.count_memories_total()}


@mcp.custom_route("/health", methods=["GET"])
async def healthcheck(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "felisa-mcp"})


@mcp.custom_route("/login", methods=["GET"])
async def login_page(request: Request) -> Response:
    state = request.query_params.get("state", "")
    return await oauth_provider.get_login_page(state)


@mcp.custom_route("/login/callback", methods=["POST"])
async def login_callback(request: Request) -> Response:
    return await oauth_provider.handle_login_callback(request)


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request) -> HTMLResponse:
    return HTMLResponse(
        "<h1>Felisa MCP</h1><p>This is an MCP server endpoint. "
        "Configure it as a custom connector in claude.ai.</p>"
    )


@contextlib.asynccontextmanager
async def _lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


def build_app() -> Starlette:
    """Wrappea la app de FastMCP con CORSMiddleware abierto a claude.ai."""
    inner = mcp.streamable_http_app()
    return Starlette(
        routes=[Mount("/", app=inner)],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origin_regex=r"https://.*\.claude\.ai|https://claude\.ai|http://localhost(:\d+)?",
                allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
                allow_headers=["*"],
                allow_credentials=True,
                expose_headers=["mcp-session-id", "mcp-protocol-version"],
            ),
        ],
        lifespan=_lifespan,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="felisa-mcp-server")
    parser.add_argument(
        "--host", default=os.environ.get("HOST", "0.0.0.0"),
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", "8080")),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    if not os.environ.get("FELISA_API_TOKEN"):
        try:
            from felisa.core.config import _read_keychain
            os.environ["FELISA_API_TOKEN"] = _read_keychain("felisa-mcp-token")
            log.info("FELISA_API_TOKEN cargado del Keychain (dev mode)")
        except Exception as exc:
            log.warning("FELISA_API_TOKEN no esta en env ni Keychain: %s", exc)

    app = build_app()
    log.info("felisa-mcp-server escuchando en %s:%d (public=%s)",
             args.host, args.port, _public_url())
    uvicorn.run(
        app, host=args.host, port=args.port, log_level="info",
        proxy_headers=True, forwarded_allow_ips="*",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
