#!/bin/sh
# Launch the official ZCode TUI inside a herdr plugin pane (real PTY).
# Usage: launch.sh [task]
. "$(dirname "$0")/common.sh"

ensure_agent_detection
ensure_daemon

# Claiming and status feeding are owned by the agent watchdog (it scans every
# pane, so plain `zcode ...` panes opened outside this entrypoint behave the
# same). Just exec the TUI.
mode="${1:-task}"
case "$mode" in
  task)
    if command -v zcode >/dev/null 2>&1; then
      exec zcode
    fi
    echo "zcode CLI not found on PATH." >&2
    echo "Install the official CLI (zai-org/ZCode): build the release and run its install.sh," >&2
    echo "then authenticate once with: zcode login bigmodel" >&2
    exit 127
    ;;
  *)
    echo "unknown launch mode: $mode" >&2
    exit 2
    ;;
esac
