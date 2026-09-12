#!/bin/sh
# Action: split the focused pane (wide -> right, tall -> down) and start the
# ZCode TUI in the new pane without moving the user's focus.
set -eu

. "$(dirname "$0")/lib.sh"

HERDR=$(plugin_herdr)
ROOT=$(plugin_root)

if [ -z "${HERDR_PANE_ID:-}" ]; then
  echo "herdr-zcode-plugin: no pane context; invoke this action from a pane." >&2
  exit 1
fi

# Herdr's own rule: split a wide pane to the right, a tall one down. When the
# geometry cannot be parsed (no python3), fall back to right.
direction=right
if size=$(pane_rect_size "$("$HERDR" pane edges --pane "$HERDR_PANE_ID")" "$HERDR_PANE_ID"); then
  width=${size%% *}
  height=${size##* }
  if [ "${width:-0}" -ge "${height:-0}" ]; then
    direction=right
  else
    direction=down
  fi
fi

split_json=$("$HERDR" pane split --pane "$HERDR_PANE_ID" --direction "$direction" --cwd "${OPEN_CWD:-$(pwd)}" --no-focus)
if ! pane_id=$(split_pane_id "$split_json"); then
  pane_id=$(printf '%s' "$split_json" | sed -n 's/.*"pane_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
fi
if [ -z "$pane_id" ]; then
  echo "herdr-zcode-plugin: could not read the new pane id from the split response:" >&2
  printf '%s\n' "$split_json" >&2
  exit 1
fi

"$HERDR" pane rename "$pane_id" zcode >/dev/null 2>&1 || :
"$HERDR" pane run "$pane_id" "\"$ROOT/bin/launch-zcode.sh\""
