#!/bin/sh
# Entry point for the plugin-owned zcode-bridge pane. Must survive minimal PATH.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/.local/share/qonnwolf-zcode-bridge"
[ -f "$BASE/env.sh" ] || { PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH" sh "$HERE/ensure-bridge.sh" >/dev/null 2>&1 || { echo "bridge install failed" >&2; exit 1; }; }
. "$BASE/env.sh" 2>/dev/null || true
export ZCODE_BIN="${ZCODE_BIN:-/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs}"
[ -x "$BASE/bin/nar" ] || sh "$HERE/ensure-bridge.sh" >/dev/null 2>&1 || { echo "bridge install failed" >&2; exit 1; }
export QAB_DEFAULT_WORKSPACE="${QAB_DEFAULT_WORKSPACE:-$HOME/projects/qab-demo}"
export QAB_DEFAULT_MODE="${QAB_DEFAULT_MODE:-yolo}"
export QAB_DEFAULT_POLICY="${QAB_DEFAULT_POLICY:-allow}"
# Machine JSON typed by `pane run` would otherwise echo into the pane; the
# executor renders its own human-readable line instead. Restore echo on exit
# so the pane's shell stays usable.
if [ -t 0 ]; then stty -echo 2>/dev/null || true; fi
"${PY3:-/usr/bin/python3}" "$HERE/executor_repl.py"
rc=$?
[ -t 0 ] && stty echo 2>/dev/null || true
exit $rc
