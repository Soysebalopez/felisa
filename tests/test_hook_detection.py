"""Tests de felisa.core.hook_detection.

Mockean anthropic.Anthropic para no llamar a la API real.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from felisa.core import hook_detection
from felisa.core.hook_detection import DetectionError


@dataclass
class _FakeBlock:
    type: str
    text: str


@dataclass
class _FakeResponse:
    content: list[_FakeBlock]


class _FakeAnthropic:
    def __init__(self, *args, **kwargs) -> None:
        self.messages = self

    def create(self, **_kwargs) -> _FakeResponse:
        raw = self._next_payload
        return _FakeResponse(content=[_FakeBlock(type="text", text=raw)])


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hook_detection, "get_anthropic_key", lambda: "test-key")
    fake = _FakeAnthropic()

    def factory(api_key: str):
        return fake

    monkeypatch.setattr(hook_detection.anthropic, "Anthropic", factory)
    return fake


def test_short_transcript_returns_empty_without_calling_haiku() -> None:
    assert hook_detection.detect("hola") == []


def test_parses_valid_payload(fake_anthropic) -> None:
    fake_anthropic._next_payload = (
        '{"candidatos": [{'
        '"texto": "decidi usar SessionEnd hook",'
        '"contexto": "Fase 6 de Felisa, evita ruido cada turno",'
        '"tipo_sugerido": "decision_tecnica",'
        '"confianza": 0.85'
        '}]}'
    )
    result = hook_detection.detect("texto suficientemente largo " * 5)
    assert len(result) == 1
    assert result[0].texto == "decidi usar SessionEnd hook"
    assert result[0].tipo_sugerido == "decision_tecnica"
    assert result[0].confianza == pytest.approx(0.85)


def test_filters_low_confidence(fake_anthropic) -> None:
    fake_anthropic._next_payload = (
        '{"candidatos": ['
        '{"texto": "fuerte", "contexto": "x", "tipo_sugerido": "patron", "confianza": 0.8},'
        '{"texto": "debil", "contexto": "y", "tipo_sugerido": "patron", "confianza": 0.3}'
        ']}'
    )
    result = hook_detection.detect("texto suficientemente largo " * 5)
    assert len(result) == 1
    assert result[0].texto == "fuerte"


def test_empty_candidates_returns_empty(fake_anthropic) -> None:
    fake_anthropic._next_payload = '{"candidatos": []}'
    assert hook_detection.detect("texto suficientemente largo " * 5) == []


def test_invalid_json_raises(fake_anthropic) -> None:
    fake_anthropic._next_payload = "no es json"
    with pytest.raises(DetectionError):
        hook_detection.detect("texto suficientemente largo " * 5)


def test_unknown_tipo_raises(fake_anthropic) -> None:
    fake_anthropic._next_payload = (
        '{"candidatos": [{"texto": "x", "contexto": "y",'
        '"tipo_sugerido": "tipo_inventado", "confianza": 0.9}]}'
    )
    with pytest.raises(DetectionError):
        hook_detection.detect("texto suficientemente largo " * 5)


def test_strips_markdown_fences(fake_anthropic) -> None:
    fake_anthropic._next_payload = '```json\n{"candidatos": []}\n```'
    assert hook_detection.detect("texto suficientemente largo " * 5) == []
