"""Comando `mem` — captura, busqueda y listado de memorias desde terminal.

Uso:
  mem "texto libre"                   # guardar (default)
  mem --tipo patron "texto"           # forzar tipo
  mem --espacio <space_id> "texto"    # forzar espacio
  mem buscar "consulta"               # busqueda semantica
  mem listar [--espacio <space_id>]   # ultimas 20 del espacio
  mem cola                            # ver items pendientes en cola offline
  mem propuestas                      # propuestas del hook pendientes de revision
  mem propuestas aprobar <id|idx>     # aprobar propuesta y guardar
  mem propuestas descartar <id|idx>   # descartar propuesta
  mem propuestas limpiar              # marcar como expired las vencidas
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from datetime import UTC, datetime

from felisa.core import db, embeddings, pipeline, proposals
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.queue import QueueItem, enqueue, list_pending
from felisa.core.structuring import VALID_TIPOS, StructuringError


def _print_memory(idx: int, m, distance: float | None = None) -> None:
    header = f"[{idx}] {m.tipo or '?':<18} {m.space_id or '?':<10}"
    if m.proyecto:
        header += f" proyecto={m.proyecto}"
    if distance is not None:
        header += f" sim={1.0 - distance:.3f}"
    print(header)
    body = m.contenido_estructurado or m.contenido
    indented = textwrap.indent(textwrap.fill(body, width=88), "    ")
    print(indented)
    if m.tags:
        print(f"    tags: {', '.join(m.tags)}")
    print()


def cmd_save(args: argparse.Namespace) -> int:
    texto = " ".join(args.texto).strip()
    if not texto:
        print("error: nada para guardar", file=sys.stderr)
        return 2

    try:
        memory_id, structured = pipeline.process(
            texto,
            tipo_override=args.tipo,
            espacio_override=args.espacio,
        )
    except (EmbeddingUnavailable, StructuringError) as exc:
        item = QueueItem(
            texto=texto,
            tipo_override=args.tipo,
            espacio_override=args.espacio,
            last_error=str(exc),
        )
        enqueue(item)
        print(f"encolado offline ({item.id[:8]}): {exc}", file=sys.stderr)
        return 1

    print(f"guardado {memory_id} [{structured.tipo}] {structured.space_id}", end="")
    if structured.proyecto:
        print(f" proyecto={structured.proyecto}", end="")
    print()
    if structured.tags:
        print(f"tags: {', '.join(structured.tags)}")
    return 0


def cmd_buscar(args: argparse.Namespace) -> int:
    consulta = " ".join(args.consulta).strip()
    if not consulta:
        print("error: consulta vacia", file=sys.stderr)
        return 2

    try:
        emb = embeddings.embed(consulta)
    except EmbeddingUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    hits = db.search_memories(emb, space=args.espacio, tipo=args.tipo, limit=args.limit)
    if not hits:
        print("(sin resultados)")
        return 0
    for idx, hit in enumerate(hits, 1):
        _print_memory(idx, hit.memory, distance=hit.distance)
    return 0


def cmd_listar(args: argparse.Namespace) -> int:
    memories = db.list_memories(space=args.espacio, tipo=args.tipo, limit=args.limit)
    if not memories:
        print("(sin memorias)")
        return 0
    for idx, m in enumerate(memories, 1):
        _print_memory(idx, m)
    return 0


def cmd_cola(_: argparse.Namespace) -> int:
    items = list_pending()
    if not items:
        print("cola vacia")
        return 0
    print(f"{len(items)} pendiente(s):")
    for item in items:
        print(
            f"  {item.id[:8]} attempts={item.attempts} "
            f"captured={item.captured_at[:19]} "
            f"texto={item.texto[:60]!r}"
        )
        if item.last_error:
            print(f"    error: {item.last_error}")
    return 0


def _resolve_proposal(ref: str):
    """Resuelve una referencia (indice 1-based o uuid prefix) a una Proposal pendiente."""
    pending = proposals.list_pending()
    if not pending:
        return None
    if ref.isdigit():
        idx = int(ref) - 1
        if 0 <= idx < len(pending):
            return pending[idx]
        return None
    for p in pending:
        if p.id == ref or p.id.startswith(ref):
            return p
    return None


def _format_remaining(expires_at: str) -> str:
    delta = datetime.fromisoformat(expires_at) - datetime.now(UTC)
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "vencida"
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def cmd_propuestas_listar(_: argparse.Namespace) -> int:
    pending = proposals.list_pending()
    if not pending:
        print("(sin propuestas pendientes)")
        return 0
    for idx, p in enumerate(pending, 1):
        header = (
            f"[{idx}] {p.id[:8]} · {p.source} · "
            f"confianza {p.confianza:.0%} · expira en {_format_remaining(p.expires_at)}"
        )
        print(header)
        print(textwrap.indent(textwrap.fill(p.texto, width=88), "    > "))
        print(textwrap.indent(textwrap.fill(p.contexto, width=88), "    "))
        print(f"    tipo sugerido: {p.tipo_sugerido}")
        print()
    return 0


def cmd_propuestas_aprobar(args: argparse.Namespace) -> int:
    proposal = _resolve_proposal(args.ref)
    if proposal is None:
        print(f"error: propuesta {args.ref!r} no encontrada", file=sys.stderr)
        return 2

    try:
        memory_id, structured = pipeline.process(
            proposal.texto, tipo_override=proposal.tipo_sugerido,
        )
    except (EmbeddingUnavailable, StructuringError) as exc:
        print(f"error: pipeline fallo, propuesta queda pendiente: {exc}", file=sys.stderr)
        return 1

    if memory_id is None:
        print("error: la propuesta no se pudo clasificar (sin-clasificar)", file=sys.stderr)
        return 1

    proposals.mark(proposal.id, "approved")
    print(f"guardado {memory_id} [{structured.tipo}] {structured.space_id}")
    return 0


def cmd_propuestas_descartar(args: argparse.Namespace) -> int:
    proposal = _resolve_proposal(args.ref)
    if proposal is None:
        print(f"error: propuesta {args.ref!r} no encontrada", file=sys.stderr)
        return 2
    proposals.mark(proposal.id, "rejected")
    print(f"descartada {proposal.id[:8]}")
    return 0


def cmd_propuestas_limpiar(_: argparse.Namespace) -> int:
    affected = proposals.expire_old()
    print(f"expiradas: {affected}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mem",
        description="Felisa — captura de memorias desde terminal",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_save = sub.add_parser("guardar", help="guardar memoria (default si no se da subcomando)")
    p_save.add_argument("texto", nargs="+")
    p_save.add_argument("--tipo", choices=sorted(VALID_TIPOS))
    p_save.add_argument("--espacio")
    p_save.set_defaults(func=cmd_save)

    p_search = sub.add_parser("buscar", help="busqueda semantica")
    p_search.add_argument("consulta", nargs="+")
    p_search.add_argument("--espacio")
    p_search.add_argument("--tipo", choices=sorted(VALID_TIPOS))
    p_search.add_argument("--limit", type=int, default=10)
    p_search.set_defaults(func=cmd_buscar)

    p_list = sub.add_parser("listar", help="lista ultimas memorias")
    p_list.add_argument("--espacio")
    p_list.add_argument("--tipo", choices=sorted(VALID_TIPOS))
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_listar)

    p_queue = sub.add_parser("cola", help="ver cola offline")
    p_queue.set_defaults(func=cmd_cola)

    p_prop = sub.add_parser("propuestas", help="propuestas del hook pendientes de revision")
    prop_sub = p_prop.add_subparsers(dest="prop_cmd")
    prop_sub.add_parser("listar", help="listar pendientes (default)").set_defaults(
        func=cmd_propuestas_listar,
    )
    p_aprobar = prop_sub.add_parser("aprobar", help="aprobar y guardar")
    p_aprobar.add_argument("ref", help="indice 1-based o prefijo del id")
    p_aprobar.set_defaults(func=cmd_propuestas_aprobar)
    p_descartar = prop_sub.add_parser("descartar", help="descartar propuesta")
    p_descartar.add_argument("ref", help="indice 1-based o prefijo del id")
    p_descartar.set_defaults(func=cmd_propuestas_descartar)
    prop_sub.add_parser("limpiar", help="marcar vencidas como expired").set_defaults(
        func=cmd_propuestas_limpiar,
    )
    p_prop.set_defaults(func=cmd_propuestas_listar)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = _build_parser()

    if argv and argv[0] not in {
        "guardar", "buscar", "listar", "cola", "propuestas", "-h", "--help",
    }:
        argv = ["guardar", *argv]

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
