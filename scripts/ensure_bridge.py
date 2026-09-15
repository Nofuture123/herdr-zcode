#!/usr/bin/env python3
"""Cross-platform herdr plugin bootstrap (replaces ensure-bridge.sh +
plugin-build.sh). Runs on `herdr plugin install` and via the doctor action.

Checks (fail loudly, with fix hints): Node >= 22, python3, git, ZCode
executor, ZCode login. Then: venv + pinned NAR (atomic switch), stable
wrappers, PATH launchers (real files, never symlinks), doctor gate.
Idempotent; safe to re-run.
"""
import json, os, re, shutil, subprocess, sys, time, venv

BASE = os.path.expanduser("~/.local/share/herdr-zcode")
VENV = os.path.join(BASE, "venv")
BIN = os.path.join(BASE, "bin")
LOG = os.path.join(BASE, "last-ensure.log")
NAR_SHA = "d65bd49755bf4f6637b3c103650175b1b789e3ae"   # verified runtime; upstream tag moved
NAR_PIN = ("native-agent-router @ git+https://github.com/BerineYang/"
           "native-agent-router.git@" + NAR_SHA)
IS_WIN = os.name == "nt"

def fail(msg, hint=""):
    print(f"✗ [zcode-bridge] {msg}" + (f"\n    fix: {hint}" if hint else ""), file=sys.stderr)
    sys.exit(1)

def find_node():
    cands = [os.environ.get("NODE_BIN"), shutil.which("node"),
             "/opt/homebrew/bin/node", "/usr/local/bin/node",
             r"C:\Program Files\nodejs\node.exe"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None

def find_python():
    for c in (os.environ.get("PY3"), sys.executable, shutil.which("python3"),
              shutil.which("python")):
        if c and os.path.isfile(c):
            return c
    return None

def resolve_zcode_bin():
    """ZCODE_BIN env -> PATH -> macOS app bundle -> Windows app layout."""
    env = os.environ.get("ZCODE_BIN")
    if env and os.path.isfile(env):
        return env
    on_path = shutil.which("zcode")
    if on_path:
        return on_path
    mac = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"
    if os.path.isfile(mac):
        return mac
    local = os.environ.get("LOCALAPPDATA")
    if local:
        win = os.path.join(local, "Programs", "ZCode", "resources", "glm", "zcode.cjs")
        if os.path.isfile(win):
            return win
    return None

def check_login():
    """ZCode OAuth credentials must exist (provider segment varies)."""
    base_dir = os.environ.get("ZCODE_DATA_BASE_DIR") or os.path.expanduser("~")
    cred = os.path.join(base_dir, ".zcode", "v2", "credentials.json")
    ok = False
    try:
        with open(cred, "r", errors="replace") as f:
            ok = re.search(r'"oauth:[a-z][a-z0-9_]*:access_token"', f.read()) is not None
    except OSError:
        pass
    if not ok:
        fail(f"ZCode is not logged in (no OAuth credentials in {cred})",
             "1. run: zcode login   2. finish sign-in in browser   "
             "3. reinstall: herdr plugin install Nofuture123/herdr-zcode")

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def nar_commit(venv_dir):
    import glob
    hits = glob.glob(os.path.join(venv_dir, "**", "native_agent_router-*.dist-info",
                                  "direct_url.json"), recursive=True)
    for f in hits:
        try:
            m = re.search(r'"commit_id": "([0-9a-f]*)"', open(f).read())
            if m:
                return m.group(1)
        except OSError:
            pass
    return None

def ensure_venv(py, node_bin, zcode_bin):
    """Create/recreate the pinned-NAR venv; atomic switch; doctor gate."""
    if os.path.isfile(os.path.join(VENV,
            "Scripts" if IS_WIN else "bin", "nar" + (".exe" if IS_WIN else ""))):
        if nar_commit(VENV) == NAR_SHA:
            print("install: present (commit matches)")
            return
        else:
            print(f"install: commit mismatch; reinstalling")
            shutil.rmtree(VENV, ignore_errors=True)
    print("installing: pinned native-agent-router (atomic: build aside, verify, switch)")
    tmp = VENV + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    r = run([py, "-m", "venv", tmp])
    if r.returncode != 0:
        print(r.stderr[-500:], file=sys.stderr); fail("venv creation failed")
    vpy = os.path.join(tmp, "Scripts", "python.exe") if IS_WIN else os.path.join(tmp, "bin", "python")
    r = run([vpy, "-m", "pip", "install", "-q", NAR_PIN])
    if r.returncode != 0:
        print(r.stderr[-800:], file=sys.stderr); fail("NAR install failed")
    if nar_commit(tmp) != NAR_SHA:
        shutil.rmtree(tmp, ignore_errors=True); fail("NAR SHA mismatch after install")
    r = run([vpy, "-m", "native_agent_router", "doctor"],
            env={**os.environ, "ZCODE_BIN": zcode_bin or ""})
    if any(l.startswith("[X]") for l in (r.stdout or "").splitlines()):
        shutil.rmtree(tmp, ignore_errors=True)
        print(r.stdout[-800:], file=sys.stderr); fail("doctor FAILED on new venv")
    if os.path.isdir(VENV):
        shutil.rmtree(VENV + ".old", ignore_errors=True)
        os.replace(VENV, VENV + ".old")
    os.replace(tmp, VENV)
    shutil.rmtree(VENV + ".old", ignore_errors=True)
    fix_venv_paths()
    print("install: ok (atomic switch complete)")

def fix_venv_paths():
    """After the atomic switch (venv.new -> venv) rewrite build-time paths that
    pip baked into console-script shebangs and activate scripts."""
    bindir = os.path.join(VENV, "Scripts" if IS_WIN else "bin")
    for fn in os.listdir(bindir):
        fp = os.path.join(bindir, fn)
        try:
            with open(fp) as f:
                body = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        fixed = body.replace(VENV + ".new", VENV).replace(VENV + ".old", VENV)
        if fixed != body:
            with open(fp, "w") as f:
                f.write(fixed)

def write_wrappers(py, node_bin, zcode_bin):
    os.makedirs(BIN, exist_ok=True)
    nar_real = os.path.join(VENV, "Scripts", "python.exe") if IS_WIN \
        else os.path.join(VENV, "bin", "python")
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    def write(name, body):
        fp = os.path.join(BIN, name)
        with open(fp, "w", newline="\n") as f:
            f.write(body)
        if not IS_WIN:
            os.chmod(fp, 0o755)
    if not IS_WIN:
        write("nar", f'#!/bin/sh\n. "$HOME/.local/share/herdr-zcode/env.sh"\n'
                     f'export ZCODE_BIN="${{ZCODE_BIN:-{zcode_bin}}}"\n'
                     f'exec "{VENV}/bin/nar" "$@"\n')
        write("zcodecli", f'#!/bin/sh\n. "$HOME/.local/share/herdr-zcode/env.sh"\n'
                          f'export ZCODE_BIN="${{ZCODE_BIN:-{zcode_bin}}}"\n'
                          f'exec "{py}" "{scripts_dir}/zcodecli_cli.py" "$@"\n')
        write("zcodecli-mcp", f'#!/bin/sh\n. "$HOME/.local/share/herdr-zcode/env.sh"\n'
                              f'export ZCODE_BIN="${{ZCODE_BIN:-{zcode_bin}}}"\n'
                              f'exec "{py}" "{scripts_dir}/zcodecli_mcp.py" "$@"\n')
    else:
        def cmd(exe):
            return (f'@echo off\r\nset "ZCODE_BIN={zcode_bin}"\r\n'
                    f'"{py}" "{scripts_dir}\\{exe}" %*\r\n')
        write("zcodecli.cmd", cmd("zcodecli_cli.py"))
        write("zcodecli-mcp.cmd", cmd("zcodecli_mcp.py"))
        write("nar.cmd", f'@echo off\r\nset "ZCODE_BIN={zcode_bin}"\r\n'
                         f'"{os.path.join(VENV, "Scripts", "nar.exe")}" %*\r\n')
    # env.sh for unix wrappers + tools
    prefix = os.path.dirname(node_bin) if node_bin else "/opt/homebrew/bin"
    with open(os.path.join(BASE, "env.sh"), "w", newline="\n") as f:
        f.write(f'export PATH="{prefix}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"\n'
                f'export NODE_BIN="{node_bin}"\nexport PY3="{py}"\n')
    with open(os.path.join(BASE, "env.json"), "w") as f:
        json.dump({"node": node_bin, "py": py, "zcode_bin": zcode_bin}, f, indent=1)

def path_launchers():
    """Install real launcher files into a PATH dir — never symlinks, so the
    on-disk story is always the real path. Migrates legacy symlink installs."""
    if IS_WIN:
        return  # .cmd wrappers live in BASE/bin; add to PATH manually if wanted
    link_dir = None
    for d in ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            link_dir = d; break
    if not link_dir:
        return
    installed = []
    for name in ("zcodecli", "nar"):
        dst = os.path.join(link_dir, name)
        src = os.path.join(BIN, name)
        with open(src) as f:
            body = f.read()
        if os.path.islink(dst):
            target = os.path.realpath(dst)
            if BASE in target or "qonnwolf-zcode-bridge" in target:
                os.remove(dst)              # legacy install of ours: symlink -> real file
            else:
                print(f"skip: {dst} is a foreign symlink (left untouched)")
                continue
        if os.path.exists(dst):
            with open(dst) as f:
                cur = f.read()
            if cur == body:
                installed.append(dst); continue
            # our own launcher from an earlier install (runtime renamed once):
            # refresh it so upgrades actually ship new launcher bodies
            if "herdr-zcode" in cur or "qonnwolf-zcode-bridge" in cur:
                with open(dst, "w", newline="\n") as f:
                    f.write(body)
                os.chmod(dst, 0o755)
                installed.append(dst)
                print(f"PATH launcher refreshed: {dst}")
                continue
            print(f"skip: {dst} already exists (not ours)")
            continue
        with open(dst, "w", newline="\n") as f:
            f.write(body)
        os.chmod(dst, 0o755)
        installed.append(dst)
        print(f"PATH launcher: {dst}")
    if installed:
        with open(os.path.join(BASE, "path-links"), "w") as f:
            f.write("\n".join(installed) + "\n")

def fix_windows_python3(py):
    """Herdr panes spawn with bare `python3`; on Windows the WindowsApps alias
    may be a silent stub. Point `python3.cmd` at the real interpreter and, if
    the stub shadows it, remove the stub (it is a user-owned reparse point)."""
    if not IS_WIN or not py:
        return
    real = os.path.realpath(py)
    wa = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                      "Microsoft", "WindowsApps")
    stub = os.path.join(wa, "python3.exe")
    try:
        if os.path.isfile(stub):
            r = run([stub, "-c", "print(1)"])
            if r.returncode == 0 and "1" in (r.stdout or ""):
                return                       # a real interpreter; leave it
            os.remove(stub)                  # silent Store alias: remove
            print("removed silent python3.exe Store alias")
    except OSError:
        pass
    try:
        with open(os.path.join(wa, "python3.cmd"), "w") as f:
            f.write("@echo off\r\n" + f'"{real}" %*\r\n')
        print(f"python3 shim -> {real}")
    except OSError as e:
        print(f"WARN: could not install python3 shim ({e}); "
              f"create python3.cmd pointing at {real} in a PATH dir",
              file=sys.stderr)


def main():
    os.makedirs(BASE, exist_ok=True)
    lines = [f"ensure-bridge (py) {time.strftime('%F %T')}"]
    def log(msg):
        lines.append(msg); print(msg)
    node = find_node()
    log(f"node: {node or 'NOT FOUND'}")
    if not node:
        fail("Node.js >= 22 required", "brew install node (macOS) / winget install OpenJS.NodeJS (Windows)")
    try:
        v = run([node, "-v"]).stdout.strip()
        major = int(re.match(r"v?(\d+)", v).group(1))
        if major < 22:
            fail(f"Node.js >= 22 required, found {v}")
    except Exception:
        fail("could not determine node version")
    py = find_python()
    log(f"python3: {py or 'NOT FOUND'}")
    if not py:
        fail("python3 required")
    if not shutil.which("git") and not IS_WIN:
        fail("git required", "xcode-select --install, or brew install git")
    zcode = resolve_zcode_bin()
    log(f"zcode_bin: {zcode or 'NOT FOUND'}")
    if not zcode:
        fail("ZCode executor not found — install the ZCode app (or set ZCODE_BIN)")
    check_login()
    os.makedirs(BIN, exist_ok=True)
    fix_windows_python3(py)
    ensure_venv(py, node, zcode)
    write_wrappers(py, node, zcode)
    path_launchers()
    # doctor gate: nar must answer and report no [X] problems
    nar = os.path.join(VENV, "Scripts", "python.exe") if IS_WIN \
        else os.path.join(VENV, "bin", "python")
    r = run([nar, "-m", "native_agent_router", "doctor"],
            env={**os.environ, "ZCODE_BIN": zcode or ""})
    out_path = os.path.join(BASE, "doctor.out")
    with open(out_path, "w") as f:
        f.write(r.stdout or "")
    if r.returncode != 0 or any(l.startswith("[X]") for l in (r.stdout or "").splitlines()):
        fail("doctor FAILED", f"see {out_path}")
    log("doctor: ok")
    with open(LOG, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("✓ [zcode-bridge] bootstrap ok")

if __name__ == "__main__":
    main()
