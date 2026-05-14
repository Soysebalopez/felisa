"""Comando `mem` — captura, busqueda y listado de memorias desde terminal.

Uso:
  mem "texto libre"                   # guardar (default)
  mem --tipo patron "texto"           # forzar tipo
  mem --espacio <space_id> "texto"    # forzar espacio
  mem buscar "consulta"               # busqueda semantica
  mem listar [--espacio <space_id>]   # ultimas 20 del espacio
  mem cola                            # ver items pendientes en cola offline
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from felisa.core import db, embeddings, pipeline
from felisa.core.embeddings import EmbeddingUnavailable
from felisa.core.queue import QueueItem, enqueue, list_pending
from felisa.core.structuring import StructuringError, VALID_TIPOS


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

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = _build_parser()

    if argv and argv[0] not in {"guardar", "buscar", "listar", "cola", "-h", "--help"}:
        argv = ["guardar", *argv]

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
