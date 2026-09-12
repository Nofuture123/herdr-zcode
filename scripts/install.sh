#!/bin/sh
# qonnwolf-agent-bridge installer — prereq checks FIRST, then bridge + plugin.
# Usage:  sh scripts/install.sh        (in a clone of this repo)
set -u
fail() { echo "✗ $1" >&2; exit 1; }
ok()   { echo "✓ $1"; }

# 1) Herdr — the whole point of this bridge; refuse without it
command -v herdr >/dev/null 2>&1 || fail "herdr not found. Install Herdr first: https://herdr.dev"
HVER="$(herdr --version 2>/dev/null | grep -o '[0-9][0-9.]*' | head -1)"
python3 -c "import sys; v='$HVER'.split('.'); sys.exit(0 if tuple(map(int,(v+['0','0'])[:2])) >= (0,7) else 1)" \
  || fail "herdr >= 0.7.0 required (found $HVER)"
ok "herdr $HVER"

# 2) Other prereqs
command -v node    >/dev/null 2>&1 || fail "Node.js >= 22 not found (brew install node)"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
command -v git     >/dev/null 2>&1 || fail "git not found"
ok "node $(node --version) · $(python3 --version) · git"

# 3) ZCode executor binary
ZCODE="${ZCODE_BIN:-/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs}"
[ -f "$ZCODE" ] || fail "ZCode CLI not found at $ZCODE — install ZCode.app or set ZCODE_BIN"
ok "ZCode CLI: $ZCODE"

# 4) Bridge runtime (pinned, doctor-gated)
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_ROOT="$HERE" sh "$HERE/scripts/ensure-bridge.sh" || fail "bridge runtime install failed"
ok "bridge runtime installed + doctor ok"

# 5) Plugin registration (skip if already linked/installed)
if herdr plugin list 2>/dev/null | grep -q "qonnwolf.zcode-bridge"; then
  ok "plugin already registered in herdr"
else
  herdr plugin link "$HERE" >/dev/null || fail "herdr plugin link failed"
  ok "plugin linked into herdr"
fi

# 6) Shared agent skill + optional MCP injection
sh "$HERE/scripts/install-skill.sh" || fail "skill install failed"
sh "$HERE/scripts/inject-mcp.sh" all || echo "ℹ MCP injection skipped/failed — CLI mode still works"

echo
echo "All set. Try:  zcodecli          (start a ZCode session in any pane)"
echo "         or:   zcodecli chat-open (spawn a dedicated session tab)"
