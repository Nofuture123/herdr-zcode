#!/bin/sh
# Run the pinned nar CLI through the stable wrapper (used by plugin actions).
set -u
sh "$(dirname "$0")/ensure-bridge.sh" >/dev/null 2>&1 || { echo "bridge install failed" >&2; exit 1; }
exec "$HOME/.local/share/qonnwolf-zcode-bridge/bin/nar" "$@"
