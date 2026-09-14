#!/bin/sh
# Inject the zcode MCP server into supported CLI agents. Idempotent, backups first.
#   sh inject-mcp.sh all | codex | claude
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 "$HERE/ensure_bridge.py" >/dev/null 2>&1 || { echo "bridge install failed" >&2; exit 1; }
MCP="$HOME/.local/share/herdr-zcode/bin/zcodecli-mcp"
TARGET="${1:-all}"

inject_codex() {
  CFG="$HOME/.codex/config.toml"
  [ -f "$CFG" ] || { echo "codex: no config.toml, skipped"; return 0; }
  # Refresh: strip any block we injected before (current or legacy marker),
  # then append the block with the current path.
  if grep -q "# BEGIN herdr-zcode\|# BEGIN qonnwolf-zcode-bridge" "$CFG"; then
    cp "$CFG" "$CFG.bak-qab-$(date +%Y%m%d-%H%M%S)" || return 1
    perl -0pi -e 's/\n?# BEGIN (?:herdr-zcode|qonnwolf-zcode-bridge).*?# END (?:herdr-zcode|qonnwolf-zcode-bridge)\n//sg' "$CFG"
  fi
  cat >> "$CFG" <<BLOCK

# BEGIN herdr-zcode (remove block or run plugin uninject to restore)
[mcp_servers.zcodecli]
command = "$MCP"
args = []
# END herdr-zcode
BLOCK
  echo "codex: injected (backup created if replaced)"
}

inject_claude() {
  command -v claude >/dev/null || { echo "claude: CLI not found, skipped"; return 0; }
  if claude mcp get zcodecli >/dev/null 2>&1; then
    claude mcp remove --scope user zcodecli >/dev/null 2>&1 || true
  fi
  claude mcp add --scope user zcodecli "$MCP" && echo "claude: injected"
}

case "$TARGET" in
  all)    inject_codex; inject_claude ;;
  codex)  inject_codex ;;
  claude) inject_claude ;;
  *) echo "usage: inject-mcp.sh all|codex|claude"; exit 2 ;;
esac
