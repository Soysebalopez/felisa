"""Tests de felisa.core.structuring contra Haiku real.

Cada test cuesta ~$0.0008. Suite total ~$0.01. Validan que el prompt
default clasifica correctamente los tipos. No asserta sobre `space_id`
especifico (la asignacion de espacio depende de la lista que tenga el
usuario y de sus prompts personalizados).
"""

from __future__ import annotations

import pytest

from felisa.core import structuring
from felisa.core.config import MissingCredential


def _have_anthropic() -> bool:
    try:
        from felisa.core.config import get_anthropic_key
        get_anthropic_key()
        return True
    except (MissingCredential, Exception):
        return False


pytestmark = pytest.mark.skipif(
    not _have_anthropic(),
    reason="ANTHROPIC_API_KEY no disponible (esperado en CI sin creds)",
)


AVAILABLE_SPACES = ["global", "trabajo", "personal"]


def test_load_prompt_template_strips_notes_section() -> None:
    system = structuring._build_system_prompt(AVAILABLE_SPACES)
    assert "Sos el clasificador" in system
    assert structuring.NOTES_MARKER not in system
    for sid in AVAILABLE_SPACES:
        assert f"`{sid}`" in system


def test_empty_input_raises() -> None:
    with pytest.raises(ValueError):
        structuring.structure("")
    with pytest.raises(ValueError):
        structuring.structure("   ")


def test_classify_decision_tecnica() -> None:
    result = structuring.structure(
        "decidi usar Mapbox sobre Google Maps en MiApp porque necesito "
        "control total del estilo visual del mapa",
        space_ids=AVAILABLE_SPACES,
    )
    assert result.tipo == "decision_tecnica"
    assert result.proyecto == "MiApp"
    assert any("mapbox" in t.lower() for t in result.tags)
    assert result.space_id in AVAILABLE_SPACES


def test_classify_framework_rule() -> None:
    result = structuring.structure(
        "nunca usar SUPABASE_SERVICE_ROLE_KEY en automatizaciones",
        space_ids=AVAILABLE_SPACES,
    )
    assert result.tipo == "framework"
    assert result.space_id == "global"


def test_classify_modo_trabajo() -> None:
    result = structuring.structure(
        "prefiero respuestas directas sin preambulo, sin listas innecesarias",
        space_ids=AVAILABLE_SPACES,
    )
    assert result.tipo == "modo_trabajo"
    assert result.space_id == "global"


def test_classify_patron_with_multiple_projects() -> None:
    result = structuring.structure(
        "Stripe Connect para split de pagos. Lo use en ProyectoA y ProyectoB. "
        "Candidato a aplicar en ProyectoC",
        space_ids=AVAILABLE_SPACES,
    )
    assert result.tipo == "patron"
    assert result.space_id in AVAILABLE_SPACES


def test_classify_global_personal() -> None:
    result = structuring.structure(
        "stack default: Next.js 15 con Tailwind y Postgres",
        space_ids=AVAILABLE_SPACES,
    )
    assert result.tipo == "global"
    assert result.space_id == "global"
    assert result.proyecto is None
