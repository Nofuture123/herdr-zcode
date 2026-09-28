#!/bin/sh
# Screen-based status watcher for a zcode TUI pane.
#
# herdr 0.9.x cannot detect the zcode agent natively (its Agent table is a
# closed enum), so state comes from report-agent. This watcher is spawned by
# agent_watchdog.py for each claimed pane: it polls the pane's visible
# screen, applies the same two patterns as config/agent-detection/zcode.toml,
# and reports idle/working on every transition via `pane report-agent`
# (source zcode-integration). It exits when the pane dies.

HERDR="${HERDR_BIN_PATH:-herdr}"
for _dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  case ":$PATH:" in
    *":$_dir:"*) ;;
    *) PATH="$_dir:$PATH" ;;
  esac
done
export PATH

PANE_ID="${HERDR_PANE_ID:-}"
[ -n "$PANE_ID" ] || exit 0
[ "${HERDR_ENV:-}" = "1" ] || exit 0

RUN_DIR="${ZCODE_WATCHDOG_RUN_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/herdr-zcode-integration}"
# herdr rejects non-increasing --seq per source, and the daemon also writes
# this counter — increment through the watchdog's flock-protected counter so
# a release can never race a watcher report to the same sequence number.
WATCHDOG_PY="$(cd "$(dirname "$0")" && pwd)/agent_watchdog.py"
next_seq() {
  python3 "$WATCHDOG_PY" next-seq "$PANE_ID" 2>/dev/null || date +%s
}

report() {
  "$HERDR" pane report-agent "$PANE_ID" \
    --source zcode-integration --agent zcode --state "$1" --seq "$(next_seq)" >/dev/null 2>&1
}

last=""
dead_count=0
while :; do
  screen=$("$HERDR" pane read "$PANE_ID" --source visible --lines 24 2>/dev/null)
  if [ -z "$screen" ]; then
    dead_count=$((dead_count + 1))
    [ "$dead_count" -ge 3 ] && exit 0
    sleep 1
    continue
  fi
  dead_count=0
  case "$screen" in
    *"esc to interrupt"*) st=working ;;
    *"GLM-5.3"*|*"account:bigmodel"*|*"Type a prompt"*) st=idle ;;
    *) st="" ;;
  esac
  if [ -n "$st" ] && [ "$st" != "$last" ]; then
    last=$st
    report "$st"
  fi
  sleep 1
done
