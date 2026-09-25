#!/bin/sh
# Screen-based status watcher for a zcode TUI pane.
#
# herdr's built-in screen detection only covers its 21 bundled agents, and the
# zcode CLI's hook events do not fire in TUI sessions yet, so this watcher
# provides the status feed instead: it polls the pane's visible screen, applies
# the same two patterns as config/agent-detection/zcode.toml, and reports the
# state via `pane report-agent` on every transition. Exits when the pane dies.

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

SEQ_FILE="${TMPDIR:-/tmp}/herdr-zcode-seq-${PANE_ID}"
next_seq() {
  s=$(cat "$SEQ_FILE" 2>/dev/null | tr -dc '0-9')
  s=$(( ${s:-$(date +%s)} + 1 ))
  printf '%s' "$s" > "$SEQ_FILE"
  printf '%s' "$s"
}

report() {
  "$HERDR" pane report-agent "$PANE_ID" \
    --source zcode-integration --agent zcode --state "$1" --seq "$(next_seq)" >/dev/null 2>&1
}

last=""
dead_count=0
while :; do
  screen=$("$HERDR" pane read "$PANE_ID" --source visible --lines 14 2>/dev/null)
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
