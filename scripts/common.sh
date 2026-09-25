#!/bin/sh
# Shared helpers for the ZCode integration plugin. Mirrors the Command Code
# integration pattern: herdr runs plugin commands with the server's PATH,
# which can be a bare system default when the server was started from a
# non-interactive shell — prepend the standard user tool dirs so `zcode`,
# `node` and the herdr binary resolve regardless of how the server launched.
for _dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  case ":$PATH:" in
    *":$_dir:"*) ;;
    *) PATH="$_dir:$PATH" ;;
  esac
done
unset _dir
export PATH

: "${HERDR_BIN_PATH:=herdr}"
HERDR="$HERDR_BIN_PATH"
PANE_ID="${HERDR_PANE_ID:-}"

# Plugin root: herdr provides it for plugin commands; fall back to this
# script's location so direct invocation also works.
: "${HERDR_PLUGIN_ROOT:=$(cd "$(dirname "$0")/.." && pwd)}"

in_herdr() { [ "${HERDR_ENV:-}" = "1" ]; }

# Seed the zcode agent-detection override into herdr's config dir (once).
# herdr loads local overrides from $HERDR_PLUGIN_CONFIG_DIR/agent-detection/.
ensure_agent_detection() {
  if ! in_herdr; then return 0; fi
  src="${HERDR_PLUGIN_ROOT}/config/agent-detection/zcode.toml"
  if [ -n "${HERDR_PLUGIN_CONFIG_DIR:-}" ] && [ -f "$src" ]; then
    dest_dir="${HERDR_PLUGIN_CONFIG_DIR}/agent-detection"
    mkdir -p "$dest_dir"
    dest="${dest_dir}/zcode.toml"
    if [ ! -f "$dest" ] || ! cmp -s "$src" "$dest"; then
      cp "$src" "$dest"
    fi
  fi
}

# Claim this pane's agent label so herdr shows the agent even between turns.
# `pane report-agent` is the claiming call (report-metadata is display-only and
# takes no --agent in herdr 0.9.x); state starts idle, screen detection and the
# status hook keep it fresh afterwards.
claim_agent() {
  in_herdr || return 0
  [ -n "$PANE_ID" ] || return 0
  "$HERDR" pane report-agent "$PANE_ID" \
    --source zcode-integration --agent zcode --state idle \
    --seq "$(date +%s)" >/dev/null 2>&1 || true
}
