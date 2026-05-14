"""Deteccion de candidatos a memoria desde un transcript de Claude Code.

Llama a Haiku con `prompts/hook_detect.md` sobre el texto del transcript y
devuelve una lista de `Candidate`. Sesgo conservador: filtra por confianza
minima antes de devolver, prefiere falsos negativos sobre falsos positivos.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import anthropic

from .config import get_anthropic_key
from .structuring import VALID_TIPOS, _strip_markdown_fences

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1500
TEMPERATURE = 0.0
MIN_CONFIDENCE = 0.6

DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "hook_detect.md"
USER_PROMPT_PATH = Path.home() / ".felisa" / "prompts" / "hook_detect.md"


class DetectionError(RuntimeError):
    """Haiku no respondio JSON valido o el JSON no cumple el esquema."""


@dataclass(slots=True)
class Candidate:
    texto: str
    contexto: str
    tipo_sugerido: str
    confianza: float


def _load_prompt() -> str:
    if USER_PROMPT_PATH.exists():
        return USER_PROMPT_PATH.read_text(encoding="utf-8")
    return DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")


def _parse(raw: str) -> list[Candidate]:
    body = _strip_markdown_fences(raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DetectionError(
            f"Haiku no devolvio JSON parseable. Respuesta:\n{raw[:300]}"
        ) from exc

    if not isinstance(data, dict):
        raise DetectionError(f"Esperaba objeto JSON, vino {type(data).__name__}")

    candidatos_raw = data.get("candidatos")
    if not isinstance(candidatos_raw, list):
        raise DetectionError(f"campo 'candidatos' debe ser lista, vino {type(candidatos_raw).__name__}")

    result: list[Candidate] = []
    for idx, item in enumerate(candidatos_raw):
        if not isinstance(item, dict):
            raise DetectionError(f"candidato[{idx}] no es dict")
        texto = item.get("texto")
        contexto = item.get("contexto")
        tipo = item.get("tipo_sugerido")
        confianza = item.get("confianza")
        if not isinstance(texto, str) or not texto.strip():
            raise DetectionError(f"candidato[{idx}].texto invalido")
        if not isinstance(contexto, str) or not contexto.strip():
            raise DetectionError(f"candidato[{idx}].contexto invalido")
        if tipo not in VALID_TIPOS:
            raise DetectionError(
                f"candidato[{idx}].tipo_sugerido invalido: {tipo!r}. "
                f"Validos: {sorted(VALID_TIPOS)}"
            )
        if not isinstance(confianza, (int, float)) or not 0.0 <= float(confianza) <= 1.0:
            raise DetectionError(f"candidato[{idx}].confianza invalida: {confianza!r}")
        result.append(
            Candidate(
                texto=texto.strip(),
                contexto=contexto.strip(),
                tipo_sugerido=tipo,
                confianza=float(confianza),
            )
        )
    return result


def detect(transcript_text: str, *, min_confidence: float = MIN_CONFIDENCE) -> list[Candidate]:
    """Llama a Haiku y devuelve candidatos con confianza >= min_confidence.

    Si el transcript es muy corto (<50 chars utiles), devuelve lista vacia sin
    quemar tokens en Haiku.
    """
    if len(transcript_text.strip()) < 50:
        return []

    system = _load_prompt()
    client = anthropic.Anthropic(api_key=get_anthropic_key())
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": transcript_text}],
    )

    parts = [block.text for block in response.content if block.type == "text"]
    if not parts:
        raise DetectionError("Haiku no devolvio bloques de texto")
    candidates = _parse("".join(parts))
    return [c for c in candidates if c.confianza >= min_confidence]
