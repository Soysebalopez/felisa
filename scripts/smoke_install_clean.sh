#!/usr/bin/env bash
# Smoke test E2E del installer en una maquina limpia.
#
# No es para correr en CI — requiere intervencion humana (pegar claves reales)
# y una DB real. Sirve para validar la experiencia de instalacion antes de
# soltar el repo al mundo.
#
# Recomendacion: correr esto en un container Docker, una VM, o una cuenta de
# usuario nueva en tu Mac (no tu cuenta principal — pq toca el Keychain).
#
# Uso:
#   bash scripts/smoke_install_clean.sh
#
# Lo que valida:
#   1. uv esta disponible (precondicion).
#   2. `uv sync` no rompe.
#   3. install.py corre end-to-end sin error.
#   4. `mem "test"` captura y vuelve a aparecer en `mem listar`.
#   5. `felisa "hola"` responde.

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✓${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; exit 1; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
step()  { echo -e "\n${GREEN}▸${NC} $*"; }

# ── Pre-flight ────────────────────────────────────────────────────────────

step "Pre-flight"

if ! command -v uv >/dev/null 2>&1; then
    fail "uv no esta. Instalalo: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi
ok "uv disponible"

if [ ! -f "pyproject.toml" ]; then
    fail "Hay que correr esto desde la raiz del repo de felisa."
fi
ok "estoy en la raiz del repo"

# ── Sync deps ─────────────────────────────────────────────────────────────

step "uv sync"
uv sync --quiet
ok "deps instaladas"

# ── Tests previos ─────────────────────────────────────────────────────────

step "Tests unitarios del installer"
uv run pytest tests/test_install.py -q
ok "unit tests del installer OK"

# ── Suite completa ────────────────────────────────────────────────────────

step "Suite completa"
if uv run pytest -q; then
    ok "suite completa OK"
else
    warn "algunos tests fallaron — chequea si son los de DB real (requieren creds)"
fi

# ── Lint ──────────────────────────────────────────────────────────────────

step "Lint con ruff"
uv run ruff check .
ok "ruff sin errores"

# ── install.py interactivo ────────────────────────────────────────────────

step "install.py (interactivo)"
echo "  Te toca ahora pegar tus claves cuando el wizard las pida."
echo "  Si es un ambiente de prueba, podes usar claves de cuentas de testing."
echo
read -p "  ¿Listo para arrancar el wizard? (enter para seguir, Ctrl+C para abortar)"

uv run python scripts/install.py
ok "install.py termino"

# ── Smoke test E2E ────────────────────────────────────────────────────────

step "Captura una memoria de prueba"
TEST_TEXT="smoke test $(date +%s): instalacion verificada"
uv run mem "$TEST_TEXT"
ok "mem capturo"

step "Listar para verificar que aparezca"
uv run mem listar | head -10
read -p "  ¿Aparece la memoria recien capturada? (y/n) " confirm
[ "$confirm" = "y" ] || fail "memoria no aparece — algo del pipeline esta roto"
ok "memoria capturada y listada"

step "Smoke del agente (one-shot, opcional)"
echo "  Mandamos al agente: 'cuantas memorias tengo en global?'"
uv run felisa "cuantas memorias tengo en global?" || warn "agente fallo (revisa logs)"

# ── Daemon (si esta instalado) ────────────────────────────────────────────

step "Daemon (si lo instalaste con LaunchAgent)"
if [ -f ~/Library/LaunchAgents/com.felisa.daemon.plist ]; then
    if launchctl print "gui/$(id -u)/com.felisa.daemon" >/dev/null 2>&1; then
        ok "LaunchAgent activo"
    else
        warn "LaunchAgent existe pero no esta corriendo — chequea ~/.felisa/daemon.log"
    fi
else
    warn "LaunchAgent no instalado (modo manual con 'uv run felisa-daemon')"
fi

echo
ok "Smoke test E2E completo."
echo
echo "Proximos pasos para validar 100%:"
echo "  · mandar un mensaje de texto al bot de Telegram (si lo configuraste)"
echo "  · mandar un audio al bot (si tenes Groq configurado)"
echo "  · si deployaste el MCP, conectarlo en claude.ai y preguntarle algo"
