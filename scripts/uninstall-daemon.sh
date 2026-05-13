#!/usr/bin/env bash
# Desinstala el LaunchAgent de Felisa.

set -euo pipefail

LABEL="com.felisa.daemon"
TARGET_PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if launchctl print "${DOMAIN}/${LABEL}" &>/dev/null; then
    echo "→ bootout"
    launchctl bootout "${DOMAIN}/${LABEL}" || true
fi

if [[ -f "${TARGET_PLIST}" ]]; then
    echo "→ borrando ${TARGET_PLIST}"
    rm -f "${TARGET_PLIST}"
fi

echo "daemon desinstalado. La cola y los logs en ~/.felisa/ se mantienen."
