"""Comando `felisa` — agente conversacional sobre la memoria.

Uso:
  felisa                  # abre loop conversacional
  felisa "mensaje"        # modo one-shot, una vuelta y sale
  felisa --help

Comandos meta dentro del loop:
  /salir   — salir del loop
  /limpiar — resetea el historial conversacional del agente
  /ayuda   — muestra estos comandos
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from felisa.core import db
from felisa.core.agent import Agent

console = Console()


META_HELP = """\
Comandos meta:
  /salir, /exit   salir
  /limpiar        resetea historial del agente
  /ayuda, /help   muestra esta ayuda

Cualquier otra cosa se manda al agente.
"""


def _opening_line() -> str:
    spaces = db.list_spaces()
    total = db.count_memories_total()
    return f"Hola Seba. Tenes {len(spaces)} espacios activos y {total} memorias en total. ¿Que necesitas?"


def _render_reply(text: str) -> None:
    console.print(Markdown(text), style="bright_white")


def _render_error(msg: str) -> None:
    console.print(f"[red]error:[/red] {msg}")


def cmd_oneshot(args: argparse.Namespace) -> int:
    msg = " ".join(args.mensaje).strip()
    if not msg:
        _render_error("nada que decirle al agente")
        return 2
    agent = Agent()
    try:
        reply = agent.chat(msg)
    except Exception as exc:
        _render_error(f"{type(exc).__name__}: {exc}")
        return 1
    _render_reply(reply)
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    agent = Agent()
    console.print(Panel.fit(_opening_line(), style="cyan"))
    console.print("[dim](escribi /ayuda para ver comandos meta)[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold cyan]> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]chau[/dim]")
            return 0

        if not user_input:
            continue

        if user_input in {"/salir", "/exit", "/quit"}:
            console.print("[dim]chau[/dim]")
            return 0
        if user_input in {"/ayuda", "/help"}:
            console.print(META_HELP)
            continue
        if user_input == "/limpiar":
            agent.reset()
            console.print("[dim]historial limpiado[/dim]")
            continue

        try:
            with console.status("[dim]pensando...[/dim]", spinner="dots"):
                reply = agent.chat(user_input)
        except KeyboardInterrupt:
            console.print("\n[dim](cancelado)[/dim]")
            continue
        except Exception as exc:
            _render_error(f"{type(exc).__name__}: {exc}")
            continue

        _render_reply(reply)
        console.print()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="felisa",
        description="Felisa — agente conversacional sobre tu memoria persistente",
    )
    parser.add_argument(
        "mensaje", nargs="*",
        help="Si das un mensaje, modo one-shot. Sin mensaje, loop conversacional.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.mensaje:
        return cmd_oneshot(args)
    return cmd_loop(args)


if __name__ == "__main__":
    sys.exit(main())
