#!/bin/sh
# ZCode pane entrypoint: launch the ZCode TUI in this Herdr pane.
set -eu

. "$(dirname "$0")/lib.sh"

# Run ZCode in the workspace (or focused pane) directory Herdr reports.
cd_context_cwd

if command -v zcode >/dev/null 2>&1; then
  exec zcode
fi

# macOS app-bundle fallback. Point ZCODE_CJS at the zcode.cjs entrypoint when
# ZCode lives somewhere else.
_zcode_cjs="${ZCODE_BIN:-${ZCODE_CJS:-/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs}}"
if [ -f "$_zcode_cjs" ] && command -v node >/dev/null 2>&1; then
  exec node "$_zcode_cjs"
fi

echo "herdr-zcode-plugin: ZCode CLI not found." >&2
echo "Make sure 'zcode' is on PATH, or set ZCODE_CJS to the zcode.cjs entrypoint." >&2
exit 127
