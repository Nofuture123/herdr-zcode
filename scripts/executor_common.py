#!/usr/bin/env python3
"""Shared plumbing for zcode-bridge executors: herdr agent-state reporting,
in-process NAR kernel access, and the trusted result pipeline."""
import importlib.util, json, os, socket, sys, threading, time

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

_here = os.path.dirname(os.path.abspath(__file__))
broker = _load("broker", os.path.join(_here, "broker.py"))
broker.init_dirs()

BASE = broker.BASE
NAR_VENV = os.path.join(BASE, "venv")
RESULTS = broker.RESULTS

def build_kernel():
    """In-process NAR Kernel: real cancel, honest reconcile, no subprocess CLI."""
    if not os.path.exists(os.path.join(NAR_VENV, "bin", "python")):
        print("bridge runtime missing; run scripts/ensure-bridge.sh", file=sys.stderr)
        sys.exit(3)
    sys.path.insert(0, os.path.join(NAR_VENV, "lib",
                    [d for d in os.listdir(os.path.join(NAR_VENV, "lib")) if d.startswith("python")][0],
                    "site-packages"))
    from native_agent_router.config import load_config
    from native_agent_router.kernel.kernel import Kernel
    from native_agent_router.kernel.store import TaskStore
    return Kernel(load_config(None), TaskStore())

# ---------- herdr agent-state (socket protocol, best-effort) ----------
H_ENV = os.environ.get("HERDR_ENV")
H_SOCK = os.environ.get("HERDR_SOCKET_PATH")
H_PANE = os.environ.get("HERDR_PANE_ID")
H_SRC = "herdr:zcode"
_seq = [int(time.time() * 1000)]
_last_state = [None]
LAST_SESSION = [None]

def _next():
    _seq[0] += 1
    return _seq[0]

def _send(req, t=0.5):
    if H_ENV != "1" or not H_SOCK or not H_PANE:
        return
    for timeout in (t, 1.5):
        try:
            sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sk.settimeout(timeout)
            sk.connect(H_SOCK)
            sk.sendall((json.dumps(req) + "\n").encode())
            sk.recv(512)
            sk.close()
            return
        except OSError:
            continue

def report(state, message=None, force=False):
    if not force and state == _last_state[0]:
        return
    _last_state[0] = state
    p = {"pane_id": H_PANE, "source": H_SRC, "agent": "zcode",
         "state": state, "message": message, "seq": _next()}
    if LAST_SESSION[0]:
        p["agent_session_id"] = LAST_SESSION[0]
    _send({"id": f"{H_SRC}:{time.time()}:{_next()}",
           "method": "pane.report_agent", "params": p})

def report_session(sid):
    if not sid:
        return
    _send({"id": f"{H_SRC}:session:{time.time()}:{_next()}",
           "method": "pane.report_agent_session",
           "params": {"pane_id": H_PANE, "source": H_SRC, "agent": "zcode",
                      "seq": _next(), "agent_session_id": sid}})

def remember_session(native_session_id):
    if native_session_id:
        LAST_SESSION[0] = native_session_id
        report_session(native_session_id)

# ---------- output hygiene ----------
import re as _re
_CTRL = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def sanitize(text, limit=600):
    if text is None:
        return ""
    text = _CTRL.sub(" ", str(text)).replace("\n", " ")
    return text[:limit]
def emit(out, kind, payload):
    out(f"[zcodecli:{kind}] " + json.dumps(payload, ensure_ascii=True))

def receipt_ok(nonce, **fields):
    """Durable accepted-ack for the send client (survives pane wrapping)."""
    if nonce:
        try:
            broker.save_receipt(nonce, {"ok": True, **fields})
        except Exception:
            pass

def receipt_err(nonce, error, **fields):
    """Durable rejection-ack for the send client (survives pane wrapping)."""
    if nonce:
        try:
            broker.save_receipt(nonce, {"ok": False, "error": str(error)[:300], **fields})
        except Exception:
            pass

# NOTE: acceptance/verdict logic deliberately lives in the MASTER (shared skill),
# not here. The bridge reports facts only: status / verify_ok / out_of_scope.

def collect(d):
    """Flatten a NAR task snapshot into the trusted result dict."""
    res = d.get("result") or {}
    diff = res.get("diff") or {}
    verifies = res.get("verify") or []
    return {
        "task_id": d.get("task_id"), "status": d.get("status"), "ok": bool(d.get("ok")),
        "summary": (res.get("worker_summary") or "")[:600].replace("\n", " "),
        "changed_files": diff.get("changed_files") or [],
        "out_of_scope": diff.get("out_of_scope") or [],
        "verify_ok": all(v.get("ok") for v in verifies) if verifies else None,
        "verify": verifies, "native_session_id": d.get("native_session_id"),
        "usage": res.get("usage"), "error": d.get("error"),
        "duration_sec": res.get("duration_sec"),
    }

def attach_summary_full(data, task_id):
    """Recover the FULL last assistant message (NAR caps worker_summary, which
    can cut verdict lines); stored only when it beats the capped summary."""
    try:
        full = native_final_text(task_id)
    except Exception:
        full = None
    full = (full or "").strip()
    if full and full != (data.get("summary") or "").strip():
        data["summary_full"] = full
    return data

def persist(d):
    tid = d.get("task_id")
    if tid and broker.TASK_RE.match(tid):
        with open(broker.result_path(tid), "w") as f:
            json.dump(d, f, indent=1)
        os.chmod(broker.result_path(tid), 0o600)

# ---------- live native output streaming ----------
# Palette: pi's "enchanted-forest" theme (awesome-pi-themes), truecolor.
_RST = "\033[0m"
_C_MUTED = "\033[38;2;157;187;155m"    # thinkingText #9dbb9b
_C_TOOL = "\033[38;2;183;245;176m"     # toolTitle  #b7f5b0
_C_OK = "\033[38;2;159;245;200m"       # success    #9ff5c8
_C_ERR = "\033[38;2;255;123;147m"      # error      #ff7b93
_C_DIM = "\033[38;2;101;125;98m"       # dim        #657d62
_C_DEEP = "\033[38;2;47;158;68m"       # accentDeep #2f9e44 (code-block border)
_C_HEAD = "\033[38;2;183;245;176m"     # mdHeading  #b7f5b0
_C_ITAL = "\033[2;3;38;2;157;187;155m"  # dim+italic muted (thinking)
WARN = "\033[38;2;214;198;95m"         # warning    #d6c65f
if os.environ.get("NO_COLOR") or os.environ.get("QAB_EXEC_PLAIN"):
    _C_MUTED = _C_TOOL = _C_OK = _C_ERR = _C_DIM = WARN = ""

def native_log_path(task_id):
    base = os.path.expanduser(os.environ.get("NAR_LOGS_DIR", "~/.native-agent-router/logs"))
    return os.path.join(base, task_id, "native-raw.jsonl")

def native_final_text(task_id, cap=4000):
    """Full text of the LAST assistant message from the native log. NAR caps
    worker_summary (~800 chars), which can cut verdict lines off the tail;
    this recovers the complete message. None when unavailable."""
    path = native_log_path(task_id)
    if not os.path.exists(path):
        return None
    msgs, order = {}, []
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("msg") or {}
                if msg.get("method") != "session/event":
                    continue
                pl = (msg.get("params") or {}).get("payload") or {}
                if pl.get("kind") == "text_delta" and pl.get("delta"):
                    mid = pl.get("assistantMessageId") or "?"
                    if mid not in msgs:
                        msgs[mid] = []
                        order.append(mid)
                    msgs[mid].append(pl["delta"])
    except OSError:
        return None
    for mid in reversed(order):
        text = "".join(msgs[mid]).strip()
        if text:
            return text[:cap]
    return None

def stream_native_output(task_id, out, stop):
    """Tail the NAR native protocol log and render the agent's live activity
    into the pane, pi-CLI style: italic muted thinking with an opener rule,
    blank-line paragraph spacing, fenced code as a numbered bordered block,
    cyan-free accent tool lines with `└ ✓` summary results, and a heartbeat.
    QAB_EXEC_QUIET=1 mutes everything; QAB_EXEC_REASONING=0 mutes thinking."""
    if not task_id or os.environ.get("QAB_EXEC_QUIET", "") == "1" \
            or not broker.TASK_RE.match(task_id):
        return
    show_reasoning = os.environ.get("QAB_EXEC_REASONING", "") != "0"
    path = native_log_path(task_id)
    pos = 0
    text_buf = [""]
    think_buf = [""]
    in_code = [False]
    code_no = [0]
    thinking_open = [False]
    cur_mid = [None]
    last_blank = [True]
    t0 = time.time()
    last_emit = [t0]

    def raw(sx):
        out(sx)

    def emit_line(sx):
        last_emit[0] = time.time()
        last_blank[0] = False
        raw(sx)

    def gap():
        if not last_blank[0]:
            raw("")
            last_blank[0] = True

    def heartbeat():
        if os.environ.get("QAB_EXEC_HEARTBEAT") != "1":
            return
        now = time.time()
        if now - last_emit[0] >= 20:
            last_emit[0] = now
            gap()
            raw(f"{_C_DIM}── {int(now - t0)}s ──{_RST}")
            last_blank[0] = True

    def render_text(line):
        line = sanitize(line, 400)
        if line.lstrip().startswith("```"):
            if in_code[0]:
                in_code[0] = False
                return f"{_C_DEEP}╰{'─' * 8}{_RST}"
            gap()
            in_code[0] = True
            code_no[0] = 0
            return f"{_C_DEEP}╭{'─' * 4} code {'─' * 4}{_RST}"
        if in_code[0]:
            code_no[0] += 1
            return f"{_C_DEEP}{code_no[0]:>3} │{_RST} {line}"
        if line.startswith("#"):
            return f"{_C_HEAD}{line}{_RST}"
        return line

    def flush_text(force=False):
        buf = text_buf[0]
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            if line.strip():
                emit_line(render_text(line))
        text_buf[0] = "" if force else buf
        if force and buf.strip():
            emit_line(render_text(buf))

    def flush_think(force=False):
        buf = think_buf[0]
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            if line.strip():
                emit_line(f"{_C_ITAL}· {sanitize(line, 400)}{_RST}")
        think_buf[0] = "" if force else buf
        if force and buf.strip():
            emit_line(f"{_C_ITAL}· {sanitize(buf, 400)}{_RST}")

    try:
        idle_polls = 0
        while not stop.is_set():
            if not os.path.exists(path):
                stop.wait(0.5)
                heartbeat()
                continue
            with open(path, "r", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if not chunk:
                idle_polls += 1
                if idle_polls >= 5 and (text_buf[0] or think_buf[0]):
                    # stream quiet ~1.3s: flush partial last lines so the tail
                    # of a message is visible without waiting for task end
                    flush_text(force=True)
                    flush_think(force=True)
                stop.wait(0.25 if idle_polls < 8 else 1.0)
                heartbeat()
                continue
            idle_polls = 0
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("msg") or {}
                if msg.get("method") != "session/event":
                    continue
                pl = (msg.get("params") or {}).get("payload") or {}
                kind = pl.get("kind") or pl.get("type")
                if kind == "text_delta" and pl.get("delta"):
                    mid = pl.get("assistantMessageId")
                    if mid != cur_mid[0]:
                        if in_code[0]:
                            in_code[0] = False
                            emit_line(f"{_C_DEEP}╰{'─' * 8}{_RST}")
                        cur_mid[0] = mid
                    if think_buf[0]:
                        flush_think(force=True)   # keep thinking before its answer
                    if thinking_open[0]:
                        thinking_open[0] = False
                        gap()
                    text_buf[0] += pl["delta"]
                    flush_text()
                elif kind == "reasoning_delta" and pl.get("delta") and show_reasoning:
                    if not thinking_open[0]:
                        gap()
                        thinking_open[0] = True
                        emit_line(f"{_C_DIM}── thinking ──{_RST}")
                    think_buf[0] += pl["delta"]
                    flush_think()
                elif kind == "tool_call":
                    flush_text(force=True)
                    flush_think(force=True)
                    thinking_open[0] = False
                    gap()
                    name = pl.get("toolName") or "?"
                    inp = pl.get("input") or {}
                    head = (inp.get("command") or inp.get("file_path")
                            or inp.get("path") or inp.get("pattern")
                            or json.dumps(inp, ensure_ascii=False))
                    emit_line(f"{_C_TOOL}▸ {sanitize(name, 40)}: {sanitize(head, 160)}{_RST}")
                elif kind == "result" and isinstance(pl.get("result"), dict):
                    r = pl["result"]
                    content = str(r.get("content") or "").replace("\n", " ⏎ ")
                    if r.get("success"):
                        emit_line(f"  {_C_DIM}└{_RST} {_C_OK}✓{_RST} {_C_DIM}{sanitize(content, 120)}{_RST}")
                    else:
                        emit_line(f"  {_C_DIM}└{_RST} {_C_ERR}✗{_RST} {sanitize(content, 240)}")
                else:
                    continue
                heartbeat()
    finally:
        flush_text(force=True)
        flush_think(force=True)
        if in_code[0]:
            emit_line(f"{_C_DEEP}╰{'─' * 8}{_RST}")
