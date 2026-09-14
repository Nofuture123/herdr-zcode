#!/usr/bin/env python3
"""Executor pane entry (cross-platform): bootstrap, suppress tty echo of
machine JSON where possible, run the reception executor."""
import json, os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def main():
    os.environ.setdefault("QAB_DEFAULT_WORKSPACE",
                          os.path.expanduser("~/projects/qab-demo"))
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
        cur = os.environ.get("PATH", "")
        missing = [d for d in prepend if cur.split(":") and d not in cur.split(":")]
        if missing:
            os.environ["PATH"] = ":".join(missing) + ":" + cur
    except Exception:
        pass
    script = os.path.join(HERE, "executor_repl.py")
    if hasattr(os, "execv"):
        return os.execv(sys.executable, [sys.executable, script])
    return subprocess.call([sys.executable, script])

if __name__ == "__main__":
    sys.exit(main() or 0)
