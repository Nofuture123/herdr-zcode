#!/bin/sh
# Inject the qab MCP server into supported CLI agents. Idempotent, backups first.
#   sh inject-mcp.sh all | codex | claude
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
sh "$HERE/ensure-bridge.sh" >/dev/null 2>&1 || { echo "bridge install failed" >&2; exit 1; }
MCP="$HOME/.local/share/qonnwolf-zcode-bridge/bin/zcodecli-mcp"
TARGET="${1:-all}"

inject_codex() {
  CFG="$HOME/.codex/config.toml"
  [ -f "$CFG" ] || { echo "codex: no config.toml, skipped"; return 0; }
  if grep -q "# BEGIN qonnwolf-zcode-bridge" "$CFG"; then
    echo "codex: already injected"; return 0
  fi
  cp "$CFG" "$CFG.bak-qab-$(date +%Y%m%d-%H%M%S)" || return 1
  cat >> "$CFG" <<BLOCK

# BEGIN qonnwolf-zcode-bridge (remove block or run plugin uninject to restore)
[mcp_servers.zcodecli]
command = "$MCP"
args = []
# END qonnwolf-zcode-bridge
BLOCK
  echo "codex: injected (backup created)"
}

inject_claude() {
  command -v claude >/dev/null || { echo "claude: CLI not found, skipped"; return 0; }
  if claude mcp list 2>/dev/null | grep -q "^zcodecli"; then
    echo "claude: already injected"; return 0
  fi
  claude mcp add --scope user zcodecli "$MCP" && echo "claude: injected"
}

case "$TARGET" in
  all)    inject_codex; inject_claude ;;
  codex)  inject_codex ;;
  claude) inject_claude ;;
  *) echo "usage: inject-mcp.sh all|codex|claude"; exit 2 ;;
esac
