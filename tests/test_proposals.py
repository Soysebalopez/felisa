"""Tests de felisa.core.proposals.

Usan tmp_path para aislarse de ~/.felisa real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from felisa.core import proposals
from felisa.core.proposals import Proposal


@pytest.fixture(autouse=True)
def isolated_proposals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposals, "PROPOSALS_DIR", tmp_path)
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(proposals, "LOCK_PATH", tmp_path / "proposals.lock")


def _make(texto: str = "decidi usar SessionEnd hook", confianza: float = 0.8) -> Proposal:
    return Proposal(
        texto=texto,
        contexto="test",
        tipo_sugerido="decision_tecnica",
        confianza=confianza,
        source="hook:session_end",
    )


def test_empty_storage_lists_nothing() -> None:
    assert proposals.list_all() == []
    assert proposals.list_pending() == []
    assert proposals.count() == 0


def test_add_then_list_pending() -> None:
    p = _make()
    proposals.add(p)
    pending = proposals.list_pending()
    assert len(pending) == 1
    assert pending[0].id == p.id
    assert pending[0].texto == p.texto


def test_add_is_idempotent_by_id() -> None:
    p = _make()
    proposals.add(p)
    proposals.add(p)
    assert proposals.count() == 1


def test_mark_approved_drops_from_pending() -> None:
    p = _make()
    proposals.add(p)
    assert proposals.mark(p.id, "approved") is True
    assert proposals.list_pending() == []
    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.status == "approved"


def test_mark_invalid_id_returns_false() -> None:
    assert proposals.mark("no-existe", "approved") is False


def test_mark_invalid_status_raises() -> None:
    p = _make()
    proposals.add(p)
    with pytest.raises(ValueError):
        proposals.mark(p.id, "bogus")  # type: ignore[arg-type]


def test_set_telegram_message_id() -> None:
    p = _make()
    proposals.add(p)
    assert proposals.set_telegram_message_id(p.id, 42) is True
    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.telegram_message_id == 42


def test_expire_old_marks_overdue() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    p = Proposal(
        texto="vieja",
        contexto="test",
        tipo_sugerido="patron",
        confianza=0.7,
        source="hook:session_end",
        expires_at=past,
    )
    proposals.add(p)
    fresh = _make("nueva")
    proposals.add(fresh)

    affected = proposals.expire_old()
    assert affected == 1
    pending = proposals.list_pending()
    assert {x.id for x in pending} == {fresh.id}


def test_list_pending_filters_by_status_and_ttl() -> None:
    pending = _make("pending-uno")
    proposals.add(pending)
    approved = _make("aprobada")
    proposals.add(approved)
    proposals.mark(approved.id, "approved")

    listed = proposals.list_pending()
    assert {p.id for p in listed} == {pending.id}


def test_count_with_status_filter() -> None:
    a = _make("a")
    b = _make("b")
    proposals.add(a)
    proposals.add(b)
    proposals.mark(b.id, "rejected")
    assert proposals.count() == 2
    assert proposals.count(status="pending") == 1
    assert proposals.count(status="rejected") == 1
