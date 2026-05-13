"""MCP server de Felisa — expone tools read-only sobre la memoria.

Usado por claude.ai para consultar contexto al inicio de cada conversacion.
Transporte: streamable-http (stateless + json_response para Railway).

Auth: bearer token `FELISA_API_TOKEN` verificado en middleware Starlette.

Run local:
    uv run felisa-mcp-server
    # luego curl con Bearer al endpoint /mcp/

Run en Railway:
    nixpacks autodetecta. startCommand: uv run felisa-mcp-server
    Env vars necesarias:
        DATABASE_URL          (provista por servicio Postgres)
        FELISA_API_TOKEN
        CLOUDFLARE_ACCOUNT_ID
        CLOUDFLARE_API_TOKEN
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

from felisa.core import db, embeddings

log = logging.getLogger("felisa.mcp")


def _allowed_hosts() -> list[str]:
    """Hosts permitidos para DNS rebinding protection.

    Local: localhost/127.0.0.1. Produccion: dominio Railway. Override via
    `MCP_ALLOWED_HOSTS` (CSV) si se necesitan dominios extra.
    """
    base = ["localhost", "127.0.0.1", "felisa-mcp-production.up.railway.app"]
    extra = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if extra:
        base.extend(h.strip() for h in extra.split(",") if h.strip())
    return base


def _allowed_origins() -> list[str]:
    base = [
        "http://localhost",
        "http://127.0.0.1",
        "https://felisa-mcp-production.up.railway.app",
        "https://claude.ai",
        "https://*.claude.ai",
    ]
    extra = os.environ.get("MCP_ALLOWED_ORIGINS", "").strip()
    if extra:
        base.extend(o.strip() for o in extra.split(",") if o.strip())
    return base


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
    streamable_http_path="/",
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
        query: texto a buscar (en lenguaje natural, en cualquier idioma)
        space: filtrar por espacio (ej. "whitebay", "simplistic", "global")
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
    """Lista los espacios de memoria del usuario.

    Args:
        incluir_inactivos: si True incluye los archivados (activo=false)
    """
    activos_solamente = not incluir_inactivos
    spaces = db.list_spaces(activos_solamente=activos_solamente)
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


class BearerAuth(BaseHTTPMiddleware):
    """Valida Authorization: Bearer <token> contra FELISA_API_TOKEN."""

    async def dispatch(self, request: Request, call_next):
        # healthcheck publico
        if request.url.path in {"/health", "/healthz", "/"}:
            return await call_next(request)

        expected = os.environ.get("FELISA_API_TOKEN", "").strip()
        if not expected:
            return JSONResponse(
                {"error": "server misconfigured: FELISA_API_TOKEN no seteado"},
                status_code=500,
            )

        auth_header = request.headers.get("authorization", "").strip()
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                {"error": "missing bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:].strip()
        if token != expected:
            log.warning("intento de auth con token invalido")
            return JSONResponse({"error": "invalid token"}, status_code=401)

        return await call_next(request)


async def healthcheck(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "felisa-mcp"})


@contextlib.asynccontextmanager
async def _lifespan(app: Starlette):
    """Arranca el session manager interno de FastMCP."""
    async with mcp.session_manager.run():
        yield


def build_app() -> Starlette:
    """Arma la app Starlette con auth + MCP + lifespan."""
    from starlette.routing import Route

    routes = [
        Route("/health", healthcheck, methods=["GET"]),
        Mount("/mcp", app=mcp.streamable_http_app()),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(BearerAuth)],
        lifespan=_lifespan,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="felisa-mcp-server")
    parser.add_argument(
        "--host", default=os.environ.get("HOST", "0.0.0.0"),
        help="host bind (default 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", "8080")),
        help="puerto (default 8080, sobreescribible por env PORT)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    if not os.environ.get("FELISA_API_TOKEN"):
        # Auto-load del Keychain en dev local
        try:
            from felisa.core.config import _read_keychain
            os.environ["FELISA_API_TOKEN"] = _read_keychain("felisa-mcp-token")
            log.info("FELISA_API_TOKEN cargado del Keychain (modo dev)")
        except Exception as exc:
            log.warning(
                "FELISA_API_TOKEN no esta en env ni en Keychain: %s", exc
            )

    app = build_app()
    log.info("felisa-mcp-server escuchando en %s:%d", args.host, args.port)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
