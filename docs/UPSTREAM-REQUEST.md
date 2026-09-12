# Upstream feature request draft (for herdr.dev)

## Title
Add ZCode (GLM coding agent, zcode.cjs app-server) as a built-in agent integration

## What ZCode is
Zhipu/Z.AI's official coding agent CLI, shipped inside the ZCode desktop app at
`/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs` (also runnable headless:
`zcode.cjs app-server`). Widely used with GLM-5.3 / GLM-5.3-Flash plans.

## What we already built and verified (working today, outside the integrations panel)
A community Herdr plugin (`qonnwolf.zcode-bridge`) runs a persistent executor pane that
delegates tasks to headless ZCode. It reports agent state over the same socket protocol the
pi/codex integrations use (protocol 20):

- `pane.report_agent` with `agent: "zcode"`, `source: "herdr:zcode"`,
  state ∈ {idle, working} — Herdr shows the pane as a first-class agent with real
  idle/working/done transitions (verified live, Herdr 0.8.2).
- We also emit `pane.report_agent_session` with `agent_session_id` (native `sess_*` id);
  the server answers ok but does not persist it for unknown sources — presumably an
  integration allowlist.

## Request
1. Add `zcode` to the integration catalog (installer + the settings → integrations panel),
   so session metadata persistence/restore covers ZCode panes the same way as pi/codex.
2. Reference implementation available: ~50 lines of Python (stdlib socket, fire-and-forget,
   0.5s timeout, queue-free single-writer) embedded in our executor:
   https://github.com/Nofuture123/qonnwolf-agent-bridge (scripts/executor_repl.py)

## State mapping
task submitted → working · task settled → idle (Herdr derives done for unseen completions ·
blocked: not emitted; the bridge enforces permissions by policy, not by prompting).
