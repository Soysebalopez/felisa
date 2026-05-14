"""Credenciales y configuracion de Felisa.

Centraliza:
- API keys desde Keychain de macOS (Anthropic, Groq, Telegram)
- DATABASE_URL desde .env del proyecto (Railway Postgres public URL)

Cada getter es lazy + cacheado. Las entry points (CLI, daemon) llaman
validate_all() al startup para fallar temprano si falta alguna credencial.
"""

from __future__ import annotations

import os
import platform
import subprocess
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
KEYCHAIN_ACCOUNT = "felisa"
IS_MAC = platform.system() == "Darwin"

# Mapping de slot Keychain → nombre de env var de produccion (Railway).
# Si el env var esta seteado, se usa; si no, se lee del Keychain.
_KEYCHAIN_TO_ENV = {
    "felisa-anthropic-key": "ANTHROPIC_API_KEY",
    "felisa-telegram-token": "TELEGRAM_TOKEN",
    "felisa-telegram-chat-id": "TELEGRAM_CHAT_ID",
    "felisa-groq-key": "GROQ_API_KEY",
    "felisa-cf-account-id": "CLOUDFLARE_ACCOUNT_ID",
    "felisa-cf-token": "CLOUDFLARE_API_TOKEN",
    "felisa-mcp-token": "FELISA_API_TOKEN",
}


class MissingCredential(RuntimeError):
    """La credencial pedida no esta disponible. El mensaje indica como arreglarlo."""


def _read_keychain(service: str) -> str:
    """Lee una credencial del secret store del sistema. Prioriza env var.

    Orden:
    1. Env var equivalente si esta seteada (produccion en Railway/CI o user override).
    2. macOS: Keychain via `security find-generic-password`.
    3. Linux: `keyring` package (Secret Service / KWallet / fallback encrypted file).

    En produccion (Railway, Docker, CI) tipicamente solo hay env vars — ahi paso 1
    es el unico que importa. El acceso a Keychain/keyring desde un container suele
    fallar (no hay sesion grafica) y eso esta bien: el env var lo cubre.
    """
    env_var = _KEYCHAIN_TO_ENV.get(service)
    if env_var and os.environ.get(env_var, "").strip():
        return os.environ[env_var].strip()

    if IS_MAC:
        return _read_macos_keychain(service, env_var)
    return _read_linux_keyring(service, env_var)


def _read_macos_keychain(service: str, env_var: str | None) -> str:
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
        env_hint = f" (o env var {env_var})" if env_var else ""
        raise MissingCredential(
            f"No se encontro '{service}'{env_hint} en el Keychain.\n"
            f"  security add-generic-password -s '{service}' -a '{KEYCHAIN_ACCOUNT}' -w"
        ) from exc
    except FileNotFoundError as exc:
        if env_var:
            raise MissingCredential(
                f"El binario 'security' no esta. Seteá la env var {env_var}."
            ) from exc
        raise MissingCredential(
            f"El binario 'security' no esta y no hay env var fallback para '{service}'."
        ) from exc
    return result.stdout.strip()


def _read_linux_keyring(service: str, env_var: str | None) -> str:
    try:
        import keyring  # lazy: solo Linux lo necesita
    except ImportError as exc:
        if env_var:
            raise MissingCredential(
                f"En Linux Felisa lee credenciales via el paquete `keyring`.\n"
                f"Instalalo con `uv sync` o seteá la env var {env_var}."
            ) from exc
        raise MissingCredential(
            f"Falta el paquete `keyring` y no hay env var fallback para '{service}'."
        ) from exc

    value = keyring.get_password(service, KEYCHAIN_ACCOUNT)
    if value:
        return value
    env_hint = f" (o env var {env_var})" if env_var else ""
    raise MissingCredential(
        f"No se encontro '{service}'{env_hint} en keyring.\n"
        f"  python -c \"import keyring; keyring.set_password('{service}', '{KEYCHAIN_ACCOUNT}', 'tu-valor')\""
    )


@lru_cache(maxsize=1)
def get_user_name() -> str:
    """Nombre del usuario que el agente y los prompts usan para personalizar.

    Lee de la env var `FELISA_USER_NAME`. Si falta, devuelve "el usuario".
    A diferencia de las credenciales, este valor no es secreto.
    """
    name = os.environ.get("FELISA_USER_NAME", "").strip()
    return name or "el usuario"


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
def get_cloudflare_account_id() -> str:
    return _read_keychain("felisa-cf-account-id")


@lru_cache(maxsize=1)
def get_cloudflare_token() -> str:
    return _read_keychain("felisa-cf-token")


@lru_cache(maxsize=1)
def get_database_url(env_path: Path | None = None) -> str:
    # Prioridad: env var directa (Railway, CI)
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url

    # Fallback: .env local
    path = env_path or DEFAULT_ENV_PATH
    if not path.exists():
        raise MissingCredential(
            f"No existe DATABASE_URL en env, ni {path}. "
            f"Para dev local: railway variables --service Postgres --json | "
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
    get_cloudflare_account_id()
    get_cloudflare_token()
    get_database_url()
