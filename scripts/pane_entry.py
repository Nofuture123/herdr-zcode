#!/usr/bin/env python3
"""Executor pane entry (cross-platform): bootstrap, suppress tty echo of
machine JSON where possible, run the reception executor."""
import json, os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def main():
    # No QAB_DEFAULT_WORKSPACE seeding: the executor's default workspace is the
    # pane's own cwd (wherever this pane was opened), never a hardcoded demo dir.
    os.environ.setdefault("QAB_DEFAULT_MODE", "yolo")
    os.environ.setdefault("QAB_DEFAULT_POLICY", "allow")
    bootstrap = os.path.join(HERE, "ensure_bridge.py")
    if not os.path.exists(os.path.join(
            os.path.expanduser("~/.local/share/herdr-zcode"), "env.sh")):
        r = subprocess.run([sys.executable, bootstrap])
        if r.returncode != 0:
            sys.exit(r.returncode)
    if sys.stdin.isatty():
        try:
            import termios
            attrs = termios.tcgetattr(sys.stdin)
            attrs[3] &= ~termios.ECHO          # machines send JSON; no echo
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, attrs)
        except Exception:
            pass                                # Windows console: skip
    # NAR auto-discovers the zcode agent via ZCODE_BIN; herdr spawns panes with
    # a minimal env, so re-inject what bootstrap recorded.
    try:
        saved = json.load(open(os.path.join(
            os.path.expanduser("~/.local/share/herdr-zcode"), "env.json")))
        for env_key, saved_key in (("ZCODE_BIN", "zcode_bin"), ("NODE_BIN", "node")):
            if saved.get(saved_key) and not os.environ.get(env_key):
                os.environ[env_key] = saved[saved_key]
        # NAR resolves node via PATH lookup; herdr spawns panes with a minimal
        # PATH, so prepend the toolchain dirs (mirrors the old env.sh export).
        node_dir = os.path.dirname(saved.get("node")) if saved.get("node") else ""
        prepend = [d for d in (node_dir, "/opt/homebrew/bin", "/usr/local/bin") if d]
        sep = os.pathsep
        cur = os.environ.get("PATH", "")
        parts = cur.split(sep) if cur else []
        missing = [d for d in prepend if d not in parts]
        if missing:
            os.environ["PATH"] = sep.join(missing) + sep + cur
        # ZCode.app auto-updates relocate the bundled provider config (now
        # Resources/config/provider/zcode-builtin.json) while the spawned CLI
        # resolves it next to the .cjs and dies with 无法定位 CLI ZCode
        # Built-in Provider Config → ProcessDied on every submit. Export the
        # real file so NAR's app-server workers inherit it.
        if not os.environ.get("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE") and saved.get("zcode_bin"):
            zres = os.path.dirname(os.path.dirname(saved["zcode_bin"]))
            zdir = os.path.dirname(saved["zcode_bin"])
            for cand in (os.path.join(zres, "config", "provider", "zcode-builtin.json"),
                         os.path.join(zdir, "provider", "zcode-builtin.json")):
                if os.path.isfile(cand):
                    os.environ["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] = cand
                    break
    except Exception:
        pass
    script = os.path.join(HERE, "executor_repl.py")
    if os.name != "nt" and hasattr(os, "execv"):
        return os.execv(sys.executable, [sys.executable, script])
    # Windows os.execv spawns a child and EXITS the parent; the ConPTY sees its
    # client process exit and tears down the pty, killing the freshly spawned
    # executor with it. Run the executor in-process so the pane lives exactly
    # as long as it does.
    sys.argv = [script]
    import runpy
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        return e.code or 0
    return 0
    return subprocess.call([sys.executable, script])

if __name__ == "__main__":
    sys.exit(main() or 0)
