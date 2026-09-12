#!/bin/sh
# herdr plugin build step (runs on `herdr plugin install`). Fails loudly on missing prereqs.
set -u
fail() { echo "✗ [zcode-bridge] $1" >&2; exit 1; }
command -v node    >/dev/null 2>&1 || fail "Node.js >= 22 required (brew install node)"
command -v python3 >/dev/null 2>&1 || fail "python3 required"
[ -f "${ZCODE_BIN:-/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs}" ] \
  || [ -n "$(command -v zcode || true)" ] \
  || fail "ZCode executor not found — install ZCode.app (or set ZCODE_BIN)"
sh "$(dirname "$0")/ensure-bridge.sh" || fail "bridge runtime bootstrap failed"
echo "✓ [zcode-bridge] build ok"
