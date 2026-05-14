#!/usr/bin/env python3
"""Wizard interactivo de instalacion de Felisa.

Guia al usuario desde 0 hasta daemon corriendo:
1. Chequea dependencias (uv, git, security/keyring).
2. Pide credenciales una por una con links a donde obtenerlas.
3. Las guarda en Keychain (macOS) o en .env.local (Linux fallback).
4. Pide DATABASE_URL (Railway one-click o pega manual).
5. Aplica el schema SQL.
6. Instala el LaunchAgent (macOS) o printa instrucciones systemd (Linux).
7. Opcional: hook SessionEnd de Claude Code para captura automatica.
8. Smoke test final.

Idempotente: si lo corres dos veces no rompe nada — saltea lo que ya esta.

Run: python scripts/install.py
"""

from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = REPO_ROOT / ".env.local"
FELISA_HOME = Path.home() / ".felisa"

IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


# ── Salida coloreada (sin dep externa) ────────────────────────────────────

def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def ok(msg: str) -> None:
    print(f"  {_c('32', '✓')} {msg}")


def warn(msg: str) -> None:
    print(f"  {_c('33', '!')} {msg}")


def err(msg: str) -> None:
    print(f"  {_c('31', '✗')} {msg}")


def step(msg: str) -> None:
    print(f"\n{_c('1;36', '▸')} {_c('1', msg)}")


def ask(prompt: str, *, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    if secret:
        return getpass.getpass(f"  {prompt}: ").strip()
    raw = input(f"  {prompt}{suffix}: ").strip()
    return raw or default


def ask_yn(prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"  {prompt} {suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "s", "si", "sí"):
            return True
        if raw in ("n", "no"):
            return False
        print(f"  Respuesta '{raw}' no es y/n.")


# ── Step 1: deps ──────────────────────────────────────────────────────────

REQUIRED_BINARIES = {
    "uv": "Package manager Python. Instalar: curl -LsSf https://astral.sh/uv/install.sh | sh",
    "git": "Version control. Instalar: brew install git (Mac) o apt install git (Linux)",
}


def check_dependencies() -> None:
    step("1/7 — Verificando dependencias")
    missing = []
    for binary, hint in REQUIRED_BINARIES.items():
        if shutil.which(binary):
            ok(f"{binary} encontrado")
        else:
            err(f"{binary} no esta. {hint}")
            missing.append(binary)

    if IS_MAC and not shutil.which("security"):
        err("`security` (Keychain) no esta — necesario en macOS")
        missing.append("security")

    if IS_LINUX:
        try:
            import keyring  # noqa: F401
            ok("keyring (Python) disponible")
        except ImportError:
            warn("paquete `keyring` no encontrado. Se va a usar .env.local como fallback.")

    if missing:
        sys.exit(1)


# ── Step 2: credentials ───────────────────────────────────────────────────

CREDENTIALS = [
    {
        "slot": "felisa-anthropic-key",
        "env": "ANTHROPIC_API_KEY",
        "name": "Anthropic API key",
        "url": "https://console.anthropic.com/settings/keys",
        "required": True,
        "hint": "Empieza con 'sk-ant-'. Necesario para Haiku (clasificacion) y Sonnet (agente).",
    },
    {
        "slot": "felisa-cf-account-id",
        "env": "CLOUDFLARE_ACCOUNT_ID",
        "name": "Cloudflare Account ID",
        "url": "https://dash.cloudflare.com/ → seleccionar account → sidebar derecha 'Account ID'",
        "required": True,
        "hint": "Necesario para embeddings (Workers AI). El plan gratis alcanza para uso personal.",
    },
    {
        "slot": "felisa-cf-token",
        "env": "CLOUDFLARE_API_TOKEN",
        "name": "Cloudflare API token",
        "url": "https://dash.cloudflare.com/profile/api-tokens → Create Token → 'Workers AI'",
        "required": True,
        "hint": "Permiso necesario: Account > Workers AI > Edit.",
    },
    {
        "slot": "felisa-groq-key",
        "env": "GROQ_API_KEY",
        "name": "Groq API key (voz)",
        "url": "https://console.groq.com/keys",
        "required": False,
        "hint": "Solo si vas a usar el bot Telegram con mensajes de voz (Whisper).",
    },
    {
        "slot": "felisa-telegram-token",
        "env": "TELEGRAM_TOKEN",
        "name": "Telegram bot token",
        "url": "https://t.me/BotFather → /newbot → seguir prompts → copiar token",
        "required": False,
        "hint": "Solo si queres capturar memorias desde el celular. Ver docs/TELEGRAM.md.",
    },
    {
        "slot": "felisa-telegram-chat-id",
        "env": "TELEGRAM_CHAT_ID",
        "name": "Telegram chat ID (tu user)",
        "url": "https://t.me/userinfobot → /start → te dice tu chat_id (un numero)",
        "required": False,
        "hint": "El bot SOLO responde a este chat_id. Defensa contra mensajes ajenos.",
    },
]


def _keychain_has(slot: str) -> bool:
    if IS_MAC:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", slot, "-a", "felisa"],
            capture_output=True,
        )
        return r.returncode == 0
    if IS_LINUX:
        try:
            import keyring
            return keyring.get_password(slot, "felisa") is not None
        except ImportError:
            return False
    return False


def _keychain_set(slot: str, value: str) -> None:
    if IS_MAC:
        subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", slot, "-a", "felisa", "-w", value],
            check=True,
        )
        return
    if IS_LINUX:
        try:
            import keyring
            keyring.set_password(slot, "felisa", value)
            return
        except ImportError:
            pass
    # Fallback final: env.local (Linux sin keyring, u OS desconocido).
    raise RuntimeError("No hay backend de credenciales — usa _env_local_set")


def _env_local_set(key: str, value: str) -> None:
    lines = []
    if ENV_LOCAL.exists():
        for line in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
            if not line.startswith(f"{key}="):
                lines.append(line)
    lines.append(f"{key}={value}")
    ENV_LOCAL.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV_LOCAL.chmod(0o600)


def _have_keyring() -> bool:
    if IS_MAC:
        return True
    if IS_LINUX:
        try:
            import keyring  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def store_credential(cred: dict, value: str) -> None:
    if _have_keyring():
        try:
            _keychain_set(cred["slot"], value)
            backend = "Keychain" if IS_MAC else "keyring"
            ok(f"{cred['name']} → {backend} ({cred['slot']})")
            return
        except Exception as exc:
            warn(f"backend de credenciales fallo ({exc}); usando .env.local")
    _env_local_set(cred["env"], value)
    ok(f"{cred['name']} → {ENV_LOCAL.name} ({cred['env']})")


def collect_credentials() -> None:
    step("2/7 — Credenciales")
    for cred in CREDENTIALS:
        if IS_MAC and _keychain_has(cred["slot"]):
            ok(f"{cred['name']} ya esta en Keychain (omito)")
            continue

        label = "obligatoria" if cred["required"] else "opcional"
        print(f"\n  · {_c('1', cred['name'])} ({label})")
        print(f"    {cred['hint']}")
        print(f"    obtener: {cred['url']}")

        if not cred["required"] and not ask_yn(f"    ¿Configurar {cred['name']}?", default=False):
            continue

        value = ask("    pega el valor", secret=True)
        if not value:
            if cred["required"]:
                err("vacio. Es obligatoria. Abortando.")
                sys.exit(1)
            warn("vacio. Saltando.")
            continue
        store_credential(cred, value)


# ── Step 3: user name ─────────────────────────────────────────────────────

def collect_user_name() -> None:
    step("3/7 — Nombre para personalizar el agente")
    if os.environ.get("FELISA_USER_NAME"):
        ok(f"FELISA_USER_NAME ya seteado en environment: {os.environ['FELISA_USER_NAME']}")
        return
    name = ask("Tu nombre (como te llama el agente)", default="el usuario")
    _env_local_set("FELISA_USER_NAME", name)
    ok(f"FELISA_USER_NAME → {ENV_LOCAL.name}")


# ── Step 4: database ──────────────────────────────────────────────────────

RAILWAY_TEMPLATE_URL = "https://railway.app/template/felisa"  # placeholder


def collect_database() -> None:
    step("4/7 — Base de datos Postgres (con pgvector)")
    existing = os.environ.get("DATABASE_URL", "").strip()
    if ENV_LOCAL.exists():
        for line in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                existing = line.split("=", 1)[1].strip()

    if existing:
        ok(f"DATABASE_URL ya configurado: {existing[:25]}...")
        if not ask_yn("¿Sobreescribir?", default=False):
            return

    print("\n  Felisa necesita Postgres con la extension `vector` (pgvector).")
    print("  Opciones:")
    print(f"    a) Railway one-click template: {RAILWAY_TEMPLATE_URL}")
    print("       (deploy gratuito hasta cierto uso; vinculas y te da DATABASE_URL)")
    print("    b) Tenes ya un Postgres con pgvector: pega DATABASE_URL")
    print("    c) Postgres local: postgres://user:pass@localhost:5432/felisa")

    url = ask("\n  DATABASE_URL", secret=True)
    if not url.startswith("postgres://") and not url.startswith("postgresql://"):
        err("DATABASE_URL no parece valido (debe empezar con postgres:// o postgresql://)")
        sys.exit(1)

    _env_local_set("DATABASE_URL", url)
    ok("DATABASE_URL guardada en .env.local")


# ── Step 5: aplicar schema ────────────────────────────────────────────────

def apply_schema() -> None:
    step("5/7 — Aplicar schema SQL")
    sql_files = sorted((REPO_ROOT / "sql").glob("*.sql"))
    if not sql_files:
        warn("No hay archivos SQL en sql/, salto.")
        return

    # Usar la conexion de felisa.core.db para aplicar (mas portable que psql).
    sys.path.insert(0, str(REPO_ROOT))
    # Cargar env vars de .env.local antes de importar config
    if ENV_LOCAL.exists():
        from dotenv import load_dotenv
        load_dotenv(ENV_LOCAL)

    try:
        from felisa.core import db
    except ImportError as exc:
        err(f"No pude importar felisa.core.db: {exc}. Corre `uv sync` primero.")
        sys.exit(1)

    try:
        with db._get_pool().connection() as conn, conn.cursor() as cur:
            for sql_file in sql_files:
                ok(f"aplicando {sql_file.name}")
                cur.execute(sql_file.read_text(encoding="utf-8"))
            conn.commit()
    except Exception as exc:
        err(f"Fallo aplicando schema: {exc}")
        sys.exit(1)
    ok("Schema aplicado.")


# ── Step 6: daemon ────────────────────────────────────────────────────────

def install_daemon() -> None:
    step("6/7 — Daemon en background")
    if IS_MAC:
        if not ask_yn("¿Instalar LaunchAgent (daemon arranca al iniciar sesion)?", default=True):
            warn("Salteado. Para arrancar manualmente: `uv run felisa-daemon`")
            return
        installer = REPO_ROOT / "scripts" / "install-daemon.sh"
        if not installer.exists():
            warn(f"No existe {installer}. Salteando.")
            return
        subprocess.run(["bash", str(installer)], check=True)
        ok("LaunchAgent instalado.")
    elif IS_LINUX:
        if not ask_yn("¿Instalar systemd user unit (daemon arranca con tu sesion)?", default=True):
            warn("Salteado. Para arrancar manualmente: `uv run felisa-daemon`")
            return
        _install_systemd_unit()
    else:
        warn(f"OS no soportado para daemon automatico: {platform.system()}")


def _install_systemd_unit() -> None:
    template_path = REPO_ROOT / "scripts" / "felisa.service.template"
    if not template_path.exists():
        warn(f"No existe {template_path}. Salteando.")
        return
    uv_path = shutil.which("uv")
    if not uv_path:
        err("uv no esta en PATH — necesario para el ExecStart del unit.")
        return

    rendered = (
        template_path.read_text(encoding="utf-8")
        .replace("{WORKING_DIRECTORY}", str(REPO_ROOT))
        .replace("{UV_PATH}", uv_path)
    )

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "felisa.service"
    unit_path.write_text(rendered, encoding="utf-8")
    ok(f"Unit escrito en {unit_path}")

    print("  Para activarlo:")
    print("    systemctl --user daemon-reload")
    print("    systemctl --user enable --now felisa.service")
    print("    journalctl --user -fu felisa.service  # ver logs")

    if ask_yn("¿Lo activo ahora?", default=True):
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", "felisa.service"],
                check=True,
            )
            ok("Daemon activo (systemctl --user status felisa para verificar)")
        except subprocess.CalledProcessError as exc:
            err(f"systemctl fallo: {exc}. Probalo a mano con los comandos de arriba.")


# ── Step 7: hook de Claude Code ───────────────────────────────────────────

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def install_claude_code_hook() -> None:
    step("7/8 — Hook de captura automatica (Claude Code)")
    if not CLAUDE_SETTINGS_PATH.exists():
        warn(
            f"No existe {CLAUDE_SETTINGS_PATH} — parece que no tenes Claude Code "
            "instalado. Salteando este paso.",
        )
        return
    print(
        "  El hook analiza cada sesion de Claude Code al cerrar y propone\n"
        "  memorias para guardar (Haiku detecta candidatos). Las propuestas\n"
        "  llegan al bot de Telegram (o `mem propuestas` desde CLI) con\n"
        "  botones para Guardar / Descartar / Mas tarde."
    )
    if not ask_yn("¿Activar el hook ahora?", default=False):
        warn("Salteado. Lo podes activar manualmente despues.")
        return

    uv_path = shutil.which("uv")
    if not uv_path:
        err("uv no esta en PATH — necesario para registrar el comando del hook.")
        return

    command = f"{uv_path} run --project {REPO_ROOT} felisa-hook-session-end"
    try:
        _register_claude_code_hook(command)
    except (OSError, json.JSONDecodeError) as exc:
        err(f"no pude modificar {CLAUDE_SETTINGS_PATH}: {exc}")
        return
    ok(f"Hook SessionEnd registrado en {CLAUDE_SETTINGS_PATH}.")
    print(
        "  La proxima vez que cierres una sesion de Claude Code, las propuestas\n"
        "  van a aparecer en Telegram (si tenes el daemon corriendo) o en\n"
        "  `mem propuestas` desde terminal."
    )


def _register_claude_code_hook(command: str) -> None:
    raw = CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8")
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise OSError(f"settings.json no es un objeto JSON (es {type(data).__name__})")

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise OSError("settings.json.hooks no es un objeto")
    session_end = hooks.setdefault("SessionEnd", [])
    if not isinstance(session_end, list):
        raise OSError("settings.json.hooks.SessionEnd no es una lista")

    for entry in session_end:
        if isinstance(entry, dict):
            for hook in entry.get("hooks", []):
                if isinstance(hook, dict) and hook.get("command") == command:
                    print("  (la entrada ya existia, no toco nada)")
                    return

    backup = CLAUDE_SETTINGS_PATH.with_suffix(
        f".json.bak.{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    backup.write_text(raw, encoding="utf-8")
    print(f"  Backup en {backup}")

    session_end.append(
        {
            "hooks": [
                {"type": "command", "command": command},
            ],
        }
    )
    CLAUDE_SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Step 8: smoke test ────────────────────────────────────────────────────

def smoke_test() -> None:
    step("8/8 — Smoke test")
    if not ask_yn("¿Capturar una memoria de prueba ahora?", default=True):
        return
    texto = ask("Texto de prueba", default="primer test de instalacion de Felisa")
    print(f"  Capturando: {texto!r}")
    r = subprocess.run(
        ["uv", "run", "mem", texto],
        cwd=REPO_ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        err(f"Fallo: {r.stderr.strip()}")
        return
    ok("Capturada. Probando search...")
    r = subprocess.run(
        ["uv", "run", "mem", "buscar", "instalacion"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    print(r.stdout)


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    print(_c("1;36", "\n  Felisa — wizard de instalacion\n"))
    print("  Este script te guia paso a paso. Saltea lo que ya este hecho.")
    print("  Tus claves nunca salen de esta computadora.")

    if not ask_yn("\n¿Arrancar?", default=True):
        return 0

    FELISA_HOME.mkdir(parents=True, exist_ok=True)
    check_dependencies()
    collect_credentials()
    collect_user_name()
    collect_database()
    apply_schema()
    install_daemon()
    install_claude_code_hook()
    smoke_test()

    print(_c("1;32", "\n  ✓ Listo.\n"))
    print("  Proximos pasos:")
    print("    · CLI:           mem \"tu primera memoria\"")
    print("    · Agente:        felisa")
    print("    · Telegram:      mandate un mensaje al bot que creaste")
    print("    · claude.ai:     docs/CLAUDE_AI.md (deploy del MCP + integration)")
    print("    · Claude Code:   docs/CLAUDE_AI.md (`claude mcp add ... + bloque en ~/.claude/CLAUDE.md`)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
