"""Tests del flujo de propuestas en el bot de Telegram.

Cubre:
- dispatch_pending_proposals envia propuestas sin telegram_message_id y persiste el id.
- callback approve llama pipeline.process y marca approved + edita mensaje.
- callback reject marca rejected sin tocar el pipeline.
- callback defer no cambia status.
- callback con propuesta inexistente acka sin fallar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from felisa.core import proposals
from felisa.core.proposals import Proposal
from felisa.core.structuring import StructuredMemory
from felisa.telegram.bot import CALLBACK_PREFIX, TelegramBot

CHAT_ID = 12345


@pytest.fixture(autouse=True)
def isolated_proposals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposals, "PROPOSALS_DIR", tmp_path)
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(proposals, "LOCK_PATH", tmp_path / "proposals.lock")


def _add(texto: str = "decidi X") -> Proposal:
    p = Proposal(
        texto=texto,
        contexto="test contexto",
        tipo_sugerido="decision_tecnica",
        confianza=0.8,
        source="hook:session_end",
    )
    proposals.add(p)
    return p


def _structured() -> StructuredMemory:
    return StructuredMemory(
        contenido_estructurado="X",
        tipo="decision_tecnica",
        space_id="global",
        proyecto=None,
        tags=[],
    )


class FakeProposalAPI:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.acks: list[dict[str, Any]] = []
        self._next_message_id = 100

    async def send_message(
        self, *, chat_id, text, parse_mode=None, reply_markup=None,
    ) -> dict[str, Any]:
        message_id = self._next_message_id
        self._next_message_id += 1
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "message_id": message_id,
            }
        )
        return {"message_id": message_id}

    async def edit_message_text(
        self, *, chat_id, message_id, text, parse_mode=None, reply_markup=None,
    ) -> dict[str, Any]:
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": message_id}

    async def answer_callback_query(
        self, *, callback_query_id, text=None,
    ) -> bool:
        self.acks.append({"id": callback_query_id, "text": text})
        return True


def _make_bot(api: FakeProposalAPI, tmp_path: Path) -> TelegramBot:
    return TelegramBot(
        api,  # type: ignore[arg-type]
        chat_id=CHAT_ID,
        agent=None,
        transcribe=None,
        offset_path=tmp_path / "offset",
    )


async def test_dispatch_envia_pendientes_y_persiste_message_id(tmp_path: Path) -> None:
    p = _add("para Felisa decidi X")
    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)

    sent = await bot.dispatch_pending_proposals()
    assert sent == 1
    assert len(api.sent) == 1
    payload = api.sent[0]
    assert payload["chat_id"] == CHAT_ID
    keyboard = payload["reply_markup"]["inline_keyboard"][0]
    actions = {btn["callback_data"].rsplit(":", 1)[1] for btn in keyboard}
    assert actions == {"approve", "reject", "defer"}

    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.telegram_message_id == payload["message_id"]


async def test_dispatch_no_reenvia_si_ya_tiene_message_id(tmp_path: Path) -> None:
    p = _add()
    proposals.set_telegram_message_id(p.id, 7)
    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)
    sent = await bot.dispatch_pending_proposals()
    assert sent == 0
    assert api.sent == []


def _callback_update(
    *, proposal_id: str, action: str, callback_id: str = "cb-1", message_id: int = 500,
) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": callback_id,
            "data": f"{CALLBACK_PREFIX}:{proposal_id}:{action}",
            "message": {
                "chat": {"id": CHAT_ID},
                "message_id": message_id,
            },
        },
    }


async def test_callback_approve_llama_pipeline_y_edita(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _add("para Felisa decidi X")
    fake_uuid = UUID("00000000-0000-0000-0000-000000000010")
    calls: list[dict] = []

    def fake_process(texto, *, tipo_override=None, espacio_override=None, skip_unclassified=False):
        calls.append({"texto": texto, "tipo_override": tipo_override})
        return fake_uuid, _structured()

    monkeypatch.setattr("felisa.telegram.bot.pipeline.process", fake_process)

    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)
    await bot._handle_update(_callback_update(proposal_id=p.id, action="approve"))

    assert calls[0]["texto"] == p.texto
    assert calls[0]["tipo_override"] == p.tipo_sugerido
    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.status == "approved"
    assert len(api.edits) == 1
    assert len(api.acks) == 1
    assert api.acks[0]["text"] == "guardada"


async def test_callback_reject_marca_y_no_llama_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _add()
    boom = lambda *a, **k: pytest.fail("pipeline no deberia ser llamado")  # noqa: E731
    monkeypatch.setattr("felisa.telegram.bot.pipeline.process", boom)

    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)
    await bot._handle_update(_callback_update(proposal_id=p.id, action="reject"))

    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.status == "rejected"
    assert len(api.acks) == 1


async def test_callback_defer_deja_pendiente(tmp_path: Path) -> None:
    p = _add()
    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)
    await bot._handle_update(_callback_update(proposal_id=p.id, action="defer"))

    stored = proposals.get(p.id)
    assert stored is not None
    assert stored.status == "pending"
    assert len(api.acks) == 1
    assert api.edits == []


async def test_callback_propuesta_inexistente_acka_sin_romper(tmp_path: Path) -> None:
    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)
    await bot._handle_update(
        _callback_update(proposal_id="no-existe", action="approve"),
    )
    assert len(api.acks) == 1
    assert api.acks[0]["text"] == "propuesta no encontrada"


async def test_callback_data_invalido_acka_y_no_falla(tmp_path: Path) -> None:
    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cb-x",
            "data": "garbage",
            "message": {"chat": {"id": CHAT_ID}, "message_id": 1},
        },
    }
    await bot._handle_update(update)
    assert len(api.acks) == 1


async def test_callback_chat_ajeno_ignorado(tmp_path: Path) -> None:
    api = FakeProposalAPI()
    bot = _make_bot(api, tmp_path)
    update = {
        "update_id": 3,
        "callback_query": {
            "id": "cb-y",
            "data": f"{CALLBACK_PREFIX}:foo:approve",
            "message": {"chat": {"id": 99999}, "message_id": 1},
        },
    }
    await bot._handle_update(update)
    # Acka pero no toca proposals
    assert len(api.acks) == 1
    assert proposals.list_all() == []
