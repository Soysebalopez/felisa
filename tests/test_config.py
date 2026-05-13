"""Tests basicos de felisa.core.config.

Requieren que las credenciales esten en Keychain y .env. Si no, los tests
de credenciales reales hacen skip — el de validacion de errores corre siempre.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from felisa.core import config


def test_anthropic_key_format() -> None:
    key = config.get_anthropic_key()
    assert key.startswith("sk-ant-"), "API key de Anthropic debe empezar con sk-ant-"
    assert len(key) > 50


def test_telegram_token_format() -> None:
    token = config.get_telegram_token()
    bot_id, _, secret = token.partition(":")
    assert bot_id.isdigit()
    assert len(secret) >= 30


def test_telegram_chat_id_is_int() -> None:
    chat_id = config.get_telegram_chat_id()
    int(chat_id)


def test_groq_key_format() -> None:
    key = config.get_groq_key()
    assert key.startswith("gsk_")


def test_database_url_format() -> None:
    url = config.get_database_url()
    assert url.startswith("postgresql://")
    assert "railway" in url.lower() or "rlwy" in url.lower()


def test_validate_all_passes() -> None:
    config.validate_all()


def test_missing_env_file_raises(tmp_path: Path) -> None:
    config.get_database_url.cache_clear()
    fake = tmp_path / "no-existe.env"
    with pytest.raises(config.MissingCredential, match="No existe"):
        config.get_database_url(env_path=fake)
    config.get_database_url.cache_clear()


def test_empty_env_file_raises(tmp_path: Path) -> None:
    config.get_database_url.cache_clear()
    empty = tmp_path / "empty.env"
    empty.write_text("OTHER_VAR=foo\n")
    with pytest.raises(config.MissingCredential, match="DATABASE_URL no esta seteado"):
        config.get_database_url(env_path=empty)
    config.get_database_url.cache_clear()
