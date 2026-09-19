---
name: zcode-bridge
description: "Delegate coding tasks to the native ZCode executor (GLM-5.3-Flash) through the local Herdr pane bridge. Use when the user says things like '这票给zcode执行', '交给zcode', '委派给zcode', or when you need execution capacity for a well-scoped task with automatable acceptance. Do not use for architecture decisions or tasks without verifiable acceptance."
---

# zcode-bridge (zcodecli)

ZCode runs as a persistent executor in a Herdr pane (label `zcode-bridge`). ALL communication
goes through the Herdr protocol — every CLI (Codex, Claude Code, pi, ...) uses the same commands.

`zcodecli` — the bridge client at `~/.local/share/herdr-zcode/bin/zcodecli`:

```bash
zcodecli open                    # open the executor pane (idempotent; returns pane id)
                                 # lands in the caller's workspace ($HERDR_WORKSPACE_ID),
                                 # or pass --workspace <ID> / a path (= --cwd) explicitly
zcodecli send "<one line>"       # send a command line into the executor pane
zcodecli result [--timeout MS]   # print parsed result of the latest task (waits if needed)
zcodecli read [--lines N]        # read recent executor pane output
zcodecli list                    # all tasks with status (nar passthrough)
zcodecli inspect <task_id>       # full evidence: summary/diff/verify/usage (nar passthrough)
zcodecli cancel <task_id>        # request cancel; output says whether actually stopped
```

Equivalent raw herdr protocol (what zcodecli wraps):
```bash
herdr pane run  <pane_id> "<line>"     # send   (pane id resolves via label zcode-bridge)
herdr pane read <pane_id> --source visible
herdr pane wait-output <pane_id> --match "[zcodecli:result]" --timeout 300000
```

## Session-per-pane mode (parallel sessions)
For N independent sessions (parallel tickets/workspaces), open N chat panes — ONE native
ZCode session per pane, exactly like one agent per Herdr window:
```bash
zcodecli chat-open --label zcode-chat-1     # prints pane id, e.g. w73:p39
zcodecli --pane w73:p39 send "记住：这票是XXX"     # all turns stay in THIS pane's session
zcodecli --pane w73:p39 result                   # accept this pane's latest turn
zcodecli --pane w73:p39 send "/quit"             # end this session
```
Every pane is an independent herdr agent (own working/idle/done state, own native session).
Addressing is always via --pane; the label-less reception pane (zcode-bridge) remains the
serial queue for quick one-off delegations.

## Per-workspace visible windows (v0.9.2+)
A ticket normally streams inside the catch-all reception pane (zcode-bridge). To give a
workspace ITS OWN named, visible window (per-ticket visibility without Devin):
```bash
zcodecli open --workspace /path/to/worktree --placement tab --herdr-workspace w73
```
The pane retitles itself `zcode:<dir-name>` and registers a live heartbeat. Routing is
automatic from then on:
- `zcodecli send` run with cwd inside that workspace (or `--workspace <path>`) fast-paths
  the ticket into THAT pane; disk-pickup tickets for it land there too;
- the catch-all pane defers — a workspace with a live dedicated pane never gets its
  tickets claimed elsewhere; if the dedicated pane dies, the heartbeat goes stale and the
  catch-all self-heals (claims again);
- dedicated panes are strictly workspace-scoped (serial queue per workspace, same as the
  NAR workspace lock). Quick one-offs for OTHER workspaces still go to zcode-bridge.
NOTE: herdr runs pane entry commands with the pane's `--cwd` — the entry resolves its
scripts via HERDR_PLUGIN_ROOT, so `--cwd` is safe (pre-0.9.2 panes died within seconds).

## Lifecycle: open / close / crash behavior
- Open: `zcodecli open` (idempotent; spawns the executor pane, new pane id, same label).
- Close: `zcodecli close` (or `herdr pane close <pane_id>`). Effects:
  - an in-flight task loses its owner → NAR marks it orphaned/unknown → report it as
    INTERRUPTED to the user; never silently rerun it;
  - completed task history and all result files persist on disk (results/ + NAR store);
  - `zcodecli list` / `inspect` keep working while the pane is closed.
- Crash/restart: same as close — reconcile with `zcodecli list`, report honest states.

## What to send (executor stdin protocol, ONE line each)
1. Plain text — e.g. `你好` or `总结一下这个项目的结构` → FULL-ACCESS task (yolo mode, auto-approved).
   The executor runs with the owner's maximum permission: ZCode can read/write/execute anywhere
   the user can. Scope/verify are still recorded as acceptance evidence but do NOT gate writes.
2. Flags (PREFERRED for agents — no JSON quoting, no shell-expansion traps):
   `zcodecli send "do X" --verify "grep -qx 6 sum.txt" --mode edit --scope sum.txt --key k1 --timeout 300`
   Same spec as JSON, assembled client-side. `--verify` satisfies the edit/yolo
   fail-closed rule. Avoid `$(...)` inside --verify: it may be expanded by YOUR
   shell before reaching the executor — use self-contained commands.
3. JSON task — full control:
   `{"goal":"...","workspace":"/abs/path","mode":"edit","scope":["file.py"],"verify":"cmd","idempotency_key":"k1","timeout":600}`
   WORKSPACE IS AUTOMATIC: `zcodecli send` always carries the CALLER's current working directory
   as the task workspace — run it from your project dir and ZCode works there. Override with
   `zcodecli send --workspace /abs/path ...`. Only add explicit "workspace" in JSON to differ.
   - `mode` defaults to yolo (full access). Use "plan"/"edit" in JSON to restrict a specific task;
     set env QAB_DEFAULT_MODE/QAB_DEFAULT_POLICY on the executor pane to restrict globally.
   - `scope`: allowed relative paths. `verify`: real check command (a check is a permission too).
   - FAIL-CLOSED: a JSON task with mode edit/yolo and NO `verify` is REJECTED
     ("verify is required for yolo tasks"). Plain text (item 1) stays full-access by design.
   - `idempotency_key`: ALWAYS set one; resubmitting the same key never double-executes.
     (Exception: a `workspace_busy` failure means the task never started, so the key is
     released and the same key may be resubmitted immediately.)
4. `/continue <text>` — follow-up on the same native ZCode session (rework rounds).
5. `/steer <text>` (or `zcodecli steer <text>`) — change direction while a task
   is RUNNING: cancels the live ticket and relaunches with the new instruction
   in the SAME native session after it reaches terminal (context preserved).
   Use instead of cancel+resend; steering retries through the router's
   workspace-lock release window automatically.
6. `/status` `/list` `/inspect <id>` `/cancel <id>` — manage tasks.
   One task runs at a time. Completion is shown as a dim rule line and — for
   machines — ALWAYS lands in `~/.local/share/herdr-zcode/results/<task_id>.json`
   (`status`, `summary`, `summary_full`, `verify`, `changed_files`). Masters must read
   that file (or `zcodecli result`), never the pane: ready/accepted/summary/result/done
   markers are hidden unless `QAB_EXEC_MARKERS=1`; `[zcodecli:error]` stays visible.

## Parallelism: serial per workspace (v0.9.3: busy QUEUES, no longer fails)
The native-agent-router locks the workspace (abspath hash). TWO TICKETS ON THE SAME
DIRECTORY RUN SERIALLY — since v0.9.3 the second ticket QUEUES behind the lock
(receipt `queued (workspace lock)`, backoff 2→15s, window = min(ticket timeout, 1800s);
on window exhaustion it fails honestly with `workspace_lock_timeout` and the key is
released). The bridge also sweeps terminal-holder lock residue (a cross-process kill
can never release the submitter's in-memory lock — dead task = free workspace).
For parallel tickets give each its own path: a git worktree, or at least a distinct
directory alias (symlink) of the same project. Scope-disjoint files do NOT lift the
lock today.

## Keys, cancel, kill (v0.9.3 semantics)
- `--key` dedups ONE submission, not forever: while the first attempt is in flight a
  resend returns `duplicate`; once it goes terminal, resending the SAME key creates a
  NEW task. Use unique keys per ticket if you never want a rerun.
- `zcodecli cancel <request_id|task_id>` is offline and synchronous — no pane needed:
  a queued request gets a tombstone (executor skips it); a task gets a real cancel
  (the owning executor escalates a cross-process cancel to a kill; already-terminal
  answers instantly).
- `zcodecli kill <task_id>` stays the hard stop; it now also sweeps the workspace lock
  the dead owner left behind.
- `send` holds the line up to 90s when the ticket is queued, so you usually get the
  t-id directly; `result --request <rid>` always works meanwhile.

## Review & evidence conventions
- Review tickets that must RUN the tests: use `mode:"edit"` with
  `"verify":"python3 -m unittest discover -p test_*.py"` — verify is the permission to
  execute, and `verify_ok` becomes hard evidence. Plan mode BLOCKS Bash: a plan-mode
  reviewer cannot run the suite and will end its turn asking for approval instead of
  issuing a verdict. Use plan mode only for purely static reviews.
- Put `VERDICT: PASS` / `VERDICT: FAIL` on the **FIRST line** of the final message —
  NAR caps worker_summary and tail lines can be cut (`summary_full` recovers it).
- Make the workspace a **git repo** (`git init` if needed): non-git workspaces produce
  empty `changed_files`, so scope evidence is unavailable (the executor warns at submit).
- **Commit between tickets.** `changed_files` diffs against HEAD: an uncommitted
  previous ticket leaks into the next ticket's changed_files and poisons scope
  evidence. Master commits after accepting each ticket.
- `zcodecli result` exposes `summary_full` (the uncapped last assistant message, up to
  4k chars) in machine output and at `results/<task_id>.json:summary_full` when available.

## Acceptance rules (never trust "done")
`zcodecli result` returns status + summary + changed_files + verify output. Accept ONLY if:
status=succeeded AND verify exited 0 AND changed_files ⊆ scope AND summary matches intent.
Not accepted → `/continue <what failed and what to change>`; max 2 automatic rework rounds,
then report honestly to the user.

## Hard rules
- Never delegate: deletions outside scope, git push/merge/deploy, global config edits, purchases.
- Permission denials block by default; never bypass or widen scope mid-task.
- No secrets in goals, verify commands, or logs.
- `usage` tokens are model-reported counts, not billing; official-client quota perks for external
  drive are NOT confirmed.
