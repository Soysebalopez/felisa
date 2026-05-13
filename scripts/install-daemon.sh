#!/usr/bin/env bash
# Instala el LaunchAgent de Felisa: copia el plist con paths sustituidos
# y lo carga via launchctl bootstrap.
#
# Idempotente: si ya esta cargado, hace bootout primero.

set -euo pipefail

FELISA_HOME="$(cd "$(dirname "$0")/.." && pwd)"
USER_HOME="${HOME}"
LABEL="com.felisa.daemon"
TARGET_PLIST="${USER_HOME}/Library/LaunchAgents/${LABEL}.plist"
SOURCE_PLIST="${FELISA_HOME}/scripts/${LABEL}.plist"

if [[ ! -f "${SOURCE_PLIST}" ]]; then
    echo "error: no encuentro ${SOURCE_PLIST}" >&2
    exit 1
fi

mkdir -p "${USER_HOME}/.felisa"
mkdir -p "${USER_HOME}/Library/LaunchAgents"

echo "→ generando ${TARGET_PLIST}"
sed \
    -e "s|__FELISA_HOME__|${FELISA_HOME}|g" \
    -e "s|__USER_HOME__|${USER_HOME}|g" \
    "${SOURCE_PLIST}" \
    > "${TARGET_PLIST}"

DOMAIN="gui/$(id -u)"

if launchctl print "${DOMAIN}/${LABEL}" &>/dev/null; then
    echo "→ ya estaba cargado, bootout antes de recargar"
    launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
fi

echo "→ bootstrap"
launchctl bootstrap "${DOMAIN}" "${TARGET_PLIST}"
launchctl enable "${DOMAIN}/${LABEL}"

echo "→ kickstart (forzar primer run)"
launchctl kickstart -k "${DOMAIN}/${LABEL}"

sleep 2
echo ""
echo "=== estado ==="
launchctl print "${DOMAIN}/${LABEL}" | grep -E "state|pid|last exit" | head -5
echo ""
echo "logs: ${USER_HOME}/.felisa/daemon.log"
echo "stdout: ${USER_HOME}/.felisa/daemon.stdout.log"
echo "stderr: ${USER_HOME}/.felisa/daemon.stderr.log"
