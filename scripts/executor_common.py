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
    if full and len(full) > len(data.get("summary") or ""):
        data["summary_full"] = full
    return data

def persist(d):
    tid = d.get("task_id")
    if tid and broker.TASK_RE.match(tid):
        with open(broker.result_path(tid), "w") as f:
            json.dump(d, f, indent=1)
        os.chmod(broker.result_path(tid), 0o600)

# ---------- live native output streaming ----------
_DIM, _RST = "\033[2m", "\033[0m"
_CYA, _GRN, _RED = "\033[36m", "\033[32m", "\033[31m"

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
    into the pane, pi-CLI style: dim rule with elapsed seconds as a heartbeat,
    reasoning in dim `·` lines, assistant text plain, cyan `▸` tool lines and
    short ✓/✗ result lines. Best-effort — unknown events are ignored, and the
    marker/receipt protocol is untouched. QAB_EXEC_QUIET=1 mutes everything;
    QAB_EXEC_REASONING=0 mutes reasoning."""
    if not task_id or os.environ.get("QAB_EXEC_QUIET", "") == "1" \
            or not broker.TASK_RE.match(task_id):
        return
    show_reasoning = os.environ.get("QAB_EXEC_REASONING", "") != "0"
    path = native_log_path(task_id)
    pos = 0
    bufs = {"text": "", "reasoning": ""}
    t0 = time.time()
    last_emit = [t0]

    def heartbeat():
        now = time.time()
        if now - last_emit[0] >= 20:
            last_emit[0] = now
            out(f"{_DIM}── {int(now - t0)}s ──{_RST}")

    def render(kind, line):
        line = sanitize(line, 400)
        return f"{_DIM}· {line}{_RST}" if kind == "reasoning" else line

    def emit_line(s):
        last_emit[0] = time.time()
        out(s)

    def flush(kind, force=False):
        buf = bufs[kind]
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            if line.strip():
                emit_line(render(kind, line))
        bufs[kind] = "" if force else buf
        if force and buf.strip():
            emit_line(render(kind, buf))

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
                # adaptive backoff: snappy while the log grows, cheap when the
                # task is silent (long tool runs); display lags pi by <1s max
                idle_polls += 1
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
                    bufs["text"] += pl["delta"]
                    flush("text")
                elif kind == "reasoning_delta" and pl.get("delta") and show_reasoning:
                    bufs["reasoning"] += pl["delta"]
                    flush("reasoning")
                elif kind == "tool_call":
                    flush("text", force=True)
                    flush("reasoning", force=True)
                    name = pl.get("toolName") or "?"
                    inp = pl.get("input") or {}
                    head = (inp.get("command") or inp.get("file_path")
                            or inp.get("path") or inp.get("pattern")
                            or json.dumps(inp, ensure_ascii=False))
                    emit_line(f"{_CYA}▸ {sanitize(name, 40)}: {sanitize(head, 160)}{_RST}")
                elif kind == "result" and isinstance(pl.get("result"), dict):
                    r = pl["result"]
                    content = str(r.get("content") or "").replace("\n", " ⏎ ")
                    if r.get("success"):
                        emit_line(f"  {_GRN}✓{_RST} {_DIM}{sanitize(content, 120)}{_RST}")
                    else:
                        emit_line(f"  {_RED}✗{_RST} {sanitize(content, 240)}")
                else:
                    continue
                heartbeat()
    finally:
        flush("text", force=True)
        flush("reasoning", force=True)
