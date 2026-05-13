"""Credenciales y configuracion de Felisa.

Centraliza:
- API keys desde Keychain de macOS (Anthropic, Groq, Telegram)
- DATABASE_URL desde .env del proyecto (Railway Postgres public URL)

Cada getter es lazy + cacheado. Las entry points (CLI, daemon) llaman
validate_all() al startup para fallar temprano si falta alguna credencial.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
KEYCHAIN_ACCOUNT = "felisa"


class MissingCredential(RuntimeError):
    """La credencial pedida no esta disponible. El mensaje indica como arreglarlo."""


def _read_keychain(service: str) -> str:
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s", service,
                "-a", KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise MissingCredential(
            f"No se encontro '{service}' en Keychain. "
            f"Guardala con:\n  "
            f"security add-generic-password -s '{service}' -a '{KEYCHAIN_ACCOUNT}' -w"
        ) from exc
    except FileNotFoundError as exc:
        raise MissingCredential(
            "El binario 'security' no esta disponible. Felisa requiere macOS."
        ) from exc
    return result.stdout.strip()


@lru_cache(maxsize=1)
def get_anthropic_key() -> str:
    return _read_keychain("felisa-anthropic-key")


@lru_cache(maxsize=1)
def get_telegram_token() -> str:
    return _read_keychain("felisa-telegram-token")


@lru_cache(maxsize=1)
def get_telegram_chat_id() -> str:
    return _read_keychain("felisa-telegram-chat-id")


@lru_cache(maxsize=1)
def get_groq_key() -> str:
    return _read_keychain("felisa-groq-key")


@lru_cache(maxsize=1)
def get_database_url(env_path: Path | None = None) -> str:
    path = env_path or DEFAULT_ENV_PATH
    if not path.exists():
        raise MissingCredential(
            f"No existe {path}. Crealo con DATABASE_URL=postgresql://...\n  "
            f"railway variables --service Postgres --json | "
            f"python3 -c \"import json,sys; "
            f"print('DATABASE_URL=' + json.load(sys.stdin)['DATABASE_PUBLIC_URL'])\" "
            f"> {path}"
        )
    values = dotenv_values(path)
    url = values.get("DATABASE_URL")
    if not url:
        raise MissingCredential(f"DATABASE_URL no esta seteado en {path}")
    return url


def validate_all() -> None:
    """Fuerza la carga de todas las credenciales.

    Llamar al startup de la CLI o el daemon. Si falta alguna sale con
    MissingCredential antes de hacer trabajo real.
    """
    get_anthropic_key()
    get_telegram_token()
    get_telegram_chat_id()
    get_groq_key()
    get_database_url()
