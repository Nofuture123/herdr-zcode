# herdr-zcode

ZCode in Herdr, two ways:

1. **TUI** — open the native ZCode interactive agent in any Herdr pane (`Open ZCode here` action).
2. **Bridge** — any CLI agent (Codex / Claude Code / pi / …) delegates coding tasks to the native
   ZCode executor (GLM-5.3-Flash) over the Herdr protocol. Pure transport: the bridge reports
   facts (status / verify_ok / out_of_scope); acceptance and rework discipline belong to the master
   agent (see `skills/zcode-bridge/SKILL.md`). Based on
   [native-agent-router](https://github.com/BerineYang/native-agent-router), pinned to a verified commit.

## Install
```bash
herdr plugin install Nofuture123/herdr-zcode
```
Build-time prereq checks: Herdr, Node >= 22, python3, git, ZCode executor, and that ZCode is
**logged in** (`~/.zcode/v2/credentials.json` must contain OAuth tokens — if not, run
`zcode login` first; the installer fails with these exact instructions).

**Windows:** supported as of v0.6.0 (same prereq checks; the ZCode executor is
resolved from `ZCODE_BIN`, PATH, the macOS app bundle, or
`%LOCALAPPDATA%\Programs\ZCode`). MCP inject/skill actions remain macOS/Linux
only (bash utilities). File locking degrades gracefully on Windows
(ledger writes stay atomic).

## ⚠️ Permissions (read before installing)
The delegation executor runs ZCode with **full user permissions by default** (yolo + auto-approve,
owner-authorized): tasks can read/write/execute anything your user can, including outside the
workspace. Herdr plugins are not sandboxed. Restrict per-task (`"mode":"plan"/"edit"`,
`"policy":"deny"`) or globally via `QAB_DEFAULT_MODE`/`QAB_DEFAULT_POLICY` env on the executor pane.
The installer also installs real launcher scripts for `zcodecli` and `nar`
into a PATH dir (Homebrew bin or `~/.local/bin`) — plain files referencing the
runtime by its real path, no symlinks.

## Use
- `zcodecli`            — start a ZCode session in the current pane (one pane = one session)
- `zcodecli chat-open`  — spawn a dedicated session pane
- `zcodecli open`       — reception queue pane (serial, shared)
- `zcodecli send|result|read|cancel` — programmatic delegation
- Or just tell your agent: 「这票给 zcode 执行」

Executor panes stream ZCode's live activity into the pane (assistant text, tool
calls `▸ …`, tool results `✓/✗`) as it works — set `QAB_EXEC_QUIET=1` on the
pane to mute. Note the executor locks its workspace per task (serial); parallel
tickets need distinct workspace paths (worktrees or directory aliases).

## Steering a running task
`/steer <new instruction>` (or `zcodecli steer <text>`) redirects the running
turn: the current ticket is cancelled and the new instruction relaunches in the
**same native ZCode session** once it reaches terminal state — the model keeps
all prior context. Relaunches retry through the router's workspace-lock release
window, so steering never collides with the serial lock.

## Uninstall
Action **"ZCode: cleanup before uninstall"**, then `herdr plugin uninstall zcode`.

## Docs
`docs/VERIFICATION.md` (evidence chain incl. 13 audit rounds) · `docs/REMEDIATION-PLAN.md` ·
`docs/ORCHESTRATOR-GUIDE.md` · `docs/UPSTREAM-REQUEST.md` · `PUBLISHING.md` (herdr.dev release checklist)
