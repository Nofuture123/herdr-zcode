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
Build-time prereq checks: Herdr, Node >= 22, python3, git, ZCode executor.

## Use
- `zcodecli`            — start a ZCode session in the current pane (one pane = one session)
- `zcodecli chat-open`  — spawn a dedicated session pane
- `zcodecli open`       — reception queue pane (serial, shared)
- `zcodecli send|result|read|cancel` — programmatic delegation
- Or just tell your agent: 「这票给 zcode 执行」

## Uninstall
Action **"ZCode: cleanup before uninstall"**, then `herdr plugin uninstall zcode`.

## Docs
`docs/VERIFICATION.md` (evidence chain incl. 13 audit rounds) · `docs/REMEDIATION-PLAN.md` ·
`docs/ORCHESTRATOR-GUIDE.md` · `docs/UPSTREAM-REQUEST.md` · `PUBLISHING.md` (herdr.dev release checklist)
