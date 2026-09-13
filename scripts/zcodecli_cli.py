#!/usr/bin/env python3
"""zcodecli — herdr-protocol client for the zcode-bridge executor pane.

Any CLI agent can run this (or the raw `herdr` commands it wraps):
  zcodecli open                     open the executor pane (tab placement)
  zcodecli send <text>              send a line to the executor (task JSON or plain text)
  zcodecli result [--timeout MS]    wait for [qab:result] and print the parsed JSON
  zcodecli read [--lines N]         read recent executor output
  zcodecli wait <text> [--timeout]  wait for literal text in the pane
  zcodecli list|inspect|cancel ...  nar passthrough (structured task evidence)
"""
import argparse, json, os, re, secrets, subprocess, sys, time
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("broker", os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker.py"))
broker = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(broker)
_ctrl_re = __import__("re").compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
def sanitize(text, limit=800):
    if text is None: return ""
    return _ctrl_re.sub(" ", str(text)).replace("\n", " ")[:limit]

def _c(n): return f"\033[{n}m"
DIM,BOLD,GREEN,RED,YEL,CYA,RST = _c(2),_c(1),_c(32),_c(31),_c(33),_c(36),_c(0)
SC = {"succeeded":GREEN,"failed":RED,"cancelled":YEL,"cancel_requested":YEL,
      "orphaned":DIM,"unknown":DIM,"running":CYA}
def dot(st):
    c = SC.get(st, DIM); return f"{c}● {st}{RST}"
def trunc(t,n): return (t[:n-1]+"…") if len(t)>n else t
import unicodedata, re as _re
def sw(s):
    s=_re.sub(r"\033\[[0-9;]*m","",s)
    return sum(2 if unicodedata.east_asian_width(ch) in "FW" else 1 for ch in s)
def wrap_dw(t, width):
    lines,cur,lw=[],[],0
    for ch in t:
        w=2 if unicodedata.east_asian_width(ch) in "FW" else 1
        if lw+w>width: lines.append("".join(cur)); cur,lw=[],0
        cur.append(ch); lw+=w
    if cur: lines.append("".join(cur))
    return lines or [""]

LABEL = "zcode-bridge"
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
NAR = os.path.expanduser("~/.local/share/qonnwolf-zcode-bridge/bin/nar")

def herdr(args, timeout=60):
    p = subprocess.run([HERDR] + args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def walk_find(obj, key_pred, out):
    if isinstance(obj, dict):
        if key_pred(obj):
            out.append(obj)
        for v in obj.values(): walk_find(v, key_pred, out)
    elif isinstance(obj, list):
        for v in obj: walk_find(v, key_pred, out)

def resolve_pane(explicit=None):
    if explicit: return explicit
    rc, stdout, stderr = herdr(["pane", "list", "--json"])
    if rc != 0:
        rc2, out2, err2 = herdr(["pane", "list"])
        stdout = out2 or (stderr + err2)
    found = []
    try:
        walk_find(json.loads(stdout), lambda d: d.get("label") == LABEL and "pane_id" in d, found)
    except json.JSONDecodeError:
        m = re.search(r'"pane_id"\s*:\s*"([^"]+)"[^{}]*"label"\s*:\s*"%s"' % LABEL, stdout)
        if m: return m.group(1)
    if found: return found[-1]["pane_id"]
    # fallback: match title/label in rendered text
    for line in stdout.splitlines():
        if LABEL in line:
            m = re.search(r'(w\d+:[a-zA-Z0-9]+)', line)
            if m: return m.group(1)
    return None

def cmd_chat_open(a):
    cwd = a.cwd or os.getcwd()
    ws_arg = a.workspace
    if ws_arg and (os.path.isdir(ws_arg) or ws_arg.startswith(("/", "~", "."))):
        # herdr tab create --workspace wants a workspace ID (e.g. w73), not a
        # path; a path almost always means the caller wants the cwd. Fix it.
        if not a.cwd: cwd = os.path.realpath(os.path.expanduser(ws_arg))
        print(f"note: --workspace got a path; using it as --cwd {cwd} (default workspace)", file=sys.stderr)
        ws_arg = None
    args = ["tab","create","--cwd",cwd,"--label",a.label or f"zcode-chat-{time.strftime('%H%M%S')}"]
    if ws_arg: args += ["--workspace",ws_arg]
    rc, stdout, stderr = herdr(args)
    try:
        d=json.loads(stdout); tab=d["result"]["tab"]
        tab_id=tab.get("tab_id"); ws=tab.get("workspace_id")
    except Exception:
        print((stdout or stderr).strip(), file=sys.stderr); return rc or 1
    rc2, out2, err2 = herdr(["pane","list","--tab",tab_id]) if False else (0,None,None)
    # find the pane of the new tab
    rc3, pl, _ = herdr(["pane","list"])
    pane_id=None
    try:
        for pp in json.loads(pl)["result"]["panes"]:
            if pp.get("tab_id")==tab_id: pane_id=pp["pane_id"]; break
    except Exception: pass
    if not pane_id: print("tab created but pane not found:", tab_id, file=sys.stderr); return 1
    rc4, o4, e4 = herdr(["pane","run",pane_id,"zcodecli chat"])
    if rc4 != 0: print((e4 or o4).strip(), file=sys.stderr); return rc4
    print(f"chat pane: {pane_id} (tab {tab_id}, workspace {ws})")
    print(f"send turns: zcodecli --pane {pane_id} send '...'   ·  read: zcodecli --pane {pane_id} result")
    return 0

def cmd_chat(a):
    # top-level session CLI: runs executor_chat.py in THIS pane (one session per pane)
    if a.workspace: os.environ["QAB_WORKSPACE"] = a.workspace
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "executor_chat.py")
    os.execv(sys.executable, [sys.executable, script])

def cmd_open(a):
    rc, stdout, stderr = herdr(["plugin", "pane", "open", "--plugin", "zcode",
                                "--entrypoint", "executor", "--placement", a.placement])
    print((stdout or stderr).strip()); return rc

def need_pane(explicit=None):
    pid = resolve_pane(explicit)
    if not pid:
        print(f"executor pane not found. open it first: zcodecli open", file=sys.stderr); sys.exit(2)
    return pid

def cmd_send(a):
    pid = need_pane(getattr(a, 'pane', None))
    ws = a.workspace or os.getcwd()
    if not os.path.isdir(ws): print(f"workspace not a dir: {ws}", file=sys.stderr); return 2
    ws = os.path.realpath(ws)   # one canonical form: locks and sessions key on this
    text = a.text
    stripped = text.strip()
    nonce = secrets.token_hex(4)
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                if not obj.get("workspace"): obj["workspace"] = ws
                nonce = obj.get("nonce") or nonce   # effective nonce: caller's wins
                obj["nonce"] = nonce
                text = json.dumps(obj, ensure_ascii=False)
        except json.JSONDecodeError:
            pass  # malformed JSON is delivered verbatim; the executor will fail it closed
    elif not a.raw and not stripped.startswith("/"):
        text = json.dumps({"goal": text, "workspace": ws, "nonce": nonce}, ensure_ascii=False)
    rc, stdout, stderr = herdr(["pane", "run", pid, text])
    if rc != 0:
        print((stderr or stdout).strip(), file=sys.stderr); return rc
    if a.raw or stripped.startswith("/"):
        print(f"sent to pane {pid} (workspace: {ws})")
        return 0
    # Receipt wait: the executor persists an ack to the receipts dir (fold-proof);
    # pane markers on both sources are the fallback for older executors.
    deadline = time.time() + 15
    receipt = None
    while time.time() < deadline:
        receipt = broker.load_receipt(nonce)
        if receipt:
            break
        _, out3, _ = herdr(["pane", "read", pid, "--source", "recent-unwrapped",
                            "--lines", "200"])
        marks = [l for l in (out3 or "").splitlines()
                 if nonce in l and ("[zcodecli:accepted]" in l or "[zcodecli:error]" in l)]
        if not marks:
            # viewport fallback: the nonce may sit in a soft-wrapped line, so
            # match around it instead of requiring one clean line
            _, outv, _ = herdr(["pane", "read", pid, "--source", "visible", "--lines", "200"])
            vis = outv or ""
            if nonce in vis:
                i = vis.find(nonce)
                m = vis.rfind("[zcodecli:error]", max(0, i - 300), i)
                if m == -1:
                    m = vis.rfind("[zcodecli:accepted]", max(0, i - 300), i)
                if m != -1:
                    marks.append(vis[m:i + len(nonce) + 2])
        if marks:
            break
        time.sleep(0.5)
    if receipt:
        if receipt.get("ok"):
            print("accepted:", " ".join(str(receipt.get(k, "-"))
                                        for k in ("request_id", "task_id", "status")))
            return 0
        print(receipt.get("error", "rejected"), file=sys.stderr)
        return 5
    if not marks:
        print("no receipt within 15s (task may still be queued; re-check with "
              f"zcodecli --pane {pid} read)", file=sys.stderr)
        return 4
    last = marks[-1]
    if "[zcodecli:error]" in last:
        print(last.split("[zcodecli:error]",1)[1].strip(), file=sys.stderr); return 5
    try: print("accepted:", last.split("[zcodecli:accepted]",1)[1].strip())
    except Exception: print(last)
    return 0

def cmd_close(a):
    pid = resolve_pane(getattr(a, "pane", None))
    if not pid: print("executor pane not running (nothing to close)"); return 0
    rc, stdout, stderr = herdr(["pane", "close", pid])
    print(f"closed executor pane {pid}"); print((stderr or "").strip())
    print("note: in-flight task becomes orphaned (report as interrupted); completed history persists")
    return rc

def cmd_read(a):
    pid = need_pane(getattr(a,'pane',None))
    rc, stdout, stderr = herdr(["pane", "read", pid, "--source", "visible", "--lines", str(a.lines)])
    print(stdout or stderr); return rc

def cmd_wait(a):
    pid = need_pane(getattr(a,'pane',None))
    rc, stdout, stderr = herdr(["pane", "wait-output", "--match", a.text, "--timeout", str(a.timeout), pid])
    print((stdout or stderr).strip()); return rc

def cmd_result(a):
    pid = need_pane(getattr(a, 'pane', None))
    req = getattr(a, "request", None)

    def grab():
        lines = []
        for src in ("recent-unwrapped", "visible"):
            _, out2, _ = herdr(["pane", "read", pid, "--source", src, "--lines", "160"])
            lines += [l for l in (out2 or "").splitlines()
                      if l.startswith("[zcodecli:result]")]
        if req:
            lines = [l for l in lines if f" {req} " in l]
        return lines[-1] if lines else None

    line = grab()
    if line is None:
        # wait-output matches FUTURE output only; existing content was checked above
        rc, stdout, stderr = herdr(["pane", "wait-output", "--match", "[zcodecli:result]",
                                    "--timeout", str(a.timeout), pid],
                                   timeout=a.timeout / 1000 + 15)
        line = grab()
    if not line:
        print("no [zcodecli:result] within timeout", file=sys.stderr)
        return 1
    parts = line.split()
    if len(parts) < 3:
        print(f"malformed result signal: {line!r}", file=sys.stderr)
        return 1
    rid_s, tid = parts[1], parts[2]
    data = {"request_id": rid_s, "task_id": tid}
    try:
        rf = broker.result_path(tid)
    except ValueError:
        print(f"invalid task_id in marker: {tid!r}", file=sys.stderr)
        return 1
    if os.path.exists(rf):
        full = json.load(open(rf))
        if full.get("request_id") != rid_s or full.get("task_id") != tid:
            print("evidence-file identity mismatch — refusing", file=sys.stderr)
            return 1
        res = full.get("result") or {}
        diff = res.get("diff") or {}
        verifies = res.get("verify") or []
        data.update({
            "status": full.get("status") or data.get("status"),
            "summary": (res.get("worker_summary") or "")[:800],
            "changed_files": diff.get("changed_files") or [],
            "out_of_scope": diff.get("out_of_scope") or [],
            "verify": verifies,
            "verify_ok": (all(v.get("ok") for v in verifies) if verifies else None),
            "usage": res.get("usage"),
        })
    if a.machine:
        print(json.dumps(data, ensure_ascii=False))
        return 0
    st = data.get("status") or "?"; col = SC.get(st, DIM)
    W = 76; L = "╭" + "─" * W + "╮"; M = "│"; R = "╰" + "─" * W + "╯"
    def row(txt=""):
        pad = W - sw(txt) - 2
        return f"{M} {txt}{' ' * max(0, pad)}{M}"
    print(L)
    print(row(f"{BOLD}TASK {tid}{RST}   {col}● {st}{RST}   {DIM}request {rid_s}{RST}"))
    print(row(f"{DIM}evidence: {rf.replace(os.path.expanduser('~'), '~')}{RST}"))
    print("├" + "─" * W + "┤")
    import textwrap
    sm = sanitize((data.get("summary") or "(no summary)"), 800)
    for ln in wrap_dw(sm, W - 6)[:8]:
        print(row("  " + ln))
    print("├" + "─" * W + "┤")
    cf = data.get("changed_files") or []
    print(row(f"{BOLD} changed files{RST}  {', '.join(cf) if cf else DIM + '(none)' + RST}"))
    oos = data.get("out_of_scope") or []
    if oos:
        print(row(f"{RED} ⚠ out of scope{RST}  {', '.join(oos)}"))
    for v in (data.get("verify") or []):
        mark = f"{GREEN}✓{RST}" if v.get("ok") else f"{RED}✗{RST}"
        print(row(f" {mark} verify: {trunc(str(v.get('cmd', '')), W - 16)} (exit {v.get('exit_code')})"))
    u = data.get("usage") or {}
    if u:
        print(row(f"{DIM} tokens: in {u.get('input_tokens', '?')} · out {u.get('output_tokens', '?')} · total {u.get('total_tokens', '?')} ({u.get('quality', '?')}){RST}"))
    if data.get("error"):
        print(row(f"{RED} error: {trunc(str(data['error']), W - 12)}{RST}"))
    print(R)
    return 0

def cmd_list(a):
    if not os.path.exists(NAR):
        print("bridge not installed; run ensure-bridge.sh first", file=sys.stderr); return 2
    p = subprocess.run([NAR, "list"], capture_output=True, text=True)
    try: tasks = json.loads(p.stdout)
    except Exception: print(p.stdout or p.stderr); return p.returncode
    tasks.sort(key=lambda t: t.get("created_at",""))
    print(f"{BOLD}  {'STATUS':<18}{'TASK':<16}{'TIME':<7}{'GOAL':<34}{RST}")
    print(f"{DIM}  {'─'*18}{'─'*16}{'─'*7}{'─'*34}{RST}")
    for t in tasks:
        st=t.get("status","?"); tid=t.get("task_id","?"); ts=t.get("created_at","")[11:19]
        c=SC.get(st,DIM)
        print(f"  {c}●{RST} {c}{st:<16}{RST} {DIM}{tid:<16}{RST} {ts}  {trunc(t.get('goal',''),33)}")
    print(f"{DIM}  {len(tasks)} task(s) · lock: serial per workspace (abspath; "
          f"parallel tickets need distinct paths/worktrees){RST}")
    return 0

def cmd_nar_inspect(a):
    if not os.path.exists(NAR):
        print("bridge not installed; run the plugin startup or ensure-bridge.sh", file=sys.stderr); return 2
    os.execv(NAR, [NAR, "inspect"] + a.nar_args)

def cmd_cancel(a):
    tid = a.nar_args[0] if a.nar_args else None
    if not tid: print("usage: zcodecli cancel <task_id>", file=sys.stderr); return 2
    owner = broker.owner_of(tid)
    pid = owner or need_pane(getattr(a, "pane", None))
    rc, stdout, stderr = herdr(["pane", "run", pid, f"/cancel {tid}"])
    if rc != 0: print((stderr or stdout).strip(), file=sys.stderr); return rc
    rc2, out2, _ = herdr(["pane", "wait-output", "--regex",
                          "already_terminal|cancel_requested|cancelled \\(confirmed|interrupted|not known to this executor|owned by pane",
                          "--timeout", "35000", pid], timeout=40)
    rc3, out3, _ = herdr(["pane", "read", pid, "--source", "visible", "--lines", "40"])
    for l in (out3 or "").splitlines()[::-1]:
        if "already_terminal" in l: print(l.strip()); return 0
        if "interrupted" in l: print(l.strip()); return 0
        if "cancel_requested" in l: print(l.strip()); return 1
        if "cancelled (confirmed stopped)" in l or "not the running task" in l:
            print(l.strip()); return 0 if "confirmed" in l else 1
        if ("not known to this executor" in l or "owned by pane" in l
                or "cannot cancel" in l or "refusing to cancel" in l):
            print(l.strip()); return 1
    print((out2 or "no cancel outcome within 35s").strip(), file=sys.stderr); return 1

p = argparse.ArgumentParser(prog="zcodecli")
p.add_argument("--pane", default=None, help="target a specific executor/chat pane id (default: the zcode-bridge reception pane)")
sub = p.add_subparsers(dest="cmd")
s = sub.add_parser("open"); s.add_argument("--placement", default="tab", choices=["tab", "split", "overlay", "zoomed"]); s.set_defaults(fn=cmd_open)
s = sub.add_parser("chat-open"); s.add_argument("--workspace", default=None); s.add_argument("--cwd", default=None); s.add_argument("--label", default=None); s.set_defaults(fn=cmd_chat_open)
s = sub.add_parser("send"); s.add_argument("text"); s.add_argument("--workspace", default=None,
                   help="override workspace (default: caller's cwd)")
s.add_argument("--raw", action="store_true", help="send text verbatim (no JSON envelope)")
s.set_defaults(fn=cmd_send)
s = sub.add_parser("result"); s.add_argument("--request", default=None,
                   help="wait for the result of THIS request_id"); s.add_argument("--machine", action="store_true")
s.add_argument("--timeout", type=int, default=300000); s.set_defaults(fn=cmd_result)
s = sub.add_parser("read"); s.add_argument("--lines", type=int, default=40); s.set_defaults(fn=cmd_read)
s = sub.add_parser("close"); s.set_defaults(fn=cmd_close)
s = sub.add_parser("chat"); s.add_argument("--workspace", default=None); s.set_defaults(fn=cmd_chat)
s = sub.add_parser("wait"); s.add_argument("text"); s.add_argument("--timeout", type=int, default=60000); s.set_defaults(fn=cmd_wait)
s = sub.add_parser("list"); s.add_argument("nar_args", nargs="*"); s.set_defaults(fn=cmd_list)
s = sub.add_parser("inspect"); s.add_argument("nar_args", nargs="*"); s.set_defaults(fn=cmd_nar_inspect)
s = sub.add_parser("cancel"); s.add_argument("nar_args", nargs="*"); s.set_defaults(fn=cmd_cancel)
a = p.parse_args()
if not getattr(a, "cmd", None):
    a.cmd = "chat"; a.workspace = None; a.fn = cmd_chat   # bare `zcodecli` = start a session, like pi/codex
sys.exit(a.fn(a) or 0)
