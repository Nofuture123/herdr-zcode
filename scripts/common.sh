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

# Monotonic per-pane report sequence shared with agent_watchdog.py and
# watch-status.sh — herdr rejects non-increasing --seq per source, so every
# writer increments through the watchdog's flock-protected counter.
next_seq() {
  [ -n "$1" ] || return 0
  python3 "${HERDR_PLUGIN_ROOT}/scripts/agent_watchdog.py" next-seq "$1" 2>/dev/null || date +%s
}

# Seed the zcode agent-detection override into herdr's platform config dir
# (config_dir()/agent-detection/<agent>.toml — the location herdr's manifest
# engine documents for local overrides). In herdr 0.9.x the Agent enum is a
# closed table without zcode, so the manifest is inert there; it activates
# automatically once herdr learns the zcode agent id.
ensure_agent_detection() {
  src="${HERDR_PLUGIN_ROOT}/config/agent-detection/zcode.toml"
  [ -f "$src" ] || return 0
  if [ -n "${XDG_CONFIG_HOME:-}" ]; then
    base="$XDG_CONFIG_HOME/herdr"
  else
    base="$HOME/.config/herdr"
  fi
  dest_dir="$base/agent-detection"
  mkdir -p "$dest_dir" 2>/dev/null || return 0
  dest="$dest_dir/zcode.toml"
  if [ ! -f "$dest" ] || ! cmp -s "$src" "$dest"; then
    cp "$src" "$dest"
  fi
}

# Start the agent watchdog if it is not already running (singleton via the
# daemon's lock file). The watchdog claims panes that run the zcode CLI in
# the foreground and feeds their state, including panes opened outside the
# plugin with a plain `zcode ...` command.
ensure_daemon() {
  python3 "${HERDR_PLUGIN_ROOT}/scripts/agent_watchdog.py" ensure >/dev/null 2>&1 || true
}

# Claim this pane's agent label so herdr shows the agent immediately, before
# the watchdog's next scan. `pane report-agent` is the claiming call
# (report-metadata is display-only and takes no --agent in herdr 0.9.x).
claim_agent() {
  in_herdr || return 0
  [ -n "$PANE_ID" ] || return 0
  "$HERDR" pane report-agent "$PANE_ID" \
    --source zcode-integration --agent zcode --state idle \
    --seq "$(next_seq "$PANE_ID")" >/dev/null 2>&1 || true
}
