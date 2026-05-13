"""Tests del agente conversacional.

Los tests de tools usan la DB real (con fixture de cleanup). Los tests
del loop conversacional mockean `anthropic.Anthropic` para evitar costos.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from felisa.core import agent, db


# ---- tests de execute_tool ------------------------------------------------


@pytest.fixture
def test_space() -> Iterator[str]:
    sid = f"pytest_{uuid.uuid4().hex[:8]}"
    yield sid
    try:
        db.delete_space(sid, force=True)
    except Exception:
        pass


def test_tool_list_spaces_returns_active_only() -> None:
    raw = agent.execute_tool("list_spaces", {})
    data = json.loads(raw)
    ids = {s["id"] for s in data}
    assert {"global", "whitebay", "simplistic"} <= ids


def test_tool_create_space(test_space: str) -> None:
    raw = agent.execute_tool("create_space", {"id": test_space, "nombre": "Test"})
    data = json.loads(raw)
    assert data["created"] == test_space


def test_tool_create_space_invalid_id_returns_error() -> None:
    raw = agent.execute_tool("create_space", {"id": "Mal ID", "nombre": "x"})
    data = json.loads(raw)
    assert data["error"] == "invalid_id"


def test_tool_archive_protected_returns_error() -> None:
    raw = agent.execute_tool("archive_space", {"id": "global"})
    data = json.loads(raw)
    assert data["error"] == "protected"


def test_tool_delete_space_not_empty(test_space: str) -> None:
    db.create_space(test_space, "Test")
    db.insert_memory(
        contenido="lo bloquea",
        space_id=test_space,
        tipo="global",
        embedding=[0.0] * 384,
    )
    raw = agent.execute_tool("delete_space", {"id": test_space})
    data = json.loads(raw)
    assert data["error"] == "space_not_empty"


def test_tool_delete_protected_returns_error() -> None:
    raw = agent.execute_tool("delete_space", {"id": "global", "force": True})
    data = json.loads(raw)
    assert data["error"] == "protected"


def test_tool_count_memories_total() -> None:
    raw = agent.execute_tool("count_memories", {})
    data = json.loads(raw)
    assert "total" in data
    assert isinstance(data["total"], int)


def test_tool_count_memories_by_space() -> None:
    raw = agent.execute_tool("count_memories", {"space": "global"})
    data = json.loads(raw)
    assert data["space"] == "global"
    assert isinstance(data["count"], int)


def test_tool_unknown_returns_error() -> None:
    raw = agent.execute_tool("no_existe", {})
    data = json.loads(raw)
    assert data["error"] == "unknown_tool"


# ---- tests del loop conversacional con anthropic mockeado -----------------


@dataclass
class _Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict | None = None

    def model_dump(self) -> dict:
        d: dict[str, Any] = {"type": self.type}
        if self.type == "text":
            d["text"] = self.text
        else:
            d["id"] = self.id
            d["name"] = self.name
            d["input"] = self.input or {}
        return d


@dataclass
class _Response:
    stop_reason: str
    content: list[_Block]


def _fake_client(responses: list[_Response]) -> MagicMock:
    """Devuelve un mock de anthropic.Anthropic que itera por las respuestas dadas."""
    client = MagicMock()
    it = iter(responses)
    client.messages.create.side_effect = lambda **kw: next(it)
    return client


def test_chat_direct_response_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response(
        stop_reason="end_turn",
        content=[_Block(type="text", text="hola Seba")],
    )
    a = agent.Agent()
    a._client = _fake_client([response])
    result = a.chat("hola")
    assert result == "hola Seba"
    assert len(a.history) == 2


def test_chat_with_one_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_response = _Response(
        stop_reason="tool_use",
        content=[_Block(type="tool_use", id="t1", name="list_spaces", input={})],
    )
    final_response = _Response(
        stop_reason="end_turn",
        content=[_Block(type="text", text="tenes 3 espacios")],
    )
    a = agent.Agent()
    a._client = _fake_client([tool_response, final_response])
    result = a.chat("listame los espacios")
    assert result == "tenes 3 espacios"
    # historial: user, assistant(tool_use), user(tool_result), assistant(final)
    assert len(a.history) == 4
    assert a.history[2]["role"] == "user"
    assert a.history[2]["content"][0]["type"] == "tool_result"


def test_chat_circuit_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "MAX_TOOL_ITERATIONS", 3)
    # Siempre devuelve tool_use, nunca termina
    looped = _Response(
        stop_reason="tool_use",
        content=[_Block(type="tool_use", id="loop", name="count_memories", input={})],
    )
    a = agent.Agent()
    a._client = _fake_client([looped, looped, looped])
    with pytest.raises(RuntimeError, match="excedio"):
        a.chat("loop forever")


def test_chat_empty_input_raises() -> None:
    a = agent.Agent()
    with pytest.raises(ValueError):
        a.chat("")


def test_reset_clears_history() -> None:
    response = _Response(stop_reason="end_turn", content=[_Block(type="text", text="ok")])
    a = agent.Agent()
    a._client = _fake_client([response])
    a.chat("hola")
    assert len(a.history) > 0
    a.reset()
    assert a.history == []


# ---- test E2E real (cuesta ~$0.005) ---------------------------------------


def test_agent_real_call_lists_spaces() -> None:
    a = agent.Agent()
    reply = a.chat("listame mis espacios activos sin ningun extra, en una linea cada uno")
    assert "global" in reply.lower()
    assert "whitebay" in reply.lower()
    assert "simplistic" in reply.lower()
