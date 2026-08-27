#!/usr/bin/env bash
#
# Remove the golinks daemon. Leaves your links.json untouched.
# Pass --hosts to also remove the '127.0.0.1 go' line from /etc/hosts.
#
# Usage: ./uninstall.sh [--hosts]
#
set -euo pipefail

LABEL="local.golinks"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"

echo "Stopping and removing ${LABEL} (needs sudo)..."
sudo launchctl unload "$PLIST" 2>/dev/null || true
sudo rm -f "$PLIST"
echo "daemon:  removed."

if [[ "${1:-}" == "--hosts" ]]; then
    echo "hosts:   removing '127.0.0.1 go' from /etc/hosts..."
    sudo sed -i '' '/^127\.0\.0\.1[[:space:]]\{1,\}go$/d' /etc/hosts
fi

echo "Done. Your links.json was left in place."
