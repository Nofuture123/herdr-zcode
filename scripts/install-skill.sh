#!/bin/sh
# Install the shared agent skill so ANY CLI agent discovers how to use the bridge.
# Source of truth: ~/.agents/skills/zcode-bridge. Per-CLI skill dirs get a link.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/skills/zcode-bridge/SKILL.md"
AGENTS_DIR="$HOME/.agents/skills/zcode-bridge"
DEST="$AGENTS_DIR/SKILL.md"

mkdir -p "$AGENTS_DIR"
if [ -f "$DEST" ] && ! cmp -s "$DEST" "$SRC"; then
  cp "$DEST" "$DEST.bak-$(date +%Y%m%d-%H%M%S)"
  echo "existing skill backed up"
fi
cp "$SRC" "$DEST"
echo "installed: $DEST"

# pi: ~/.pi/agent/skills holds relative symlinks into ~/.agents/skills
PI_SKILLS="$HOME/.pi/agent/skills"
if [ -d "$PI_SKILLS" ] && [ ! -e "$PI_SKILLS/zcode-bridge" ]; then
  ln -s "../../../.agents/skills/zcode-bridge" "$PI_SKILLS/zcode-bridge" \
    && echo "linked: $PI_SKILLS/zcode-bridge (pi)"
fi

# Claude Code: personal skills dir (symlink works)
CL_SKILLS="$HOME/.claude/skills"
if [ -d "$CL_SKILLS" ] && [ ! -e "$CL_SKILLS/zcode-bridge" ]; then
  ln -s "../../.agents/skills/zcode-bridge" "$CL_SKILLS/zcode-bridge" \
    && echo "linked: $CL_SKILLS/zcode-bridge (claude)"
fi
