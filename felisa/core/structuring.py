"""Estructuracion de memorias con Claude Haiku.

Recibe el texto crudo capturado por `mem "..."` y devuelve un `StructuredMemory`
con tipo clasificado, contenido limpio, espacio inferido, proyecto y tags.

El prompt vive en `felisa/core/prompts/structure.md`. Los espacios disponibles
se inyectan dinamicamente desde `db.list_spaces()`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import anthropic

from . import db
from .config import get_anthropic_key

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 800
TEMPERATURE = 0.0
DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "structure.md"
USER_PROMPT_PATH = Path.home() / ".felisa" / "prompts" / "structure.md"
NOTES_MARKER = "## Notas para la implementacion"

VALID_TIPOS = frozenset({
    "decision_tecnica",
    "patron",
    "framework",
    "modo_trabajo",
    "contexto_proyecto",
    "global",
})


class StructuringError(RuntimeError):
    """Haiku no respondio JSON valido o el JSON no cumple el esquema. Encolar offline."""


@dataclass(slots=True)
class StructuredMemory:
    contenido_estructurado: str
    tipo: str
    space_id: str
    proyecto: str | None
    tags: list[str]


def _load_prompt_template() -> str:
    """Carga el prompt de estructuracion. Si existe `~/.felisa/prompts/structure.md`
    lo prefiere sobre el default del paquete — asi cada usuario puede personalizar
    sus ejemplos sin tocar el repo."""
    if USER_PROMPT_PATH.exists():
        return USER_PROMPT_PATH.read_text(encoding="utf-8")
    return DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")


def _build_system_prompt(space_ids: list[str]) -> str:
    template = _load_prompt_template()
    if NOTES_MARKER in template:
        template = template[: template.index(NOTES_MARKER)].rstrip()
    spaces_list_md = "\n".join(f"- `{s}`" for s in space_ids)
    return template.replace("{spaces_list}", spaces_list_md)


def _strip_markdown_fences(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_and_validate(raw: str, available_spaces: set[str]) -> StructuredMemory:
    body = _strip_markdown_fences(raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StructuringError(
            f"Haiku no devolvio JSON parseable. Respuesta:\n{raw[:300]}"
        ) from exc

    if not isinstance(data, dict):
        raise StructuringError(f"Esperaba objeto JSON, vino {type(data).__name__}")

    contenido = data.get("contenido_estructurado")
    if not isinstance(contenido, str) or not contenido.strip():
        raise StructuringError(f"contenido_estructurado invalido: {contenido!r}")

    tipo = data.get("tipo")
    if tipo not in VALID_TIPOS:
        raise StructuringError(
            f"tipo invalido: {tipo!r}. Validos: {sorted(VALID_TIPOS)}"
        )

    space_id = data.get("space_id")
    if space_id not in available_spaces:
        raise StructuringError(
            f"space_id invalido: {space_id!r}. Disponibles: {sorted(available_spaces)}"
        )

    proyecto = data.get("proyecto")
    if proyecto is not None and not isinstance(proyecto, str):
        raise StructuringError(f"proyecto invalido: {proyecto!r}")
    if proyecto == "":
        proyecto = None

    tags_raw = data.get("tags") or []
    if not isinstance(tags_raw, list) or not all(isinstance(t, str) for t in tags_raw):
        raise StructuringError(f"tags invalidos: {tags_raw!r}")

    return StructuredMemory(
        contenido_estructurado=contenido.strip(),
        tipo=tipo,
        space_id=space_id,
        proyecto=proyecto,
        tags=tags_raw,
    )


def structure(texto: str, *, space_ids: list[str] | None = None) -> StructuredMemory:
    """Llama a Haiku y devuelve la memoria estructurada.

    Si `space_ids` es None, lee la lista de espacios activos desde la DB.
    """
    if not texto.strip():
        raise ValueError("structure() recibio texto vacio")

    if space_ids is None:
        space_ids = [s.id for s in db.list_spaces()]
    if not space_ids:
        raise StructuringError("No hay espacios activos en la DB")

    system = _build_system_prompt(space_ids)
    client = anthropic.Anthropic(api_key=get_anthropic_key())
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": texto}],
    )

    parts = [block.text for block in response.content if block.type == "text"]
    if not parts:
        raise StructuringError("Haiku no devolvio bloques de texto")
    return _parse_and_validate("".join(parts), set(space_ids))
