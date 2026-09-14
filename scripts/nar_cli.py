#!/usr/bin/env python3
"""Cross-platform nar passthrough used by plugin actions: ensure bootstrap,
then exec the pinned NAR CLI (doctor / list / inspect / …)."""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.expanduser("~/.local/share/qonnwolf-zcode-bridge")

def main():
    r = subprocess.run([sys.executable, os.path.join(HERE, "ensure_bridge.py")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr or r.stdout)
        return r.returncode
    exe = os.path.join(BASE, "venv", "Scripts", "nar.exe") if os.name == "nt" \
        else os.path.join(BASE, "venv", "bin", "nar")
    env = dict(os.environ)
    try:
        env["ZCODE_BIN"] = json.load(open(os.path.join(BASE, "env.json")))["zcode_bin"] or env.get("ZCODE_BIN", "")
    except Exception:
        pass
    args = sys.argv[1:] or ["doctor"]
    if hasattr(os, "execv"):
        return os.execv(exe, [exe] + args)
    return subprocess.call([exe] + args, env=env)

if __name__ == "__main__":
    sys.exit(main() or 0)
