#!/bin/sh
# Open the executor pane (plugin action; no args needed).
set -u
HERDR="${HERDR_BIN_PATH:-herdr}"
exec "$HERDR" plugin pane open --plugin qonnwolf.zcode-bridge --entrypoint executor --placement tab
