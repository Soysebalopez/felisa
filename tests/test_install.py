"""Tests unitarios del wizard `scripts/install.py`.

Cubre los helpers que no dependen de Keychain real, DB real, ni input
interactivo. El flujo end-to-end completo se valida con
`scripts/smoke_install_clean.sh` en una VM/container limpio (manual).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_PY = REPO_ROOT / "scripts" / "install.py"


def _load_install_module():
    """Carga scripts/install.py como modulo de forma aislada."""
    spec = importlib.util.spec_from_file_location("install_script", INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def install_mod():
    return _load_install_module()


# ── Importable sin side effects ───────────────────────────────────────────


def test_install_module_loads_without_executing(install_mod) -> None:
    assert hasattr(install_mod, "main")
    assert hasattr(install_mod, "CREDENTIALS")
    assert hasattr(install_mod, "REQUIRED_BINARIES")


# ── _c: colores respetan TTY ──────────────────────────────────────────────


def test_color_skipped_when_not_tty(install_mod) -> None:
    with patch.object(install_mod.sys.stdout, "isatty", return_value=False):
        out = install_mod._c("32", "hola")
    assert out == "hola"


def test_color_applied_when_tty(install_mod) -> None:
    with patch.object(install_mod.sys.stdout, "isatty", return_value=True):
        out = install_mod._c("32", "hola")
    assert "\033[32m" in out
    assert "hola" in out
    assert out.endswith("\033[0m")


# ── ask_yn: parsing de respuestas ─────────────────────────────────────────


@pytest.mark.parametrize("answer,expected", [
    ("y", True), ("Y", True), ("yes", True), ("s", True), ("si", True), ("sí", True),
    ("n", False), ("N", False), ("no", False),
])
def test_ask_yn_parses_responses(install_mod, answer: str, expected: bool) -> None:
    with patch("builtins.input", return_value=answer):
        assert install_mod.ask_yn("test") is expected


def test_ask_yn_returns_default_on_empty(install_mod) -> None:
    with patch("builtins.input", return_value=""):
        assert install_mod.ask_yn("test", default=True) is True
        assert install_mod.ask_yn("test", default=False) is False


def test_ask_yn_reprompts_on_invalid(install_mod) -> None:
    # Primer input invalido, segundo valido. Tiene que reintentar y devolver el segundo.
    with patch("builtins.input", side_effect=["maybe", "y"]):
        assert install_mod.ask_yn("test") is True


# ── _env_local_set: idempotente + permisos ───────────────────────────────


def test_env_local_set_creates_file_with_secure_perms(
    install_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env.local"
    monkeypatch.setattr(install_mod, "ENV_LOCAL", env_path)

    install_mod._env_local_set("FOO", "bar")
    assert env_path.exists()
    assert "FOO=bar" in env_path.read_text()
    # 0600: solo el dueno lee/escribe (sin esto, otros users de la maquina podrian leerlo)
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_env_local_set_updates_existing_key(
    install_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env.local"
    monkeypatch.setattr(install_mod, "ENV_LOCAL", env_path)

    install_mod._env_local_set("FOO", "primero")
    install_mod._env_local_set("FOO", "segundo")

    content = env_path.read_text()
    # Solo una linea con FOO=
    foo_lines = [line for line in content.splitlines() if line.startswith("FOO=")]
    assert foo_lines == ["FOO=segundo"]


def test_env_local_set_preserves_other_keys(
    install_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env.local"
    monkeypatch.setattr(install_mod, "ENV_LOCAL", env_path)

    install_mod._env_local_set("A", "1")
    install_mod._env_local_set("B", "2")
    install_mod._env_local_set("A", "1-updated")

    content = env_path.read_text()
    assert "A=1-updated" in content
    assert "B=2" in content


# ── CREDENTIALS schema consistency ────────────────────────────────────────


def test_credentials_schema_has_required_fields(install_mod) -> None:
    for cred in install_mod.CREDENTIALS:
        assert {"slot", "env", "name", "url", "required", "hint"} <= set(cred.keys()), (
            f"credencial mal definida: {cred}"
        )
        assert cred["slot"].startswith("felisa-"), "slot debe seguir convencion felisa-*"
        assert cred["env"].isupper(), "env var debe estar en MAYUSCULAS"


def test_anthropic_and_cloudflare_are_required(install_mod) -> None:
    """Sin Anthropic + Cloudflare nada del pipeline funciona, son obligatorias."""
    by_slot = {c["slot"]: c for c in install_mod.CREDENTIALS}
    assert by_slot["felisa-anthropic-key"]["required"] is True
    assert by_slot["felisa-cf-account-id"]["required"] is True
    assert by_slot["felisa-cf-token"]["required"] is True


def test_telegram_and_groq_are_optional(install_mod) -> None:
    """Telegram + voz son features opcionales; el sistema arranca sin ellas."""
    by_slot = {c["slot"]: c for c in install_mod.CREDENTIALS}
    assert by_slot["felisa-telegram-token"]["required"] is False
    assert by_slot["felisa-telegram-chat-id"]["required"] is False
    assert by_slot["felisa-groq-key"]["required"] is False


# ── ask: secret no muestra el valor, normal usa default ──────────────────


def test_ask_returns_default_on_empty(install_mod) -> None:
    with patch("builtins.input", return_value=""):
        assert install_mod.ask("texto", default="defecto") == "defecto"


def test_ask_strips_whitespace(install_mod) -> None:
    with patch("builtins.input", return_value="  hola  "):
        assert install_mod.ask("texto") == "hola"


def test_ask_secret_uses_getpass(install_mod) -> None:
    with patch("getpass.getpass", return_value="secreto") as gp:
        result = install_mod.ask("clave", secret=True)
    gp.assert_called_once()
    assert result == "secreto"


# ── Detectores de OS ──────────────────────────────────────────────────────


def test_os_flags_are_mutually_exclusive(install_mod) -> None:
    """Solo un IS_MAC o IS_LINUX puede ser True en una corrida."""
    assert not (install_mod.IS_MAC and install_mod.IS_LINUX)
