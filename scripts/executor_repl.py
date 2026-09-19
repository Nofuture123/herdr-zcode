#!/usr/bin/env python3
"""zcode-bridge executor — reception pane (serial task queue). PURE transport.

Protocol (one line per stdin):
  <text>                    -> freeform task (owner-authorized full access)
  {json}                    -> strict spec, fail-closed; edit/yolo requires verify
  /continue <req_id> <text> -> rework turn; inherits parent spec, sets session_ref
  /cancel                   -> cancel the running task (tri-state, honest)
  /status | /list | /inspect <id> | /help | /quit

The bridge is transport + bookkeeping ONLY: it reports facts (status / verify_ok /
out_of_scope) and never issues acceptance verdicts or rework limits — those belong
to the master agent (see skills/zcode-bridge/SKILL.md).
Markers are signals carrying identifiers only; evidence is results/<task_id>.json.
"""
import json, os, queue, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from executor_common import (broker, build_kernel, report, remember_session,
                             emit, collect, persist, sanitize, stream_native_output,
                             receipt_ok, receipt_err, attach_summary_full, WARN, marker_line,
                             display_text, _C_DIM, start_disk_pickup)

def _c(n): return f"\033[{n}m"
DIM, BOLD, GREEN, RED, YEL, CYA, RST = _c(2), _c(1), _c(32), _c(31), _c(33), _c(36), _c(0)
# Default workspace = the pane's own cwd (wherever this pane was opened; the
# opener's workspace with `zcodecli open --workspace`). The caller's cwd rides
# in every zcodecli-sent ticket anyway; this only covers bare text typed into
# the pane. QAB_DEFAULT_WORKSPACE still pins it explicitly.
DEFAULT_WS = os.path.realpath(os.environ.get("QAB_DEFAULT_WORKSPACE") or os.getcwd())
DEFAULT_MODE = os.environ.get("QAB_DEFAULT_MODE", "yolo")
DEFAULT_POLICY = os.environ.get("QAB_DEFAULT_POLICY", "allow")
REQUIRE_VERIFY = os.environ.get("QAB_REQUIRE_VERIFY", "1") == "1"
GUI_TOOLS = ["computer-use", "computer_use", "mcp__computer-use", "cua", "screenshot", "gui"]

class Reception:
    def __init__(self, kernel, out=print, pane_id=None):
        self.kernel = kernel
        self.out = out
        self.pane_id = pane_id or os.environ.get("HERDR_PANE_ID")
        self.busy = None            # task_id
        self.active_request = None  # request_id
        self._own_cancels = set()   # tids this pane itself is cancelling
        self.pending_steer = None
        self.last_finished_tid = None
        self.current_nonce = None
        self.last = None            # last request record (spec + task_id)

    # -- markers: identifiers only, no business verdicts --
    def sig_accepted(self, rid, tid, state, nonce=None):
        line = marker_line("accepted", f"{rid} {tid} {state}" + (f" {nonce}" if nonce else ""))
        if line:
            self.out(line)
        receipt_ok(nonce, request_id=rid, task_id=tid, status=state)

    def sig_result(self, rid, tid):
        line = marker_line("result", f"{rid} {tid}")
        if line:
            self.out(line)

    def sig_error(self, msg, nonce=None):
        nonce = nonce or self.current_nonce
        receipt_err(nonce, msg)
        emit(self.out, "error", {"ok": False, "error": sanitize(msg, 200),
                                 **({"nonce": nonce} if nonce else {})})

    # -- lifecycle --
    def submit(self, spec, rid, attempt=1):
        if spec.get("idempotency_key"):
            broker.idempotency_state(spec["idempotency_key"], broker.fingerprint(spec),
                                     "submitting", request_id=rid)
        kwargs = dict(scope_files=spec["scope"] or None, forbid=GUI_TOOLS,
                      verify=spec["verify"] or None, mode=spec["mode"],
                      permission_policy=spec["policy"], timeout_sec=float(spec["timeout"]),
                      idempotency_key=spec.get("idempotency_key"))
        if spec.get("session_ref"):
            kwargs["session_ref"] = spec["session_ref"]
        if spec.get("idempotency_key"):
            # The BRIDGE ledger owns once-only semantics. NAR's own key must be
            # unique per attempt: NAR returns the stale existing task forever
            # for a repeated key (field report #9), so a fresh attempt after a
            # terminal predecessor would collide with it.
            kwargs["idempotency_key"] = f"{spec['idempotency_key']}#{rid}#{attempt}"
        try:
            snap = self.kernel.submit("zcode", spec["goal"], spec["workspace"], **kwargs)
        except Exception:
            # never reached the queue: release the key so a retry is legal
            if spec.get("idempotency_key"):
                broker.idempotency_release(spec["idempotency_key"])
            raise
        tid = snap.get("task_id")
        broker.attach_task(rid, tid)
        broker.set_owner(tid, self.pane_id)
        if spec.get("idempotency_key"):
            broker.idempotency_state(spec["idempotency_key"], broker.fingerprint(spec),
                                     "attached", task_id=tid, request_id=rid)
        self.busy, self.active_request = tid, rid
        self.current_nonce = spec.get("nonce")
        self.sig_accepted(rid, tid, snap.get("status"), spec.get("nonce"))
        if not os.path.exists(os.path.join(spec["workspace"], ".git")):
            self.out(f"{WARN}⚠ workspace is not a git repo — changed_files evidence "
                     f"will be unavailable{RST}")
        if getattr(self, "_tail", None):
            self._tail.set()
        self._tail = threading.Event()
        self._tail_thread = threading.Thread(
            target=stream_native_output, args=(tid, self.out, self._tail), daemon=True)
        self._tail_thread.start()
        return tid

    def poll(self, tid, deadline):
        """Wait to terminal; honors /cancel arriving on the queue mid-run."""
        t0 = time.time()
        while time.time() < deadline:
            try:
                while True:
                    raw = self.q.get_nowait()
                    l = (raw or "").strip()
                    if l == "/cancel" or l.startswith("/cancel "):
                        ctid = l.split()[-1] if " " in l else tid
                        if ctid == tid or not self.busy:
                            self.do_cancel(ctid)   # tri-state honest outcome; keep polling
                        else:
                            self.sig_error(f"busy: {tid} running; cannot cancel {ctid} from here")
                    elif l.startswith("/steer "):
                        self.pending_steer = l[len("/steer "):].strip()
                        self.do_cancel(tid)        # redirect: cancel, resubmit after finish
                    elif l:
                        incoming = None
                        if l.startswith("{"):
                            try: incoming = json.loads(l).get("nonce")
                            except Exception: pass
                        self.sig_error(f"busy: {tid} running", nonce=incoming)
            except queue.Empty:
                pass
            snap = self.kernel.wait(tid, timeout_sec=0.5)
            st = snap.get("status")
            if st == "cancel_requested" and tid not in self._own_cancels:
                # cross-process `zcodecli cancel`: the CLI can only write the
                # status — THIS process owns the native session and the lock,
                # so escalate to a real kill (full cleanup, lock released).
                self.out(f"{YEL}cancel requested from another session — "
                         f"escalating to kill{RST}")
                try:
                    self.kernel.kill(tid)
                except Exception:
                    pass
            if snap.get("status") not in ("submitted", "running", "cancel_requested", None):
                return snap, time.time() - t0
        return self.kernel.snapshot(tid), time.time() - t0

    def finish(self, snap, spec, rid, dur):
        data = collect(snap)
        data.update({"request_id": rid, "_dur": round(dur, 1),
                     "result_file": broker.result_path(data["task_id"])})
        attach_summary_full(data, data["task_id"])
        # a task that died on the workspace lock never executed: release the
        # idempotency key so the caller's retry with the same key is legal
        if (data.get("status") == "failed" and spec.get("idempotency_key")
                and "workspace_busy" in str(data.get("error") or "")):
            broker.idempotency_release(spec["idempotency_key"])
        # one facts file per task: raw snapshot + flattened facts (master reads this)
        persist({**snap, "request_id": rid, "status": data["status"],
                 "ok": data["ok"], "verify_ok": data["verify_ok"],
                 "out_of_scope": data["out_of_scope"],
                 "changed_files": data["changed_files"],
                 "summary": data["summary"], "usage": data["usage"]})
        if getattr(self, "_tail", None):
            self._tail.set()
        if getattr(self, "_tail_thread", None):
            self._tail_thread.join(timeout=1.0)
        self.sig_result(rid, data["task_id"])
        # the key guarded THIS submission; terminal means it is free again —
        # resending the same key intentionally starts a NEW task (field
        # report #9: a key must never shadow a fresh attempt with a stale one)
        if spec.get("idempotency_key"):
            broker.idempotency_terminal(spec["idempotency_key"], broker.fingerprint(spec))
        mark = "✓" if data["ok"] else "✗"
        self.out(f"{_C_DIM}── {mark} {data['task_id']} {data['status']} · "
                 f"verify_ok={data['verify_ok']} ({data.get('_dur', '?')}s) ──{RST}")
        line = marker_line("summary", display_text(data["summary"], 200))
        if line:
            self.out(line)
        if getattr(self, "_tail", None):
            self._tail.set()
        remember_session(data.get("native_session_id"))
        self.last_finished_tid = data["task_id"]
        self.busy = self.active_request = None
        report("idle")
        self._launch_pending_steer()
        return data

    def background_wait(self, tid, spec, rid):
        def waiter():
            snap, _ = self.poll(tid, time.time() + 24 * 3600)
            if snap.get("status") in ("submitted", "running", "cancel_requested", None):
                self.sig("wait_cap", rid, tid, snap.get("status"))   # honest: still running, busy held
                return   # do NOT fabricate a verdict; busy stays, /cancel still works
            self.finish(snap, spec, rid, 0.0)
            self._launch_pending_steer()
        threading.Thread(target=waiter, daemon=True).start()

    def _launch_pending_steer(self):
        """After a steered task reaches terminal, relaunch with the new
        instruction in the same native session. NAR publishes terminal status
        before it releases the workspace lock, so a too-early relaunch fails
        workspace_busy without ever running — retry bounded."""
        if getattr(self, "pending_steer", None) and self.last_finished_tid:
            depth, f = 0, sys._getframe().f_back
            while f and depth < 64: depth += 1; f = f.f_back
            if depth >= 64:   # runaway steer chains must not exhaust the stack
                self.pending_steer = None
                return self.sig_error("steer chain too deep; dropped")
            text, self.pending_steer = self.pending_steer, None
            spec = dict(getattr(self, "active_spec", {}) or {})
            spec["goal"] = text
            spec["session_ref"] = self.last_finished_tid
            spec.pop("idempotency_key", None)
            rid = broker.new_request_id()
            broker.save_request(rid, spec)
            self.out(f"{YEL}↻ steering → {sanitize(text, 60)}{RST}")
            for attempt in range(12):
                try:
                    data = self.run_task(spec, rid) or {}
                except Exception as e:
                    if "workspace_busy" not in str(e) or attempt == 11:
                        raise
                    data = {"status": "failed", "error": str(e)}
                if not (data.get("status") == "failed"
                        and "workspace_busy" in str(data.get("error") or "")):
                    return
                self.out(f"{YEL}workspace lock still held; steer relaunch retry "
                         f"{attempt + 2}/12 …{RST}")
                time.sleep(0.5)

    def run_task(self, spec, rid):
        self.active_spec, self.active_request = spec, rid
        self.out(f"{DIM}── {time.strftime('%H:%M:%S')} ▶ {display_text(spec['goal'], 60)}{RST}")
        report("working", sanitize(spec["goal"], 80))
        # A busy workspace QUEUES, it does not fail (field report #6): wait for
        # the NAR lock before submitting, and if a submit still loses the race
        # (NAR fails the task async with workspace_busy), requeue — bounded.
        lock_window = min(float(spec["timeout"]), 1800.0)
        lock_deadline = time.time() + lock_window
        attempt = 0
        while True:
            attempt += 1
            if not self._wait_workspace_free(spec, rid, lock_deadline):
                self.out(f"{RED}✗ workspace still locked after {int(lock_window)}s "
                         f"— giving up (key released, resend is legal){RST}")
                if spec.get("idempotency_key"):
                    broker.idempotency_release(spec["idempotency_key"])
                self.sig_error(f"workspace_lock_timeout: {spec['workspace']} still locked "
                               f"after {int(lock_window)}s", nonce=spec.get("nonce"))
                report("idle")
                return None
            tid = self.submit(spec, rid, attempt)
            snap, dur = self.poll(tid, time.time() + spec["timeout"])
            if snap.get("status") in ("submitted", "running", None):
                self.out(f"[zcodecli:wait_timeout] {rid} {tid} (task continues; busy held)")
                return self.background_wait(tid, spec, rid)
            data = collect(snap)
            if (data.get("status") == "failed" and attempt < 3
                    and "workspace_busy" in str(data.get("error") or "")
                    and time.time() < lock_deadline):
                # lost the lock race between free-check and submit: the task
                # never ran. Sweep terminal-lock residue and requeue.
                self.out(f"{YEL}⏳ lost the workspace-lock race — requeueing "
                         f"(attempt {attempt + 1}/3){RST}")
                broker.sweep_workspace_locks(workspace=spec["workspace"])
                continue
            return self.finish(snap, spec, rid, dur)

    def _wait_workspace_free(self, spec, rid, deadline):
        """Poll the NAR workspace lock with backoff (2→15s) until free or the
        window closes. One 'queued' receipt so the master knows it's parked."""
        announced = False
        delay = 2.0
        while True:
            holder = broker.workspace_lock_holder(spec["workspace"])
            if not holder:
                return True
            if time.time() >= deadline:
                return False
            if not announced:
                announced = True
                self.out(f"{YEL}⏳ workspace locked by {holder.get('task_id')} — queueing "
                         f"(up to {int(deadline - time.time())}s){RST}")
                receipt_ok(spec.get("nonce"), request_id=rid, task_id="-",
                           status="queued (workspace lock)")
            time.sleep(min(delay, max(0.1, deadline - time.time())))
            delay = min(15.0, delay * 2)

    # -- entry points --
    def start(self, line):
        rid = broker.new_request_id()
        try:
            if line.startswith("{"):
                spec = broker.normalize_spec(json.loads(line), DEFAULT_WS, DEFAULT_MODE,
                                             DEFAULT_POLICY, require_verify=REQUIRE_VERIFY)
            else:
                spec = broker.normalize_spec({"goal": line, "workspace": DEFAULT_WS},
                                             DEFAULT_WS, DEFAULT_MODE, DEFAULT_POLICY)
        except (broker.SpecError, json.JSONDecodeError) as e:
            raw_nonce = None
            try: raw_nonce = json.loads(line).get("nonce")
            except Exception: pass
            return self.sig_error(f"rejected (fail-closed): {e}", nonce=raw_nonce)
        if spec.get("request_id"):
            # client-written ticket (disk pickup / pane fast-path copy): claim
            # exclusively. "taken" is NOT a failure — the ticket is already
            # being handled by another pane; ack so the master awaits ITS result
            st = broker.claim_request(spec["request_id"], self.pane_id or "repl")
            if st == "taken":
                receipt_ok(spec.get("nonce"), request_id=spec["request_id"],
                           task_id="-", status="claimed-elsewhere")
                self.out(f"{DIM}↗ {spec['request_id']} 已由其他 executor 领取,结果仍以 "
                         f"request_id 查询{RST}")
                return None
            rid = spec["request_id"]
        fp = broker.fingerprint(spec)
        if spec.get("idempotency_key"):
            st, rec = broker.idempotency_claim(spec["idempotency_key"], fp)
            if st == "conflict":
                raw_nonce = None
                try: raw_nonce = json.loads(line).get("nonce")
                except Exception: pass
                return self.sig_error("idempotency_conflict: same key, different spec", nonce=raw_nonce)
            if st == "indeterminate":
                raw_nonce = None
                try: raw_nonce = json.loads(line).get("nonce")
                except Exception: pass
                return self.sig_error("idempotency_indeterminate: previous attempt crashed "
                                      "mid-submit; inspect manually, do not blind-rerun", nonce=raw_nonce)
            if st == "duplicate":
                self.out(f"{YEL}↺ duplicate submit{RST}")
                raw_nonce = None
                try: raw_nonce = json.loads(line).get("nonce")
                except Exception: pass
                return self.sig_accepted(rec["request_id"], rec.get("task_id") or "-", "duplicate",
                                         nonce=raw_nonce)
            rid = rec["request_id"]
        broker.save_request(rid, spec)
        self.last = spec
        self.run_task(spec, rid)

    def do_continue(self, rest):
        rid_prefix, _, text = rest.partition(" ")
        if not rid_prefix.startswith("r-") or not text.strip():
            return self.sig_error("usage: /continue <parent_request_id> <text> "
                                  "(acceptance and rework limits are the master's job)")
        try:
            parent = broker.load_request(rid_prefix)
        except Exception:
            return self.sig_error(f"unknown parent request {rid_prefix}")
        spec = dict(parent["spec"]); spec["goal"] = text
        spec.pop("idempotency_key", None)
        if parent.get("task_id"):
            spec["session_ref"] = parent["task_id"]     # real native-session continuation
        rid = broker.new_request_id()
        broker.save_request(rid, spec, {"rework_of": rid_prefix})
        self.run_task(spec, rid)

    def do_cancel(self, tid=None):
        tid = tid or self.busy
        if not tid:
            self.out("nothing running"); return None
        snap = self.kernel.snapshot(tid)
        if not snap or not snap.get("task_id"):
            self.out(f"task {tid} is not known to this executor "
                     f"(stale store entry or foreign pane); use nar kill if force-needed")
            return "unknown_to_executor"
        owner = broker.owner_of(tid)
        if owner != os.environ.get("HERDR_PANE_ID"):
            self.out(f"task {tid} owner mismatch (owner={owner or 'none'}); refusing to cancel/kill here")
            return "foreign_owner"
        pre = self.kernel.snapshot(tid).get("status")
        if pre in ("succeeded", "failed", "cancelled", "killed"):
            self.out(f"already_terminal ({pre}) — not claiming a cancel")
            if tid != self.busy: self.busy = None if self.busy == tid else self.busy
            return "already_terminal"
        if tid != self.busy and self.busy:
            self.sig_error(f"task {tid} is not the running task ({self.busy}); cannot cancel from here")
            return "not_owner"
        busy_was_mine = (tid == self.busy)
        self._own_cancels.add(tid)
        try:
            self.out(f"{YEL}cancelling {tid} …{RST}")
            self.kernel.cancel(tid)
            deadline = time.time() + 10
            while time.time() < deadline:
                st = self.kernel.snapshot(tid).get("status")
                if st not in ("running", "submitted", "cancelling", "cancel_requested", None):
                    break
                time.sleep(0.3)
            else:
                self.kernel.kill(tid)
                st = self.kernel.snapshot(tid).get("status")
        finally:
            self._own_cancels.discard(tid)
        if st == "cancelled":
            self.out("✓ cancelled (confirmed stopped)")
        elif st in ("succeeded", "failed"):
            self.out(f"already_terminal ({st}) — task finished during cancel")
        elif st in ("running", "cancel_requested"):
            self.out("cancel_requested — not yet confirmed stopped; busy held until terminal")
            return "cancel_requested"   # busy stays; background waiter finalizes
        else:
            self.out(f"final state {st}")
        if busy_was_mine:
            self.busy = self.active_request = None
            report("idle")
        return st

    def do_steer(self, text):
        """Redirect the running task: cancel it, resubmit in the SAME native
        session with the new instruction (model sees all prior context)."""
        if not self.busy:
            self.out("nothing running"); return
        self.pending_steer = text
        self.do_cancel(self.busy)              # run_task relaunches with the steer

    def handle(self, line):
        line = line.strip()
        if not line: return
        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            if cmd == "/help": self.out(__doc__)
            elif cmd == "/quit": report("idle"); sys.exit(0)
            elif cmd == "/cancel":
                if self.busy: self.do_cancel()
                else: self.out("nothing running")
            elif cmd == "/list":
                nar = os.path.expanduser("~/.local/share/herdr-zcode/bin/nar")
                p = subprocess.run([nar, "list"], capture_output=True, text=True)
                self.out(p.stdout or p.stderr)
            elif cmd == "/status":
                import executor_common
                self.out(json.dumps({"busy": self.busy, "last_session": executor_common.LAST_SESSION[0]}))
            elif cmd == "/inspect":
                self.out(sanitize(str(self.kernel.inspect(rest.strip(), "summary")), 1500))
            elif cmd == "/continue": self.do_continue(rest)
            elif cmd == "/steer": self.do_steer(rest)
            else: self.sig_error(f"unknown command {cmd}")
            return
        if self.busy:
            return self.sig_error(f"busy: {self.busy} running; serial queue — /cancel or wait")
        self.start(line)

    def busy_active(self):
        """True while self.busy is genuinely unfinished. Heals the stuck-busy
        case (field report #7): a busy task that is ALREADY terminal but whose
        finish() never ran gets finalized here instead of rejecting every new
        line with a zombie 'busy' error forever."""
        if not self.busy:
            return False
        try:
            snap = self.kernel.snapshot(self.busy)
        except Exception:
            return True
        st = (snap or {}).get("status")
        if st is None or st in ("queued", "submitted", "running", "blocked",
                                "cancel_requested", "cancelling"):
            return True
        spec = (getattr(self, "active_spec", None) or getattr(self, "last", None)
                or {"goal": "(recovered task)", "workspace": DEFAULT_WS, "mode": "plan",
                    "scope": [], "verify": [], "policy": "allow", "timeout": 600})
        try:
            self.finish(snap, spec, self.active_request or f"r-recovered", 0.0)
        except Exception:
            self.busy = self.active_request = None
            report("idle")
        return self.busy is not None

    def process(self, line):
        """Main-loop body: honors /cancel mid-run, otherwise dispatches."""
        line = line.strip()
        if self.busy and not (line == "/cancel" or line.startswith("/cancel ")
                          or line.startswith("/steer ")):
            if self.busy_active():
                if line:
                    incoming = None
                    if line.startswith("{"):
                        try: incoming = json.loads(line).get("nonce")
                        except Exception: pass
                    self.sig_error(f"busy: {self.busy} running", nonce=incoming)
                return
            # busy was stale and got finalized — fall through to dispatch
        if line == "/cancel" or line.startswith("/cancel "):
            if self.busy:
                self.do_cancel()
            elif line.startswith("/cancel "):
                self.do_cancel(line.split()[-1])
            return
        if line.startswith("/steer "):
            if self.busy:
                self.do_steer(line[len("/steer "):].strip())
            else:
                self.out("nothing running")
            return
        try:
            self.handle(line)
        except SystemExit:
            raise
        except Exception as e:
            self.sig_error(f"executor: {e}")
            report("idle", force=True)


def _plugin_version():
    try:
        mf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "herdr-plugin.toml")
        for line in open(mf):
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "?"


def _retitle(workspace):
    """Best-effort pane rename so masters can reference the dedicated window
    by name (`herdr pane rename` semantics: label only, pane id unchanged)."""
    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    title = "zcode:" + (os.path.basename(workspace.rstrip(os.sep)) or "ws")
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        return
    try:
        subprocess.run([herdr, "pane", "rename", pane, title],
                       capture_output=True, text=True, timeout=5)
    except Exception:
        pass

def main(kernel=None):
    kernel = kernel or build_kernel()
    r = Reception(kernel)
    # Per-workspace visibility: a pane opened with `zcodecli open --cwd <wt>`
    # has DEFAULT_WS=<wt> != the plugin root — it becomes the DEDICATED
    # executor for that workspace (heartbeat registered, tickets for it route
    # here, pane retitles itself). The pane opened without --cwd stays the
    # catch-all reception queue (claims anything no dedicated pane wants).
    plugin_root = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    affine_ws = DEFAULT_WS if DEFAULT_WS != plugin_root else None
    W = 78
    r.out(f"{CYA}╭{'─'*(W-2)}╮{RST}")
    r.out(f"{CYA}│{RST} {BOLD}zcode-bridge executor{RST} · reception queue {DIM}(pure transport · plugin {_plugin_version()}){RST}")
    r.out(f"{CYA}│{RST} default-ws {DIM}{DEFAULT_WS}{RST}  mode {YEL}{DEFAULT_MODE}{RST} · policy {YEL}{DEFAULT_POLICY}{RST}")
    if affine_ws:
        r.out(f"{CYA}│{RST} {GREEN}◈ dedicated window for this workspace{RST}{DIM} — its tickets route here;{RST}")
        r.out(f"{CYA}│{RST} {DIM}other workspaces stay on the catch-all zcode-bridge pane{RST}")
    else:
        r.out(f"{CYA}│{RST} {DIM}catch-all reception · tickets for any workspace no dedicated pane serves{RST}")
    r.out(f"{CYA}│{RST} {DIM}dispatched tickets run in their own workspace (send stamps the caller's cwd){RST}")
    r.out(f"{CYA}│{RST} {DIM}facts reported; acceptance & rework discipline = master's job{RST}")
    r.out(f"{CYA}╰{'─'*(W-2)}╯{RST}")
    report("idle", force=True)
    _r = marker_line("ready")
    if _r:
        r.out(_r)
    if affine_ws:
        def _heartbeat():
            while True:
                try:
                    broker.register_pane(r.pane_id or "repl", affine_ws, os.getpid())
                except Exception:
                    pass
                time.sleep(5)
        threading.Thread(target=_heartbeat, daemon=True).start()
        _retitle(affine_ws)
    r.q = queue.Queue()
    def reader():
        for raw in sys.stdin: r.q.put(raw)
        r.q.put(None)
    threading.Thread(target=reader, daemon=True).start()
    start_disk_pickup(r.q, r.pane_id or "repl", r.busy_active, affine_ws=affine_ws)
    # terminal-holder lock residue (cross-process kills leave it behind) would
    # make every first submit of a workspace queue behind a ghost
    try:
        broker.sweep_workspace_locks()
    except Exception:
        pass
    # 重启/崩溃后,把 owner 已死的 running 任务标成 interrupted(诚实状态),
    # 否则 inspect 永远返回过期状态,调用方无法区分「在跑」和「owner 已死」
    def _reconcile_orphans():
        import glob as _g, json as _json
        store_dir = os.path.expanduser("~/.native-agent-router/tasks")
        for f in _g.glob(os.path.join(store_dir, "t-*.json")):
            try:
                d = _json.load(open(f))
            except Exception:
                continue
            if d.get("status") in ("running", "cancel_requested"):
                try:
                    r.kernel.reconcile(d["task_id"])
                    r.out(f"{DIM}↺ reconcile: {d['task_id']} → interrupted (owner died){RST}")
                except Exception:
                    pass
    threading.Thread(target=_reconcile_orphans, daemon=True).start()
    while True:
        raw = r.q.get()
        if raw is None: break
        r.process(raw)
        _r = marker_line("ready")
        if _r:
            r.out(_r)

if __name__ == "__main__":
    main()
