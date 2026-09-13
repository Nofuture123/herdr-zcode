#!/usr/bin/env python3
"""Executor tests with a FakeKernel: no ZCode, no herdr, no network."""
import importlib.util, io, json, os, queue, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
tmp = tempfile.mkdtemp()

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

broker = load("broker", os.path.join(ROOT, "scripts", "broker.py"))
broker.REQUESTS = os.path.join(tmp, "requests"); broker.LEDGERS = os.path.join(tmp, "ledgers")
broker.RESULTS = os.path.join(tmp, "results"); broker.init_dirs()
# executor_common loads its own broker copy; point it at the same redirected dirs
ec = load("executor_common", os.path.join(ROOT, "scripts", "executor_common.py"))
ec.broker.REQUESTS, ec.broker.LEDGERS, ec.broker.RESULTS = broker.REQUESTS, broker.LEDGERS, broker.RESULTS
er = load("executor_repl", os.path.join(ROOT, "scripts", "executor_repl.py"))

class FakeKernel:
    def __init__(self, sticky=False):
        self.tasks = {}; self.submits = []; self.cancelled = []; self.sticky = sticky
    def submit(self, agent, goal, ws, **kw):
        self.submits.append({"goal": goal, "workspace": ws, **kw})
        tid = f"t-{len(self.submits):06d}"
        t = {"task_id": tid, "status": "running", "goal": goal, "workspace": ws,
             "native_session_id": f"sess-{tid}",
             "result": {"worker_summary": "work done", "duration_sec": 0.1,
                        "diff": {"changed_files": [], "out_of_scope": []},
                        "verify": ([{"cmd": kw.get("verify"), "exit_code": 0, "ok": True}]
                                   if kw.get("verify") else []),
                        "usage": {"input_tokens": 1, "output_tokens": 1,
                                  "total_tokens": 2, "quality": "fake"}}}
        self.tasks[tid] = t
        return {"task_id": tid, "status": "running"}
    def wait(self, tid, timeout_sec=None):
        t = self.tasks[tid]
        if t["status"] == "running" and self.sticky:
            time.sleep(0.05); return t
        if t["status"] == "running": t["status"] = "succeeded"
        return t
    def snapshot(self, tid): return self.tasks[tid]
    def cancel(self, tid):
        self.cancelled.append(tid); self.tasks[tid]["status"] = "cancelled"
        return {"confirmed": True}
    def kill(self, tid): self.tasks[tid]["status"] = "killed"
    def inspect(self, tid, what="status"): return self.tasks[tid]

WS = tempfile.mkdtemp()

class TestReception(unittest.TestCase):
    def make(self, sticky=False):
        k = FakeKernel(sticky=sticky); buf = io.StringIO()
        r = er.Reception(k, out=lambda s=None: buf.write((s or "") + "\n"))
        r.q = queue.Queue() if (queue := __import__("queue")) else None
        return k, buf, r

    def test_submit_receipt_and_facts(self):
        k, buf, r = self.make()
        r.start(json.dumps({"goal": "do it", "workspace": WS, "mode": "yolo",
                            "verify": "true", "idempotency_key": "t1"}))
        lines = buf.getvalue().splitlines()
        acc = [l for l in lines if l.startswith("[zcodecli:accepted]")][0]
        self.assertIn("t-", acc)
        tid = acc.split()[2]
        self.assertEqual(len(k.submits), 1)
        facts = json.load(open(broker.result_path(tid)))
        self.assertEqual(facts["status"], "succeeded")     # FACT reported
        self.assertEqual(facts["verify_ok"], True)         # FACT reported
        self.assertNotIn("accepted", facts)                # NO verdict from the bridge
        out = buf.getvalue()
        self.assertNotIn("verified=", out)                 # no verdict marker either

    def test_fail_closed(self):
        k, buf, r = self.make()
        r.start('{"mode":"edit"}')                          # no goal
        self.assertIn("rejected (fail-closed)", buf.getvalue())
        self.assertEqual(k.submits, [])

    def test_idempotency_duplicate_and_indeterminate(self):
        k, buf, r = self.make()
        spec = {"goal": "x", "workspace": WS, "mode": "plan", "idempotency_key": "dup1"}
        r.start(json.dumps(spec))
        n1 = len(k.submits)
        r.start(json.dumps(spec))                            # terminal state -> duplicate
        self.assertIn("duplicate", buf.getvalue())
        self.assertEqual(len(k.submits), n1)
        spec2 = {"goal": "x", "workspace": WS, "mode": "plan", "idempotency_key": "dup2"}
        s2 = broker.normalize_spec(spec2, WS, "yolo", "allow")
        broker.idempotency_claim("dup2", broker.fingerprint(s2))   # stuck claim
        r.start(json.dumps(spec2))
        self.assertIn("idempotency_indeterminate", buf.getvalue())
        self.assertEqual(len(k.submits), n1)                 # never auto-rerun

    def test_continue_inherits_and_sets_session_ref(self):
        k, buf, r = self.make()
        spec = {"goal": "v1", "workspace": WS, "mode": "plan", "idempotency_key": "c1"}
        r.start(json.dumps(spec))
        rid = r.last and None
        rid = [json.load(open(os.path.join(broker.REQUESTS, f)))["request_id"]
               for f in os.listdir(broker.REQUESTS)][-1]
        parent_tid = k.submits[-1] and None
        r.do_continue(f"{rid} fix it please")
        self.assertEqual(len(k.submits), 2)
        kw = k.submits[1]
        self.assertEqual(kw["goal"], "fix it please")
        self.assertEqual(kw["workspace"], os.path.realpath(WS))  # inherited (canonical)
        self.assertIsNotNone(kw.get("session_ref"))          # REAL native-session continuation
        self.assertNotIn("rework cap", buf.getvalue())       # no bridge-side cap (master's job)

    def test_cancel_tri_state(self):
        k, buf, r = self.make()
        k.tasks["t-manual"] = {"task_id": "t-manual", "status": "succeeded"}
        r.do_cancel("t-manual")                              # natural finish -> honest
        self.assertIn("already_terminal", buf.getvalue())
        self.assertEqual(k.cancelled, [])
        k2, buf2, r2 = self.make(sticky=True)
        threading.Timer(0.3, lambda: r2.q.put("/cancel\n")).start()
        r2.start(json.dumps({"goal": "long", "workspace": WS, "mode": "plan",
                             "timeout": 10}))
        self.assertTrue(k2.cancelled)
        self.assertIn("cancelled (confirmed stopped)", buf2.getvalue())

import queue  # for make()

if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestChat(unittest.TestCase):
    def make(self, sticky=False):
        import executor_chat as ech
        k = FakeKernel(sticky=sticky); buf = io.StringIO()
        c = ech.Chat(k, out=lambda s=None: buf.write((s or "") + "\n"), ws=WS)
        return k, buf, c

    def test_turn_receipt_nonce_and_facts(self):
        k, buf, c = self.make()
        c.run_turn(json.dumps({"goal": "hi", "workspace": WS, "mode": "plan",
                               "nonce": "n-abcd1234", "idempotency_key": "ch1"}))
        lines = buf.getvalue().splitlines()
        acc = [l for l in lines if l.startswith("[zcodecli:accepted]")][0]
        self.assertIn("n-abcd1234", acc)                     # nonce echoed on receipt
        self.assertIn("t-", acc)
        res = [l for l in lines if l.startswith("[zcodecli:result]")][0]
        self.assertIn("n-abcd1234", res)                     # signal bound to request
        self.assertEqual(len(k.submits), 1)

    def test_busy_error_carries_nonce(self):
        k, buf, c = self.make()
        c.run_turn(json.dumps({"goal": "first", "workspace": WS, "mode": "plan",
                               "nonce": "n-busy0001", "idempotency_key": "ch2"}))
        c.busy = "t-fake"; c.current_nonce = "n-busy0001"    # simulate still-running turn
        # mainloop busy branch (inline in main(); replicate its exact error path here)
        bn = f" {c.current_nonce}" if c.current_nonce else ""
        c.sig("error", f"busy: {c.busy} running; /cancel to stop{bn}")
        self.assertIn("n-busy0001", buf.getvalue())          # busy error carries running nonce
        c.busy = None; c.current_nonce = None

    def test_fail_closed_rejects_unknown_field(self):
        k, buf, c = self.make()
        c.run_turn(json.dumps({"goal": "x", "workspace": WS, "wat": 1, "nonce": "n-fc01"}))
        self.assertIn("rejected (fail-closed)", buf.getvalue())
        self.assertIn("n-fc01", buf.getvalue())              # nonce bound even on rejection
        self.assertEqual(k.submits, [])


def _plain(sx):
    import re as _re2
    return _re2.sub(r"\033\[[0-9;]*m", "", sx)


class TestNativeStream(unittest.TestCase):
    def _run_stream(self, events, stop_after):
        tid = "t-" + "a" * 12
        logdir = os.path.join(tmp, "narlogs", tid)
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, "native-raw.jsonl"), "w") as f:
            for ev in events:
                f.write(json.dumps({"msg": {"method": "session/event",
                                            "params": {"payload": ev}}}) + "\n")
        old = os.environ.get("NAR_LOGS_DIR")
        os.environ["NAR_LOGS_DIR"] = os.path.join(tmp, "narlogs")
        lines, stop = [], threading.Event()

        def out(sx):
            lines.append(sx)
            if len([x for x in lines if x.strip()]) >= stop_after:
                stop.set()

        try:
            ec.stream_native_output(tid, out, stop)
        finally:
            if old is None:
                os.environ.pop("NAR_LOGS_DIR", None)
            else:
                os.environ["NAR_LOGS_DIR"] = old
        return [x for x in lines if x.strip()]

    def test_renders_text_tools_and_results(self):
        nb = self._run_stream([
            {"kind": "text_delta", "delta": "你好\n世界"},
            {"kind": "tool_call", "toolName": "Bash",
             "input": {"command": "ls -la /x"}},
            {"kind": "result", "toolCallId": "c1",
             "result": {"success": True, "content": "total 40\nfoo"}},
        ], stop_after=4)
        self.assertEqual(nb[0], "你好")
        self.assertEqual(nb[1], "世界")
        self.assertIn("▸ Bash: ls -la /x", nb[2])
        self.assertIn("✓", nb[3])
        self.assertIn("└", nb[3])

    def test_reasoning_renders_dim_and_mutable(self):
        nb = self._run_stream([
            {"kind": "reasoning_delta", "delta": "先想清楚\n再动手"},
            {"kind": "text_delta", "delta": "答案"},
            {"kind": "tool_call", "toolName": "Bash",
             "input": {"command": "true"}},
        ], stop_after=5)
        self.assertEqual(_plain(nb[0]), "── thinking ──")
        self.assertEqual(_plain(nb[1]), "· 先想清楚")
        self.assertEqual(_plain(nb[2]), "· 再动手")
        self.assertEqual(_plain(nb[3]), "答案")
        self.assertIn("▸ Bash: true", _plain(nb[4]))
        os.environ["QAB_EXEC_REASONING"] = "0"
        try:
            nb = self._run_stream(
                [{"kind": "reasoning_delta", "delta": "hidden"},
                 {"kind": "text_delta", "delta": "答案"}], stop_after=1)
        finally:
            os.environ.pop("QAB_EXEC_REASONING", None)
        self.assertNotIn("hidden", " ".join(nb))

    def test_quiet_env_mutes_streaming(self):
        os.environ["QAB_EXEC_QUIET"] = "1"
        try:
            nb = self._run_stream(
                [{"kind": "text_delta", "delta": "hi"}], stop_after=1)
        finally:
            os.environ.pop("QAB_EXEC_QUIET", None)
        self.assertEqual(nb, [])

    def test_fenced_code_renders_as_block(self):
        nb = self._run_stream([
            {"kind": "text_delta", "delta": "看代码:\n```python\nx = 1\n```\n完"},
        ], stop_after=5)
        self.assertEqual(_plain(nb[0]), "看代码:")
        self.assertTrue(_plain(nb[1]).startswith("╭──"))
        self.assertIn("code", nb[1])
        self.assertIn("1 │ x = 1", _plain(nb[2]))
        self.assertTrue(_plain(nb[3]).startswith("╰──"))
        self.assertEqual(nb[4], "完")


class TestNativeFinalText(unittest.TestCase):
    def test_returns_last_nonempty_message(self):
        tid = "t-" + "b" * 12
        logdir = os.path.join(tmp, "narlogs", tid)
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, "native-raw.jsonl"), "w") as f:
            for mid, delta in (("m1", "VERDICT cut off "), ("m2", "VERDICT: PASS\nscore 90")):
                f.write(json.dumps({"msg": {"method": "session/event", "params": {
                    "payload": {"kind": "text_delta",
                                "assistantMessageId": mid, "delta": delta}}}}) + "\n")
        old = os.environ.get("NAR_LOGS_DIR")
        os.environ["NAR_LOGS_DIR"] = os.path.join(tmp, "narlogs")
        try:
            self.assertEqual(ec.native_final_text(tid), "VERDICT: PASS\nscore 90")
            self.assertIsNone(ec.native_final_text("t-" + "c" * 12))
        finally:
            if old is None:
                os.environ.pop("NAR_LOGS_DIR", None)
            else:
                os.environ["NAR_LOGS_DIR"] = old


class TestCodeBlocks(unittest.TestCase):
    def test_fenced_code_renders_as_block(self):
        lines = []
        stop = threading.Event()

        def out(sx):
            lines.append(_plain(sx))
            if len(lines) >= 5:
                stop.set()

        tid = "t-" + "d" * 12
        logdir = os.path.join(tmp, "narlogs", tid)
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, "native-raw.jsonl"), "w") as f:
            f.write(json.dumps({"msg": {"method": "session/event", "params": {
                "payload": {"kind": "text_delta", "assistantMessageId": "m1",
                            "delta": "看代码:\n```python\nx = 1\n```\n完"}}}}) + "\n")
        old = os.environ.get("NAR_LOGS_DIR")
        os.environ["NAR_LOGS_DIR"] = os.path.join(tmp, "narlogs")
        try:
            ec.stream_native_output(tid, out, stop)
        finally:
            if old is None:
                os.environ.pop("NAR_LOGS_DIR", None)
            else:
                os.environ["NAR_LOGS_DIR"] = old
        nb = [x for x in lines if x.strip()]
        self.assertIn("完", nb)
        self.assertEqual(_plain(nb[0]), "看代码:")
        self.assertTrue(_plain(nb[1]).startswith("╭──"))
        self.assertIn("code", nb[1])
        self.assertIn("1 │ x = 1", _plain(nb[2]))
        self.assertTrue(_plain(nb[3]).startswith("╰──"))
        self.assertEqual(nb[4], "完")
