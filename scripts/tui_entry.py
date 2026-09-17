#!/usr/bin/env python3
"""dsh/TUI pane entry (cross-platform): cd to the Herdr-reported workspace and
launch the ZCode TUI (zcode on PATH, app-bundle zcode.cjs, or ZCODE_BIN)."""
import json, os, sys

def main():
    ctx = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if ctx:
        try:
            d = json.loads(ctx)
            cwd = d.get("workspace_cwd") or d.get("focused_pane_cwd")
            if cwd and os.path.isdir(cwd):
                os.chdir(cwd)
        except Exception:
            pass
    bin_ = os.environ.get("ZCODE_BIN")
    if not bin_:
        from shutil import which
        bin_ = which("zcode")
    if not bin_:
        for c in ("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
                  os.path.join(os.environ.get("LOCALAPPDATA", ""),
                               "Programs", "ZCode", "resources", "glm", "zcode.cjs")):
            if c and os.path.isfile(c):
                bin_ = c; break
    if not bin_:
        print("zcode: ZCode CLI not found (install ZCode app or set ZCODE_BIN)",
              file=sys.stderr)
        return 127
    if bin_.endswith(".cjs"):
        node = os.environ.get("NODE_BIN") or "node"
        argv = [node, bin_]
    else:
        argv = [bin_]
    # ZCode.app auto-updates relocate the bundled provider config (now
    # Resources/config/provider/zcode-builtin.json) while the CLI resolves it
    # next to the .cjs and dies with 无法定位 CLI ZCode Built-in Provider
    # Config. Point it at the real file explicitly.
    if not os.environ.get("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"):
        res = os.path.dirname(os.path.dirname(bin_)) if bin_ else ""
        for cand in (os.path.join(res, "config", "provider", "zcode-builtin.json") if res else "",
                     os.path.join(os.path.dirname(bin_ or ""), "provider", "zcode-builtin.json")):
            if cand and os.path.isfile(cand):
                os.environ["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] = cand
                break
    if os.name != "nt" and hasattr(os, "execv"):
        return os.execv(argv[0], argv)
    # Windows: execv would tear down the ConPTY (see pane_entry) — wait instead
    return subprocess.call(argv) if (subprocess := __import__("subprocess")) else 1

if __name__ == "__main__":
    sys.exit(main() or 0)
