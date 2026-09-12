#!/bin/sh
# Remove qab MCP injections made by inject-mcp.sh. Never touches anything else.
set -u
TARGET="${1:-all}"

uninject_codex() {
  CFG="$HOME/.codex/config.toml"
  [ -f "$CFG" ] || return 0
  if grep -q "# BEGIN qonnwolf-zcode-bridge" "$CFG"; then
    cp "$CFG" "$CFG.bak-qab-uninject-$(date +%Y%m%d-%H%M%S)"
    # Delete marker-wrapped block plus the blank line that preceded it
    perl -0pi -e 's/\n?# BEGIN qonnwolf-zcode-bridge.*?# END qonnwolf-zcode-bridge\n//s' "$CFG"
    echo "codex: injected block removed (backup created)"
  else
    echo "codex: nothing to remove"
  fi
}

uninject_claude() {
  command -v claude >/dev/null || return 0
  if claude mcp get zcodecli >/dev/null 2>&1; then
    claude mcp remove --scope user zcodecli && echo "claude: removed"
  else
    echo "claude: nothing to remove"
  fi
}

case "$TARGET" in
  all)    uninject_codex; uninject_claude ;;
  codex)  uninject_codex ;;
  claude) uninject_claude ;;
  *) echo "usage: uninject-mcp.sh all|codex|claude"; exit 2 ;;
esac
