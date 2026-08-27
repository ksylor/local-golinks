#!/usr/bin/env bash
#
# Install golinks as an always-on macOS LaunchDaemon and point `go` at
# this machine. Safe to re-run (it reinstalls / reloads).
#
# Usage: ./install.sh
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="local.golinks"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
PYTHON="$(command -v python3 || true)"

if [[ -z "$PYTHON" ]]; then
    echo "error: python3 not found on PATH." >&2
    echo "Install the Xcode Command Line Tools:  xcode-select --install" >&2
    exit 1
fi

echo "golinks installer"
echo "  directory: $DIR"
echo "  python:    $PYTHON"
echo "  daemon:    $LABEL"
echo "You'll be prompted for your password (sudo) to edit /etc/hosts and"
echo "install a system daemon that binds port 80."
echo

# 1. Point the hostname `go` at localhost.
if grep -qE '^127\.0\.0\.1[[:space:]]+go([[:space:]]|$)' /etc/hosts; then
    echo "hosts:   '127.0.0.1 go' already present, skipping."
else
    echo "hosts:   adding '127.0.0.1 go' to /etc/hosts..."
    echo "127.0.0.1 go" | sudo tee -a /etc/hosts >/dev/null
fi

# 2. Seed the link store on first install (never clobber an existing one).
if [[ ! -f "$DIR/links.json" ]]; then
    cp "$DIR/links.example.json" "$DIR/links.json"
    chmod 0666 "$DIR/links.json"
    echo "store:   created links.json from links.example.json."
fi

# 3. Render the plist template with real paths.
TMP="$(mktemp)"
sed -e "s|__LABEL__|${LABEL}|g" \
    -e "s|__PYTHON__|${PYTHON}|g" \
    -e "s|__DIR__|${DIR}|g" \
    "$DIR/com.golinks.local.plist.template" > "$TMP"

# 4. Install and (re)load the daemon.
echo "daemon:  installing ${PLIST}..."
sudo launchctl unload "$PLIST" 2>/dev/null || true
sudo cp "$TMP" "$PLIST"
sudo chown root:wheel "$PLIST"
sudo chmod 644 "$PLIST"
sudo launchctl load -w "$PLIST"
rm -f "$TMP"

echo
echo "Done. Open http://go/ to manage links."
echo "Try:   curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\\n' http://go/mail"
