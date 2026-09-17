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
# nar launcher: real file in the runtime bin — `nar.cmd` on Windows (CreateProcessW
# cannot exec .cmd, but subprocess/cmd resolves it), `nar` elsewhere
NAR = os.path.join(os.path.expanduser("~"), ".local", "share", "herdr-zcode", "bin",
                   "nar.cmd" if os.name == "nt" else "nar")

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

LAST_PANE_FILE = os.path.join(os.path.expanduser(
    "~/.local/share/herdr-zcode"), "last-executor-pane")

def remember_pane(pid):
    try:
        os.makedirs(os.path.dirname(LAST_PANE_FILE), exist_ok=True)
        with open(LAST_PANE_FILE, "w") as f:
            f.write(pid)
    except OSError:
        pass

def last_pane():
    try:
        with open(LAST_PANE_FILE) as f:
            pid = f.read().strip()
        return pid or None
    except OSError:
        return None

def resolve_pane(explicit=None):
    if explicit: return explicit
    lp = last_pane()
    if lp:
        rc, out, _ = herdr(["pane", "get", lp])   # positional: pane get has no --pane option
        if rc == 0:
            labeled = []
            try:
                walk_find(json.loads(out), lambda d: "label" in d, labeled)
            except json.JSONDecodeError:
                pass
            # stick only when the id still points at a bridge pane — after a
            # pane move/close the id can outlive its executor and land on a
            # plain shell that would swallow sent lines without any receipt
            if not labeled or labeled[0].get("label") == LABEL:
                return lp
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
    # tab placement: explicit ID > --workspace ID > the caller's own workspace
    # (masters run inside herdr panes) > herdr's session default
    ws_id = a.herdr_workspace or ws_arg or os.environ.get("HERDR_WORKSPACE_ID")
    args = ["tab","create","--cwd",cwd,"--label",a.label or f"zcode-chat-{time.strftime('%H%M%S')}"]
    if ws_id: args += ["--workspace",ws_id]
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
    rc4, o4, e4 = herdr(["pane","run",pane_id,
                          "stty -echo 2>/dev/null; zcodecli chat; stty echo 2>/dev/null"])
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
    args = ["plugin", "pane", "open", "--plugin", "zcode",
            "--entrypoint", "executor", "--placement", a.placement]
    ws_arg = a.workspace
    if ws_arg and (os.path.isdir(ws_arg) or ws_arg.startswith(("/", "~", "."))):
        # plugin pane open --workspace wants a workspace ID (e.g. w73), not a
        # path; a path almost always means the caller wants the cwd — same
        # fixup as chat-open, landed via --cwd
        args += ["--cwd", os.path.realpath(os.path.expanduser(ws_arg))]
        print("note: --workspace got a path; using it as --cwd (default workspace)",
              file=sys.stderr)
        ws_arg = None
    # explicit ID > $HERDR_WORKSPACE_ID (masters run inside herdr panes) >
    # herdr's session default — same resolution order as chat-open
    ws_id = a.herdr_workspace or ws_arg or os.environ.get("HERDR_WORKSPACE_ID")
    if ws_id:
        args += ["--workspace", ws_id]
    rc, stdout, stderr = herdr(args)
    print((stdout or stderr).strip()); return rc

def need_pane(explicit=None):
    pid = resolve_pane(explicit)
    if pid and not explicit:
        remember_pane(pid)
    if not pid:
        print(f"executor pane not found. open it first: zcodecli open", file=sys.stderr); sys.exit(2)
    return pid

def auto_open_executor(timeout_s=3.0):
    """No executor pane: open the reception pane ourselves (idempotent). The
    open response carries the pane id, so this is instant; input typed before
    the executor finishes booting is buffered by the tty line discipline."""
    args = ["plugin", "pane", "open", "--plugin", "zcode",
            "--entrypoint", "executor", "--placement", "tab"]
    ws_id = os.environ.get("HERDR_WORKSPACE_ID")   # open in the caller's workspace
    if ws_id:
        args += ["--workspace", ws_id]
    _, stdout, _ = herdr(args)
    try:
        pid = json.loads(stdout)["result"]["plugin_pane"]["pane"]["pane_id"]
        if pid:
            print(f"note: executor pane was missing — auto-opened {pid}", file=sys.stderr)
            remember_pane(pid)
            return pid
    except Exception:
        pass
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pid = resolve_pane()
        if pid:
            print(f"note: executor pane was missing — auto-opened {pid}", file=sys.stderr)
            remember_pane(pid)
            return pid
        time.sleep(0.25)
    return None

def cmd_send(a):
    pid = resolve_pane(getattr(a, 'pane', None))
    if not pid:
        pid = auto_open_executor()
    if not pid:
        print("executor pane not found and auto-open failed. run: zcodecli open", file=sys.stderr); return 2
    ws = a.workspace or os.getcwd()
    if not os.path.isdir(ws): print(f"workspace not a dir: {ws}", file=sys.stderr); return 2
    ws = os.path.realpath(ws)   # one canonical form: locks and sessions key on this
    text = a.text
    stripped = text.strip()
    nonce = secrets.token_hex(4)
    flags = any(getattr(a, k, None) for k in ("verify", "mode", "policy", "scope", "key", "timeout"))
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
        obj = {"goal": text, "workspace": ws, "nonce": nonce}
        if a.verify: obj["verify"] = a.verify
        if a.mode: obj["mode"] = a.mode
        if a.policy: obj["policy"] = a.policy
        if a.scope: obj["scope"] = [x.strip() for x in a.scope.split(",") if x.strip()]
        if a.key: obj["idempotency_key"] = a.key
        if a.timeout: obj["timeout"] = a.timeout
        text = json.dumps(obj, ensure_ascii=False)
    elif flags and not a.raw:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                if a.verify: obj["verify"] = a.verify
                if a.mode: obj["mode"] = a.mode
                if a.policy: obj["policy"] = a.policy
                if a.scope: obj["scope"] = [x.strip() for x in a.scope.split(",") if x.strip()]
                if a.key: obj["idempotency_key"] = a.key
                if a.timeout: obj["timeout"] = a.timeout
                nonce = obj.get("nonce") or nonce
                obj["nonce"] = nonce
                text = json.dumps(obj, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    # disk delivery: the request file is the guaranteed channel (any executor
    # picks it up, even if this pane dies mid-delivery); the pane line is its
    # fast-path copy. Raw/slash lines stay pure tty — that's the human path.
    try:
        env = json.loads(text)
    except Exception:
        env = None
    if not a.raw and not stripped.startswith("/") and isinstance(env, dict):
        env["request_id"] = "r-" + secrets.token_hex(8)
        text = json.dumps(env, ensure_ascii=False)
        try:
            broker.save_request(env["request_id"], env)
        except Exception:
            pass   # 磁盘通道不可用:面板快路径仍在线上
    rc, stdout, stderr = herdr(["pane", "run", pid, text])
    if rc != 0 and "pane_not_found" in (stderr or "") and not getattr(a, "pane", None):
        pid = auto_open_executor()          # targeted pane was closed; self-heal
        if not pid:
            print((stderr or stdout).strip(), file=sys.stderr); return rc
        rc, stdout, stderr = herdr(["pane", "run", pid, text])
    if rc != 0:
        print((stderr or stdout).strip(), file=sys.stderr); return rc
    if a.raw or stripped.startswith("/"):
        print(f"sent to pane {pid} (workspace: {ws})")
        return 0
    # Receipt wait: the executor persists an ack to the receipts dir (fold-proof);
    # pane markers on both sources are the fallback for older executors.
    t0 = time.time()
    deadline = t0 + 15
    receipt = None
    marks = []
    while time.time() < deadline:
        receipt = broker.load_receipt(nonce)
        if receipt:
            break
        # v0.3.2+ executors answer on disk within ~1s; only fall back to pane
        # heuristics after giving the receipt a fair chance
        if time.time() - t0 >= 3:
            _, out3, _ = herdr(["pane", "read", pid, "--source", "recent-unwrapped",
                                "--lines", "200"])
            marks = [l for l in (out3 or "").splitlines()
                     if nonce in l and ("[zcodecli:accepted]" in l or "[zcodecli:error]" in l)]
            if not marks:
                # viewport fallback: the nonce may sit in a soft-wrapped line.
                # Tight window + no echoed-JSON/nested-marker inside, else a
                # PREVIOUS task's marker would match this nonce's echo.
                _, outv, _ = herdr(["pane", "read", pid, "--source", "visible",
                                    "--lines", "200"])
                vis = outv or ""
                if nonce in vis:
                    i = vis.find(nonce)
                    for marker in ("[zcodecli:error]", "[zcodecli:accepted]"):
                        m = vis.rfind(marker, max(0, i - 220), i)
                        if m == -1:
                            continue
                        window = vis[m:i + len(nonce) + 2]
                        if '"goal"' in window or marker in window[len(marker):]:
                            break   # window spans another task's line: not ours
                        marks.append(window)
                        break
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
    """Wait for a task's terminal result — resolved from disk evidence
    (owners/ + results/), independent of pane markers."""
    pid = resolve_pane(getattr(a, 'pane', None))
    req = getattr(a, "request", None)
    deadline = time.time() + a.timeout / 1000

    def latest_task_for_pane(pane):
        best = (0.0, None)
        try:
            for f in os.listdir(broker.OWNERS):
                if not f.endswith(".json"):
                    continue
                try:
                    rec = json.load(open(os.path.join(broker.OWNERS, f)))
                except Exception:
                    continue
                if (pane is None or rec.get("pane_id") == pane) and rec.get("ts", 0) > best[0]:
                    best = (rec.get("ts", 0), rec.get("task_id"))
        except OSError:
            pass
        return best[1]

    tid = None
    while True:
        if req:
            if not broker.REQ_RE.match(req or ""):
                print(f"invalid request id: {req!r}", file=sys.stderr); return 1
            rp = os.path.join(broker.REQUESTS, req + ".json")
            if not os.path.exists(rp):
                print(f"unknown request {req}", file=sys.stderr); return 1
            try:
                tid = json.load(open(rp)).get("task_id")
            except Exception:
                tid = None
        else:
            tid = latest_task_for_pane(pid)
        terminal = False
        if tid and broker.TASK_RE.match(tid) and os.path.exists(broker.result_path(tid)):
            try:
                st = json.load(open(broker.result_path(tid))).get("status") or ""
            except Exception:
                st = ""
            terminal = st in ("succeeded", "failed", "cancelled", "killed")
        if terminal:
            break
        if time.time() > deadline:
            print("no terminal result within timeout", file=sys.stderr)
            return 1
        time.sleep(0.5)

    rid_s = "-"
    try:
        rid_s = (broker.find_request_by_task(tid) or {}).get("request_id", "-")
    except Exception:
        pass
    if req and rid_s not in ("-", req):
        print("evidence-file identity mismatch — refusing", file=sys.stderr)
        return 1
    rf = broker.result_path(tid)
    full = json.load(open(rf))
    res = full.get("result") or {}
    diff = res.get("diff") or {}
    verifies = res.get("verify") or []
    summ = (res.get("worker_summary") or "")[:800]
    # the executor persists the uncapped last assistant message when the
    # native log was available — prefer it (verdict lines live at the tail)
    if full.get("summary_full") and len(full["summary_full"]) > len(summ):
        summary_full = full["summary_full"]
        summ = summary_full[:1500]
    else:
        summary_full = None
    data = {"request_id": rid_s, "task_id": tid,
            "status": full.get("status"),
            "summary": summ,
            "summary_full": summary_full,
            "changed_files": diff.get("changed_files") or [],
            "out_of_scope": diff.get("out_of_scope") or [],
            "verify": verifies,
            "verify_ok": (all(v.get("ok") for v in verifies) if verifies else None),
            "usage": res.get("usage"),
            "error": full.get("error")}
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
    for ln in wrap_dw(sm, W - 6)[:12]:
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
    if getattr(a, "machine", False):
        # MCP bridge consumes this: raw nar JSON, untouched
        print(p.stdout.strip() or "[]")
        return p.returncode
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
    if os.name == "nt":
        return subprocess.call([NAR, "inspect"] + a.nar_args)   # execv can't run .cmd
    os.execv(NAR, [NAR, "inspect"] + a.nar_args)

def cmd_open_session(a):
    """Open a delegated task's full native conversation (resume by session id)."""
    tid = a.task_id
    if not broker.TASK_RE.match(tid or ""):
        print("usage: zcodecli open-session <task_id>", file=sys.stderr); return 2
    rf = broker.result_path(tid)
    if not os.path.exists(rf):
        print(f"no evidence file for {tid}", file=sys.stderr); return 1
    sess = json.load(open(rf)).get("native_session_id")
    if not sess:
        print(f"task {tid} has no native session id", file=sys.stderr); return 1
    zbin = os.environ.get("ZCODE_BIN") or "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"
    cmd = [os.environ.get("NODE_BIN") or "node", zbin, "--resume", sess]
    if a.print:
        print(" ".join(cmd)); return 0
    print(f"opening {sess} …", file=sys.stderr)
    os.execvp(cmd[0], cmd)


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

def cmd_steer(a):
    """Redirect the running task: cancel + resubmit in the SAME native session."""
    text = a.text.strip()
    if not text:
        print("usage: zcodecli steer <new instruction>", file=sys.stderr); return 2
    pid = resolve_pane(getattr(a, "pane", None))
    if not pid:
        print("executor pane not found", file=sys.stderr); return 2
    rc, stdout, stderr = herdr(["pane", "run", pid, f"/steer {text}"])
    if rc != 0:
        print((stderr or stdout).strip(), file=sys.stderr); return rc
    print(f"steered pane {pid}: {text[:60]}")
    return 0


p = argparse.ArgumentParser(prog="zcodecli")
p.add_argument("--pane", default=None, help="target a specific executor/chat pane id (default: the zcode-bridge reception pane)")
sub = p.add_subparsers(dest="cmd")
s = sub.add_parser("open"); s.add_argument("--placement", default="tab", choices=["tab", "split", "overlay", "zoomed"]); s.add_argument("--workspace", default=None, help="workspace ID (e.g. w7Y); a path is used as --cwd"); s.add_argument("--herdr-workspace", default=None, help="herdr workspace ID (e.g. w7Y); default: $HERDR_WORKSPACE_ID"); s.set_defaults(fn=cmd_open)
s = sub.add_parser("chat-open"); s.add_argument("--workspace", default=None); s.add_argument("--herdr-workspace", default=None, help="herdr workspace ID (e.g. w7Y) for the new tab; default: $HERDR_WORKSPACE_ID"); s.add_argument("--cwd", default=None); s.add_argument("--label", default=None); s.set_defaults(fn=cmd_chat_open)
s = sub.add_parser("send"); s.add_argument("text"); s.add_argument("--workspace", default=None,
                   help="override workspace (default: caller's cwd)")
s.add_argument("--raw", action="store_true", help="send text verbatim (no JSON envelope)")
s.add_argument("--verify", default=None, help="verify command (required for edit/yolo)")
s.add_argument("--mode", default=None, choices=["plan", "build", "edit", "yolo"])
s.add_argument("--policy", default=None, choices=["allow", "deny"])
s.add_argument("--scope", default=None, help="comma-separated relative paths")
s.add_argument("--key", default=None, help="idempotency_key")
s.add_argument("--timeout", type=int, default=None)
s.set_defaults(fn=cmd_send)
s = sub.add_parser("result"); s.add_argument("--request", default=None,
                   help="wait for the result of THIS request_id"); s.add_argument("--machine", action="store_true")
s.add_argument("--timeout", type=int, default=300000); s.set_defaults(fn=cmd_result)
s = sub.add_parser("read"); s.add_argument("--lines", type=int, default=40); s.set_defaults(fn=cmd_read)
s = sub.add_parser("close"); s.set_defaults(fn=cmd_close)
s = sub.add_parser("chat"); s.add_argument("--workspace", default=None); s.set_defaults(fn=cmd_chat)
s = sub.add_parser("wait"); s.add_argument("text"); s.add_argument("--timeout", type=int, default=60000); s.set_defaults(fn=cmd_wait)
s = sub.add_parser("list"); s.add_argument("nar_args", nargs="*"); s.add_argument("--machine", action="store_true"); s.set_defaults(fn=cmd_list)
s = sub.add_parser("inspect"); s.add_argument("nar_args", nargs="*"); s.set_defaults(fn=cmd_nar_inspect)
s = sub.add_parser("cancel"); s.add_argument("nar_args", nargs="*"); s.set_defaults(fn=cmd_cancel)
s = sub.add_parser("open-session"); s.add_argument("task_id"); s.add_argument("--print", action="store_true"); s.set_defaults(fn=cmd_open_session)
s = sub.add_parser("steer"); s.add_argument("text"); s.set_defaults(fn=cmd_steer)
a = p.parse_args()
if not getattr(a, "cmd", None):
    a.cmd = "chat"; a.workspace = None; a.fn = cmd_chat   # bare `zcodecli` = start a session, like pi/codex
sys.exit(a.fn(a) or 0)
