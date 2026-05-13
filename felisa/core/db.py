"""Operaciones contra Railway Postgres + pgvector.

Usa psycopg v3 con `ConnectionPool`. La primera llamada a una operacion
abre el pool; queda warm hasta que cierres el proceso.

Convencion: las funciones puras reciben un cliente o usan el pool global
internamente. El pool global es seguro para uso desde threads.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID

from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_database_url
from .models import Memory, SearchHit, Space

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _configure_connection(conn) -> None:
    register_vector(conn)


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=get_database_url(),
                min_size=1,
                max_size=4,
                open=False,
                configure=_configure_connection,
                kwargs={"row_factory": dict_row, "autocommit": True},
            )
            _pool.open()
    return _pool


def close_pool() -> None:
    """Cerrar el pool. Util para tests y shutdown limpio."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def insert_memory(
    contenido: str,
    *,
    contenido_estructurado: str | None = None,
    tipo: str | None = None,
    space_id: str | None = None,
    proyecto: str | None = None,
    tags: list[str] | None = None,
    proyectos_relacionados: list[str] | None = None,
    embedding: list[float] | None = None,
) -> UUID:
    """Inserta una memoria y devuelve su UUID generado."""
    with _get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into memories (
                contenido, contenido_estructurado, tipo, space_id, proyecto,
                tags, proyectos_relacionados, embedding
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                contenido,
                contenido_estructurado,
                tipo,
                space_id,
                proyecto,
                tags,
                proyectos_relacionados,
                embedding,
            ),
        )
        return cur.fetchone()["id"]


def search_memories(
    query_embedding: list[float],
    *,
    space: str | None = None,
    tipo: str | None = None,
    limit: int = 10,
) -> list[SearchHit]:
    """Busqueda semantica por cosine distance.

    El operador `<=>` usa el indice ivfflat con `vector_cosine_ops`.
    Devuelve hits ordenados por similitud descendente.
    """
    filters: list[str] = []
    filter_params: list[Any] = []
    if space is not None:
        filters.append("space_id = %s")
        filter_params.append(space)
    if tipo is not None:
        filters.append("tipo = %s")
        filter_params.append(tipo)
    where = ("where " + " and ".join(filters)) if filters else ""

    with _get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select
                id, contenido, contenido_estructurado, tipo, space_id, proyecto,
                tags, proyectos_relacionados, created_at,
                embedding <=> %s::vector as distance
            from memories
            {where}
            order by embedding <=> %s::vector
            limit %s
            """,
            [query_embedding, *filter_params, query_embedding, limit],
        )
        rows = cur.fetchall()
        return [
            SearchHit(memory=Memory.from_row(row), distance=float(row["distance"]))
            for row in rows
        ]


def list_memories(
    *,
    space: str | None = None,
    tipo: str | None = None,
    limit: int = 20,
) -> list[Memory]:
    """Lista las ultimas memorias ordenadas por created_at desc."""
    filters: list[str] = []
    params: list[Any] = []
    if space is not None:
        filters.append("space_id = %s")
        params.append(space)
    if tipo is not None:
        filters.append("tipo = %s")
        params.append(tipo)
    where = ("where " + " and ".join(filters)) if filters else ""
    params.append(limit)

    with _get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select
                id, contenido, contenido_estructurado, tipo, space_id, proyecto,
                tags, proyectos_relacionados, created_at
            from memories
            {where}
            order by created_at desc
            limit %s
            """,
            params,
        )
        return [Memory.from_row(r) for r in cur.fetchall()]


def list_spaces(activos_solamente: bool = True) -> list[Space]:
    """Lista los espacios. Por default solo los activos."""
    where = "where activo = true" if activos_solamente else ""
    with _get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select id, nombre, descripcion, activo, es_global
            from spaces
            {where}
            order by es_global desc, id
            """
        )
        return [Space.from_row(r) for r in cur.fetchall()]


def delete_memory(memory_id: UUID) -> bool:
    """Elimina una memoria. Devuelve True si existia."""
    with _get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("delete from memories where id = %s", (memory_id,))
        return cur.rowcount > 0
