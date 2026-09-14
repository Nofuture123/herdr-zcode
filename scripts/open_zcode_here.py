#!/usr/bin/env python3
"""Cross-platform 'Open ZCode here': split the focused pane (wide -> right,
tall -> down) and start the ZCode TUI in the new pane without stealing focus."""
import json, os, sys, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")

def herdr(args, timeout=30):
    p = subprocess.run([HERDR] + args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def jwalk(data, *keys):
    node = data
    for k in keys:
        try:
            node = node[k]
        except Exception:
            return None
    return node

def main():
    if not os.environ.get("HERDR_PANE_ID"):
        print("open-here: no pane context; invoke from a pane.", file=sys.stderr)
        return 1
    pid = os.environ["HERDR_PANE_ID"]
    direction = "right"
    try:
        rc, out, _ = herdr(["pane", "edges", "--pane", pid])
        edges = json.loads(out)
        for pane in edges["layout"]["panes"]:
            if pane.get("pane_id") == pid:
                r = pane["rect"]
                direction = "right" if r["width"] >= r["height"] else "down"
                break
    except Exception:
        pass
    rc, out, err = herdr(["pane", "split", "--pane", pid, "--direction", direction,
                          "--no-focus"])
    pane_id = None
    try:
        d = json.loads(out)
        pane_id = (jwalk(d, "result", "pane", "pane_id")
                   or jwalk(d, "pane", "pane_id") or d.get("pane_id"))
    except Exception:
        pass
    if not pane_id:
        m = re.search(r'"pane_id"\s*:\s*"([^"]+)"', out or "")
        pane_id = m.group(1) if m else None
    if not pane_id:
        print((err or out or "split failed"), file=sys.stderr); return 1
    herdr(["pane", "rename", pane_id, "zcode"])
    entry = os.path.join(HERE, "tui_entry.py")
    rc, out, err = herdr(["pane", "run", pane_id,
                          f'"{sys.executable}" "{entry}"'])
    return rc

if __name__ == "__main__":
    sys.exit(main() or 0)
