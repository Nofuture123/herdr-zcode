#!/bin/sh
# Install the herdr integration pieces that outlive a single pane:
#  - the zcode agent-detection override (screen-based state detection)
#  - a pane agent claim check + CLI presence doctor
# Safe to run repeatedly.
. "$(dirname "$0")/common.sh"

ensure_agent_detection && echo "agent-detection override: seeded"

if command -v zcode >/dev/null 2>&1; then
  echo "zcode CLI: $(zcode --version 2>/dev/null || echo present)"
else
  echo "zcode CLI: NOT FOUND — install the official CLI (zai-org/ZCode) first" >&2
  exit 1
fi

if in_herdr && [ -n "$PANE_ID" ]; then
  claim_agent && echo "pane agent claimed: $PANE_ID"
else
  echo "note: run inside a herdr pane (or open one via the plugin) for the agent claim"
fi
echo "done."
