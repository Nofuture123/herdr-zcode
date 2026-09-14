#!/usr/bin/env python3
"""Executor pane entry (cross-platform): bootstrap, suppress tty echo of
machine JSON where possible, run the reception executor."""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def main():
    os.environ.setdefault("QAB_DEFAULT_WORKSPACE",
                          os.path.expanduser("~/projects/qab-demo"))
    os.environ.setdefault("QAB_DEFAULT_MODE", "yolo")
    os.environ.setdefault("QAB_DEFAULT_POLICY", "allow")
    bootstrap = os.path.join(HERE, "ensure_bridge.py")
    if not os.path.exists(os.path.join(
            os.path.expanduser("~/.local/share/qonnwolf-zcode-bridge"), "env.sh")):
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
    script = os.path.join(HERE, "executor_repl.py")
    if hasattr(os, "execv"):
        return os.execv(sys.executable, [sys.executable, script])
    return subprocess.call([sys.executable, script])

if __name__ == "__main__":
    sys.exit(main() or 0)
