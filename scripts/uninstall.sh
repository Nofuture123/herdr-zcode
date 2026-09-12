#!/bin/sh
# Full cleanup for zcode-bridge. Run BEFORE `herdr plugin unlink/uninstall`.
#   sh scripts/uninstall.sh            # keep task evidence (results/)
#   sh scripts/uninstall.sh --purge    # also delete results/owners/ledgers/requests
set -u
BASE="$HOME/.local/share/qonnwolf-zcode-bridge"
PURGE="${1:-}"
# 1) running panes + processes
for P in $(herdr pane list 2>/dev/null | python3 -c "import sys,json; [print(p['pane_id']) for p in json.load(sys.stdin)['result']['panes'] if (p.get('label') or '').startswith('zcode')]" 2>/dev/null); do
  herdr pane close "$P" && echo "closed pane $P"
done
pkill -f 'executor_repl.py|executor_chat.py' 2>/dev/null && echo "stopped executor processes"
# 2) MCP injections
sh "$(dirname "$0")/uninject-mcp.sh" all 2>/dev/null || true
# 3) shared skill + links
rm -rf "$HOME/.agents/skills/zcode-bridge" && echo "removed shared skill"
rm -f "$HOME/.pi/agent/skills/zcode-bridge" "$HOME/.claude/skills/zcode-bridge" 2>/dev/null
# 4) PATH symlinks
rm -f /opt/homebrew/bin/zcodecli /opt/homebrew/bin/nar 2>/dev/null && echo "removed PATH symlinks"
# 5) plugin registration (herdr-managed checkout is removed by `herdr plugin uninstall`)
herdr plugin list 2>/dev/null | grep -q "zcode" \
  && herdr plugin unlink zcode 2>/dev/null && echo "plugin unlinked (github installs: herdr plugin uninstall zcode)"
# 6) runtime
if [ "$PURGE" = "--purge" ]; then
  rm -rf "$BASE" && echo "runtime + ALL task data purged: $BASE"
else
  rm -rf "$BASE/venv" "$BASE/venv.new" "$BASE/venv.old" "$BASE/bin" "$BASE/env.sh"
  echo "runtime removed; task evidence kept in $BASE/results (re-run with --purge to delete)"
fi
echo "done."
