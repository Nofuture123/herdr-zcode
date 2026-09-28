#!/bin/sh
# Add a /quit slash command to the official zcode TUI (zai-org/ZCode).
#
# Upstream 0.16.x has no /quit — leaving the TUI requires Ctrl-C twice. This
# patch wires /quit through the exact same exit path: the TUI's submit choke
# point (useSubmitValue) aborts any active turn and calls onExit(0), which
# unwinds runTui -> promptHandler.close() (session save, telemetry shutdown,
# provider registry dispose) and exits cleanly.
#
# Four insertions, all idempotent, each guarded by a unique anchor that fails
# loudly if upstream moves:
#   1. agent/zcode.cjs            builtin slash-command registry (autocomplete,
#                                 /help listing, reserved-name protection)
#   2. agent/zcode.cjs            `zcode --help` slash-command list
#   3. tui/dist/index.js          /quit intercept in useSubmitValue (the real,
#                                 self-contained bundle entry)
#   4. tui/dist/index.js          thread onExit into useSubmitValue
#   + optional: the same two edits in the modular dist/app-submit-controller.js
#     and dist/app.js (only loaded in dev setups; skipped when absent)
#
# Re-run after any zcode reinstall/update (new releases/<ver> are unpatched).
# ZCODE_RUNTIME=<dir> overrides the target (default: the current release).
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

resolve_runtime() {
  if [ -n "${ZCODE_RUNTIME:-}" ]; then
    printf '%s' "$ZCODE_RUNTIME"
    return 0
  fi
  # ~/.local/bin/zcode is `exec node <runtime>/bin/zcode.mjs "$@"`
  wrapper=${ZCODE_BIN:-$HOME/.local/bin/zcode}
  if [ -f "$wrapper" ]; then
    rt=$(sed -n 's|^exec node "\(.*\)/bin/zcode\.mjs".*$|\1|p' "$wrapper" | head -1)
    if [ -n "$rt" ]; then
      printf '%s' "$rt"
      return 0
    fi
  fi
  printf '%s' "$HOME/.zcode/runtime/current"
}

RT=$(resolve_runtime)
AGENT_CJS="$RT/agent/zcode.cjs"
TUI_DIST="$RT/agent/node_modules/@zcode/tui/dist"
[ -f "$AGENT_CJS" ] || { echo "zcode runtime not found: $RT" >&2; exit 1; }
[ -d "$TUI_DIST" ] || { echo "TUI dist not found: $TUI_DIST" >&2; exit 1; }
echo "runtime: $RT"

python3 - "$AGENT_CJS" "$TUI_DIST" <<'PY'
import os, shutil, sys, time

agent_cjs, tui_dist = sys.argv[1], sys.argv[2]
MARKER = "zcode-quit-patch"
stamp = time.strftime("%Y%m%d-%H%M%S")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def save(path, text):
    shutil.copy2(path, f"{path}.bak-quit-{stamp}")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def replace_once(text, old, new, path, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{label}: anchor found {count} times (need exactly 1) in {path} — "
            "upstream moved, patch needs a manual rebase"
        )
    return text.replace(old, new)


def already(text):
    return MARKER in text


# 1. builtin registry -> autocomplete, /help listing, reserved-name protection
# The quit entry is inserted right after the goal entry; the anchor has two
# shapes across versions: goal mid-array (3.14.3+ has /workflow after it) or
# goal last (3.14.0).
QUIT_ENTRY = '''      {
        details: ["Exits the interactive TUI; same as pressing Ctrl-C twice."],
        name: "quit",
        summary: "Quit the TUI.",
        usage: "/quit"
      },'''
GOAL_USAGE = '        usage: "/goal [pause|resume|clear|replace <objective>|<objective>]"\n'
path = agent_cjs
text = load(path)
if already(text) or 'name: "quit"' in text:
    print("1. registry: already patched")
else:
    for tail, replacement in (
        ('      },', GOAL_USAGE + '      },\n' + QUIT_ENTRY),
        ('      }\n    ];', GOAL_USAGE + '      },\n' + QUIT_ENTRY[:-1] + '\n    ];'),
    ):
        anchor = GOAL_USAGE + tail
        if text.count(anchor) == 1:
            text = text.replace(anchor, replacement)
            break
    else:
        raise SystemExit(
            "1. registry: goal-entry anchor not found in " + path + " — "
            "upstream moved, patch needs a manual rebase"
        )
    save(path, text)
    print("1. registry: patched")

# 2. `zcode --help` slash command list
text = load(path)
if already(text) or "  /quit                 Quit the TUI" in text:
    print("2. help list: already patched")
else:
    anchor = "  /goal [action]        Show or set the current session goal\n"
    text = replace_once(
        text, anchor,
        anchor + "  /quit                 Quit the TUI\n",
        path, "2. help list")
    save(path, text)
    print("2. help list: patched")

# 3+4. the TUI's real entry is dist/index.js (a self-contained bundle, per
# package.json exports) — the modular dist/*.js files are only loaded in dev
# setups; patch them too when present.
quit_intercept_bundle = '''      if (!text) {
        input2.setStatus(input2.emptyPromptStatus);
        return;
      }
      /* zcode-quit-patch: /quit takes the ctrl-c-double exit path */
      if (input2.onExit && (text === "/quit" || text.startsWith("/quit "))) {
        input2.turnRef.current?.abort();
        input2.onExit(0);
        return;
      }'''
quit_wiring_bundle = '''    setStatus,
    setStatusDetails,
    turnRef: abortControllerRef,
    onExit
  });'''

path = f"{tui_dist}/index.js"
text = load(path)
if already(text):
    print("3. bundle intercept+wiring: already patched")
else:
    text = replace_once(
        text,
        '''      if (!text) {
        input2.setStatus(input2.emptyPromptStatus);
        return;
      }''',
        quit_intercept_bundle, path, "3. bundle intercept")
    text = replace_once(
        text,
        '''    setStatus,
    setStatusDetails,
    turnRef: abortControllerRef
  });''',
        quit_wiring_bundle, path, "4. bundle onExit wiring")
    save(path, text)
    print("3+4. bundle intercept+wiring: patched")

for extra in ("app-submit-controller.js", "app.js"):
    path = f"{tui_dist}/{extra}"
    if not os.path.exists(path):
        print(f"  (optional) {extra}: absent, skipped")
        continue
    text = load(path)
    already_optional = already(text) or (
        extra == "app-submit-controller.js" and "zcode-quit-patch" in text
    ) or (
        extra == "app.js" and "turnRef: abortControllerRef,\n        onExit," in text
    )
    if already_optional:
        print(f"  (optional) {extra}: already patched")
        continue
    if extra == "app-submit-controller.js":
        text = replace_once(
            text,
            '''        if (!text) {
            input.setStatus(input.emptyPromptStatus);
            return;
        }''',
            '''        if (!text) {
            input.setStatus(input.emptyPromptStatus);
            return;
        }
        /* zcode-quit-patch: /quit takes the ctrl-c-double exit path */
        if (input.onExit && (text === "/quit" || text.startsWith("/quit "))) {
            input.turnRef.current?.abort();
            input.onExit(0);
            return;
        }''',
            path, f"  (optional) {extra}")
    else:
        text = replace_once(
            text,
            '''        setStatus,
        setStatusDetails,
        turnRef: abortControllerRef,
    });''',
            '''        setStatus,
        setStatusDetails,
        turnRef: abortControllerRef,
        onExit,
    });''',
            path, f"  (optional) {extra}")
    save(path, text)
    print(f"  (optional) {extra}: patched")

print("done.")
PY

node --check "$AGENT_CJS" && echo "zcode.cjs syntax ok"
node --check "$TUI_DIST/index.js" && echo "tui bundle syntax ok"
