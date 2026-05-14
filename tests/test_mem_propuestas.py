"""Tests del subcomando `mem propuestas`."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from felisa.cli import mem as cli
from felisa.core import proposals
from felisa.core.proposals import Proposal
from felisa.core.structuring import StructuredMemory


@pytest.fixture(autouse=True)
def isolated_proposals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposals, "PROPOSALS_DIR", tmp_path)
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(proposals, "LOCK_PATH", tmp_path / "proposals.lock")


def _add(texto: str = "decidi X") -> Proposal:
    p = Proposal(
        texto=texto,
        contexto="test",
        tipo_sugerido="decision_tecnica",
        confianza=0.8,
        source="hook:session_end",
    )
    proposals.add(p)
    return p


def test_listar_vacio_imprime_aviso(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["propuestas", "listar"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sin propuestas" in out


def test_listar_muestra_pendientes(capsys: pytest.CaptureFixture[str]) -> None:
    p = _add("para Felisa uso SessionEnd")
    rc = cli.main(["propuestas"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "para Felisa uso SessionEnd" in out
    assert p.id[:8] in out


def test_aprobar_llama_pipeline_y_marca(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _add()
    fake_uuid = UUID("00000000-0000-0000-0000-000000000001")
    fake_structured = StructuredMemory(
        contenido_estructurado=p.texto,
        tipo="decision_tecnica",
        space_id="global",
        proyecto=None,
        tags=[],
    )
    calls: list[dict] = []

    def fake_process(texto, *, tipo_override=None, espacio_override=None):
        calls.append({"texto": texto, "tipo_override": tipo_override})
        return fake_uuid, fake_structured

    monkeypatch.setattr(cli.pipeline, "process", fake_process)

    rc = cli.main(["propuestas", "aprobar", "1"])
    assert rc == 0
    assert calls[0]["texto"] == p.texto
    assert calls[0]["tipo_override"] == p.tipo_sugerido
    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.status == "approved"


def test_aprobar_por_prefijo_id(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _add()
    fake_structured = StructuredMemory(
        contenido_estructurado=p.texto,
        tipo="patron",
        space_id="global",
        proyecto=None,
        tags=[],
    )
    monkeypatch.setattr(
        cli.pipeline, "process",
        lambda texto, **_: (UUID("00000000-0000-0000-0000-000000000002"), fake_structured),
    )
    rc = cli.main(["propuestas", "aprobar", p.id[:8]])
    assert rc == 0


def test_aprobar_ref_invalida() -> None:
    _add()
    rc = cli.main(["propuestas", "aprobar", "99"])
    assert rc == 2


def test_descartar_marca_rejected() -> None:
    p = _add()
    rc = cli.main(["propuestas", "descartar", "1"])
    assert rc == 0
    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.status == "rejected"


def test_limpiar_cuenta_expiradas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposals, "expire_old", lambda: 3)
    rc = cli.main(["propuestas", "limpiar"])
    assert rc == 0
