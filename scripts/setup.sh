#!/bin/sh
# Install the herdr integration pieces that outlive a single pane:
#  - the zcode agent-detection override (screen-based state detection)
#  - the agent watchdog (auto-claims any pane running the zcode CLI and
#    feeds its state, including panes opened outside the plugin)
#  - a CLI presence doctor
# Safe to run repeatedly. Claiming is owned exclusively by the watchdog so a
# pane never keeps a stale agent label after its zcode process exits.
. "$(dirname "$0")/common.sh"

ensure_agent_detection && echo "agent-detection override: seeded"

if command -v zcode >/dev/null 2>&1; then
  echo "zcode CLI: $(zcode --version 2>/dev/null || echo present)"
else
  echo "zcode CLI: NOT FOUND — install the official CLI (zai-org/ZCode) first" >&2
  exit 1
fi

ensure_daemon && echo "agent watchdog: running"
echo "done."
