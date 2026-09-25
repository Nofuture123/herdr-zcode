#!/bin/sh
# Launch the official ZCode TUI inside a herdr plugin pane (real PTY).
# Usage: launch.sh [task]
. "$(dirname "$0")/common.sh"

ensure_agent_detection
claim_agent

# Status feed: herdr's screen detection covers only its bundled agents and the
# zcode TUI does not fire hook events yet, so run the screen watcher alongside
# the TUI (it exits on pane death). The watcher must start BEFORE exec so it
# survives the shell being replaced by the zcode process.
if [ -x "$(dirname "$0")/watch-status.sh" ]; then
  "$(dirname "$0")/watch-status.sh" &
fi

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
