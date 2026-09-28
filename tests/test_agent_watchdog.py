#!/usr/bin/env python3
"""Unit tests for the agent watchdog (no herdr server, no zcode process)."""
import importlib.util, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("agent_watchdog", os.path.join(
    os.path.dirname(__file__), "..", "scripts", "agent_watchdog.py"))
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)


def make_wd() -> aw.Watchdog:
    tmp = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {"ZCODE_WATCHDOG_RUN_DIR": tmp}):
        wd = aw.Watchdog(interval=0)
    wd.rundir = Path(tmp)
    wd.seq_dir = wd.rundir / "seq"
    wd.claims_dir = wd.rundir / "claims"
    wd.log_file = wd.rundir / "watchdog.log"
    wd.lock_path = wd.rundir / "daemon.lock"
    return wd


def procinfo(*procs):
    return {"foreground_process_group_id": procs[0][0] if procs else 0,
            "foreground_processes": [
                {"pid": pid, "name": name, "argv0": argv0} for pid, name, argv0 in procs
            ]}


class TestProcessDetection(unittest.TestCase):
    # Shape recorded in the astra handoff: main CLI self-titled zcode-cli plus
    # its node-repl helper child.
    HANDOFF = procinfo((69897, "node", "zcode-cli"), (69950, "node", "zcode-node-repl-mcp"))

    def test_main_cli_detected(self):
        self.assertTrue(aw.Watchdog.zcode_foreground(self.HANDOFF))

    def test_helper_child_alone_is_not_zcode(self):
        self.assertFalse(aw.Watchdog.zcode_foreground(
            procinfo((69950, "node", "zcode-node-repl-mcp"))))

    def test_shell_is_not_zcode(self):
        self.assertFalse(aw.Watchdog.zcode_foreground(procinfo((10, "zsh", "-zsh"))))

    def test_bare_zcode_binary_detected(self):
        self.assertTrue(aw.Watchdog.zcode_foreground(
            procinfo((1, "zcode", "/Users/x/.local/bin/zcode"))))

    def test_empty_and_none_inputs(self):
        self.assertFalse(aw.Watchdog.zcode_foreground(None))
        self.assertFalse(aw.Watchdog.zcode_foreground({}))

    def test_shell_detection(self):
        self.assertTrue(aw.Watchdog.shell_foreground(procinfo((1, "zsh", "-zsh"))))
        self.assertFalse(aw.Watchdog.shell_foreground(procinfo((1, "node", "zcode-cli"))))
        self.assertFalse(aw.Watchdog.shell_foreground(
            procinfo((1, "zsh", "zsh"), (2, "node", "zcode-cli"))))  # mixed: hold
        self.assertFalse(aw.Watchdog.shell_foreground(None))


class TestSeqCounter(unittest.TestCase):
    def test_monotonic_and_shared_file(self):
        wd = make_wd()
        a = wd.next_seq("wX:pY")
        b = wd.next_seq("wX:pY")
        self.assertGreater(b, a)
        # same file backs the shell watcher's counter
        seq_file = wd.seq_dir / "wX_pY.txt"
        self.assertTrue(seq_file.exists())

    def test_pane_names_are_file_safe(self):
        wd = make_wd()
        wd.next_seq("w8Z:p8E/../etc")
        self.assertNotIn("/", " ".join(p.name for p in wd.seq_dir.glob("*.txt")))


class TestUnwrapAndClaims(unittest.TestCase):
    def test_unwrap_envelope(self):
        self.assertEqual(aw.unwrap({"id": "x", "result": {"panes": 1}}), {"panes": 1})
        self.assertEqual(aw.unwrap({"panes": 1}), {"panes": 1})

    def test_process_info_envelope(self):
        wd = make_wd()
        with mock.patch.object(wd, "herdr", return_value={
            "id": "cli:pane:process_info",
            "result": {"process_info": {"foreground_processes": [
                {"pid": 1, "name": "node", "argv0": "zcode-cli"}]},
                "type": "pane_process_info"}}):
            info = wd.process_info("wX:p1")
        self.assertTrue(aw.Watchdog.zcode_foreground(info))

    def test_claim_roundtrip(self):
        wd = make_wd()
        wd.write_claim("wX:p1", 4242)
        self.assertEqual(wd.load_claims(), {"wX:p1": {
            "pane": "wX:p1", "watcher_pid": 4242,
            "claimed_at": wd.load_claims()["wX:p1"]["claimed_at"]}})
        wd.drop_claim("wX:p1")
        self.assertEqual(wd.load_claims(), {})

    def test_corrupt_claim_ignored(self):
        wd = make_wd()
        wd.claims_dir.mkdir(parents=True)
        (wd.claims_dir / "bad.json").write_text("{not json")
        self.assertEqual(wd.load_claims(), {})
        self.assertFalse((wd.claims_dir / "bad.json").exists())


class TestCommands(unittest.TestCase):
    def test_report_and_release_shape(self):
        wd = make_wd()
        with mock.patch.object(aw, "herdr_bin", return_value="herdr"):
            report = wd.report_cmd("wX:p1")
            release = wd.release_cmd("wX:p1")
        # herdr() prepends the binary itself — command must NOT include it
        self.assertEqual(report[:4], ["pane", "report-agent", "wX:p1", "--source"])
        self.assertIn("--agent", report)
        self.assertEqual(report[report.index("--agent") + 1], "zcode")
        self.assertIn("--state", report)
        self.assertEqual(report[report.index("--state") + 1], "idle")
        self.assertEqual(release[:3], ["pane", "release-agent", "wX:p1"])
        self.assertNotIn("--state", release)


if __name__ == "__main__":
    unittest.main()
