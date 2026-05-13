"""Agente conversacional Felisa con tool use sobre Claude Sonnet.

Mantiene historial de la sesion. Loop interno: si Claude devuelve `tool_use`
ejecuta las tools, agrega los `tool_result` al historial y vuelve a llamar
hasta que `stop_reason == "end_turn"`.

El system prompt se carga de `prompts/agent.md` en cada `chat()` con
sustitucion de variables. Asi cambios mid-sesion se reflejan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from . import db, embeddings
from .config import get_anthropic_key

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
MAX_TOOL_ITERATIONS = 20
PROMPT_PATH = Path(__file__).parent / "prompts" / "agent.md"


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_spaces",
        "description": "Lista los espacios activos del usuario con sus metadatos. Usar al inicio o cuando el usuario pregunta '¿que espacios tengo?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "incluir_inactivos": {
                    "type": "boolean",
                    "description": "Si true, incluye espacios archivados (activo=false)",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "create_space",
        "description": "Crea un espacio nuevo. El id debe estar en snake_case (minusculas, numeros, guion bajo, max 31 chars).",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "id snake_case, ej. 'futbol'"},
                "nombre": {"type": "string", "description": "Nombre legible, ej. 'Futbol'"},
                "descripcion": {"type": "string"},
            },
            "required": ["id", "nombre"],
        },
    },
    {
        "name": "archive_space",
        "description": "Marca un espacio como inactivo (activo=false). Las memorias siguen accesibles via mem buscar pero el espacio no aparece para captura. Reversible con unarchive_space.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "unarchive_space",
        "description": "Reactiva un espacio archivado.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "delete_space",
        "description": "BORRA un espacio definitivamente. Si force=true tambien borra todas las memorias asociadas. Si force=false y hay memorias, devuelve error space_not_empty. NO se puede borrar 'global'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["id"],
        },
    },
    {
        "name": "count_memories",
        "description": "Cuenta memorias totales o de un espacio especifico.",
        "input_schema": {
            "type": "object",
            "properties": {
                "space": {"type": "string", "description": "Si se omite cuenta total"},
            },
        },
    },
    {
        "name": "search_memories",
        "description": "Busqueda semantica sobre las memorias. Devuelve top hits con similitud.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "space": {"type": "string"},
                "tipo": {
                    "type": "string",
                    "enum": ["decision_tecnica", "patron", "framework", "modo_trabajo", "contexto_proyecto", "global"],
                },
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_recent_memories",
        "description": "Lista las memorias mas recientes ordenadas por fecha de creacion.",
        "input_schema": {
            "type": "object",
            "properties": {
                "space": {"type": "string"},
                "tipo": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
]


def _serialize_memory(m, *, similarity: float | None = None) -> dict:
    out = {
        "id": str(m.id),
        "tipo": m.tipo,
        "space_id": m.space_id,
        "proyecto": m.proyecto,
        "contenido": m.contenido_estructurado or m.contenido,
        "tags": m.tags,
    }
    if m.created_at is not None:
        out["created_at"] = m.created_at.isoformat()
    if similarity is not None:
        out["similarity"] = round(similarity, 3)
    return out


def execute_tool(name: str, args: dict) -> str:
    """Ejecuta una tool y devuelve un string JSON serializable."""
    if name == "list_spaces":
        activos_solamente = not args.get("incluir_inactivos", False)
        spaces = db.list_spaces(activos_solamente=activos_solamente)
        return json.dumps([
            {
                "id": s.id, "nombre": s.nombre, "es_global": s.es_global,
                "descripcion": s.descripcion, "activo": s.activo,
            } for s in spaces
        ])

    if name == "create_space":
        try:
            space = db.create_space(
                id=args["id"], nombre=args["nombre"],
                descripcion=args.get("descripcion"),
            )
        except ValueError as e:
            return json.dumps({"error": "invalid_id", "message": str(e)})
        except Exception as e:
            return json.dumps({"error": type(e).__name__, "message": str(e)})
        return json.dumps({"created": space.id, "nombre": space.nombre})

    if name == "archive_space":
        try:
            ok = db.archive_space(args["id"])
        except db.SpaceProtected as e:
            return json.dumps({"error": "protected", "message": str(e)})
        return json.dumps({"archived": ok})

    if name == "unarchive_space":
        ok = db.unarchive_space(args["id"])
        return json.dumps({"unarchived": ok})

    if name == "delete_space":
        force = bool(args.get("force", False))
        try:
            n = db.delete_space(args["id"], force=force)
        except db.SpaceNotEmpty as e:
            return json.dumps({"error": "space_not_empty", "message": str(e)})
        except db.SpaceProtected as e:
            return json.dumps({"error": "protected", "message": str(e)})
        return json.dumps({"deleted": True, "memories_deleted": n})

    if name == "count_memories":
        space = args.get("space")
        if space:
            return json.dumps({"space": space, "count": db.count_memories_in_space(space)})
        return json.dumps({"total": db.count_memories_total()})

    if name == "search_memories":
        try:
            emb = embeddings.embed(args["query"])
        except embeddings.EmbeddingUnavailable as e:
            return json.dumps({"error": "ollama_down", "message": str(e)})
        hits = db.search_memories(
            emb,
            space=args.get("space"),
            tipo=args.get("tipo"),
            limit=int(args.get("limit", 5)),
        )
        return json.dumps([_serialize_memory(h.memory, similarity=h.similarity) for h in hits])

    if name == "list_recent_memories":
        memories = db.list_memories(
            space=args.get("space"),
            tipo=args.get("tipo"),
            limit=int(args.get("limit", 10)),
        )
        return json.dumps([_serialize_memory(m) for m in memories])

    return json.dumps({"error": "unknown_tool", "name": name})


def _build_system_prompt(user_name: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    marker = "## Prompt"
    if marker in template:
        template = template[template.index(marker) + len(marker):].lstrip()

    spaces = db.list_spaces()
    if spaces:
        lines = []
        for s in spaces:
            n = db.count_memories_in_space(s.id)
            global_tag = " (global)" if s.es_global else ""
            desc = f" — {s.descripcion}" if s.descripcion else ""
            lines.append(f"- `{s.id}` ({s.nombre}{global_tag}): {n} memorias{desc}")
        spaces_summary = "\n".join(lines)
    else:
        spaces_summary = "(ninguno)"

    return template.replace("{spaces_summary}", spaces_summary).replace("{user_name}", user_name)


@dataclass
class Agent:
    user_name: str = "Seba"
    history: list[dict] = field(default_factory=list)
    _client: anthropic.Anthropic | None = field(default=None, init=False, repr=False)

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=get_anthropic_key())
        return self._client

    def reset(self) -> None:
        self.history = []

    def chat(self, user_input: str) -> str:
        """Envia mensaje del usuario, ejecuta tools si Claude lo pide, devuelve respuesta final."""
        if not user_input.strip():
            raise ValueError("chat() recibio mensaje vacio")

        self.history.append({"role": "user", "content": user_input})
        system_prompt = _build_system_prompt(self.user_name)

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                tools=TOOLS,
                messages=self.history,
            )

            self.history.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })

            if response.stop_reason != "tool_use":
                return "".join(
                    b.text for b in response.content if b.type == "text"
                ).strip()

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        result_str = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        })
                    except Exception as exc:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"error": type(exc).__name__, "message": str(exc)}),
                            "is_error": True,
                        })

            self.history.append({"role": "user", "content": tool_results})

        raise RuntimeError(
            f"Agente excedio {MAX_TOOL_ITERATIONS} iteraciones de tool use sin terminar"
        )
