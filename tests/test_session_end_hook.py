"""Tests del hook de Claude Code (felisa.hooks.session_end).

Mockean `detect` para no llamar a Haiku real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from felisa.core import proposals
from felisa.core.hook_detection import Candidate
from felisa.hooks import session_end


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposals, "PROPOSALS_DIR", tmp_path)
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(proposals, "LOCK_PATH", tmp_path / "proposals.lock")
    monkeypatch.setattr(session_end, "LOG_PATH", tmp_path / "hook.log")


def _write_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for role, text in turns:
            f.write(json.dumps({"type": role, "content": text}) + "\n")


def test_no_candidates_no_proposals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [("user", "hola"), ("assistant", "que tal")])
    monkeypatch.setattr(session_end, "detect", lambda _: [])

    rc = session_end.run({"transcript_path": str(transcript), "session_id": "abc"})
    assert rc == 0
    assert proposals.list_all() == []


def test_candidates_become_proposals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_transcript(
        transcript,
        [
            ("user", "para Felisa decidi usar SessionEnd hook en vez de Stop"),
            ("assistant", "claro, eso evita ruido"),
        ],
    )
    monkeypatch.setattr(
        session_end,
        "detect",
        lambda _: [
            Candidate(
                texto="decidi usar SessionEnd hook",
                contexto="Fase 6, evita ruido",
                tipo_sugerido="decision_tecnica",
                confianza=0.85,
            ),
        ],
    )

    rc = session_end.run({"transcript_path": str(transcript), "session_id": "abc"})
    assert rc == 0
    pending = proposals.list_pending()
    assert len(pending) == 1
    assert pending[0].source == "hook:session_end"
    assert pending[0].texto == "decidi usar SessionEnd hook"


def test_missing_transcript_path_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    rc = session_end.run({})
    assert rc == 0


def test_nonexistent_transcript_returns_zero(tmp_path: Path) -> None:
    rc = session_end.run({"transcript_path": str(tmp_path / "no-existe.jsonl")})
    assert rc == 0


def test_detect_raises_returns_zero_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [("user", "x" * 100)])

    def boom(_: str):
        raise RuntimeError("haiku timeout")

    monkeypatch.setattr(session_end, "detect", boom)

    rc = session_end.run({"transcript_path": str(transcript)})
    assert rc == 0
    assert proposals.list_all() == []


def test_extract_text_handles_block_list() -> None:
    event = {
        "type": "assistant",
        "content": [
            {"type": "text", "text": "primera parte"},
            {"type": "tool_use", "name": "Bash"},
            {"type": "text", "text": "segunda parte"},
        ],
    }
    out = session_end._extract_text(event)
    assert "primera parte" in out
    assert "segunda parte" in out
    assert "tool_use" not in out


def test_extract_text_handles_string_content() -> None:
    assert session_end._extract_text({"role": "user", "content": "texto crudo"}) == "texto crudo"


def test_extract_text_handles_nested_message() -> None:
    event = {"message": {"content": [{"type": "text", "text": "anidado"}]}}
    assert session_end._extract_text(event) == "anidado"
