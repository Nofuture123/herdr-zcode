#!/bin/sh
# herdr plugin build step (runs on `herdr plugin install`). Fails loudly on missing prereqs.
set -u
fail() { echo "✗ [zcode-bridge] $1" >&2; exit 1; }
command -v node    >/dev/null 2>&1 || fail "Node.js >= 22 required (brew install node)"
command -v python3 >/dev/null 2>&1 || fail "python3 required"
[ -f "${ZCODE_BIN:-/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs}" ] \
  || [ -n "$(command -v zcode || true)" ] \
  || fail "ZCode executor not found — install ZCode.app (or set ZCODE_BIN)"

# Login gate: headless ZCode and the TUI both need Z.AI OAuth credentials,
# which `zcode login` writes to ~/.zcode/v2/credentials.json (the provider
# segment varies: oauth:zai:... / oauth:bigmodel:...).
CRED_FILE="${ZCODE_DATA_BASE_DIR:-$HOME}/.zcode/v2/credentials.json"
if [ ! -f "$CRED_FILE" ] || ! grep -q '"oauth:[a-z][a-z0-9_]*:access_token"' "$CRED_FILE"; then
  fail "ZCode is not logged in (no OAuth credentials in $CRED_FILE).
    Fix:  1. run: zcode login
          2. finish the sign-in in the browser
          3. reinstall: herdr plugin install Nofuture123/herdr-zcode"
fi

sh "$(dirname "$0")/ensure-bridge.sh" || fail "bridge runtime bootstrap failed"
echo "✓ [zcode-bridge] build ok"
