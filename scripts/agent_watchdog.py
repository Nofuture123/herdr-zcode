#!/usr/bin/env python3
"""Automatic agent registration for ZCode TUI panes (zcode.integration).

herdr 0.9.x identifies agents from a hardcoded Agent table (24 CLIs). The
official ZCode CLI runs as `zcode-cli` and is not in that table, so herdr can
never natively detect a zcode pane: process detection, the HERDR_AGENT env
hint and screen manifests all resolve through the same closed enum. The only
registration path herdr offers is an external `pane report-agent` call.

This watchdog makes that path automatic, for EVERY zcode pane (plugin-opened
or plain `zcode ...` in any pane):

- scans `herdr pane list` on an interval;
- a pane with no agent claim whose foreground process is the zcode CLI is
  claimed (--source zcode-integration --agent zcode) once it has been seen
  twice in a row, and gets a per-pane screen watcher (watch-status.sh) that
  feeds idle/working transitions;
- when the foreground process returns to a shell (TUI quit) the claim is
  released, so no ghost agent is left behind; a pane that disappears is
  reaped outright;
- a claim whose authority was wiped by a server restart is re-claimed.

It claims only panes that carry no agent yet and never touches panes owned by
another source, so a controller's report-agent authority is left alone.

Modes:
  daemon   foreground loop (herdr [[startup]] runs this)
  ensure   start the daemon detached, guarded by a singleton lock
  status   print the daemon lock state
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

SOURCE = "zcode-integration"
AGENT = "zcode"
# The main CLI self-titles argv0 "zcode-cli"; a future bare "zcode" binary is
# accepted too. The node-repl helper child ("zcode-node-repl-mcp") must NOT
# match — one pane is one agent regardless of its helper subprocesses.
ZCODE_PROC_BASENAMES = {"zcode", "zcode-cli"}
# Mirrors herdr's is_pane_shell_process_name: seeing a bare shell in the
# foreground means the TUI has exited (a TUI child like $EDITOR keeps the
# claim alive instead of flapping it).
SHELL_BASENAMES = {
    "sh", "bash", "dash", "zsh", "fish", "ksh", "mksh", "csh", "tcsh",
    "elvish", "xonsh", "nu", "pwsh", "powershell", "cmd",
}

POLL_INTERVAL = float(os.environ.get("ZCODE_WATCHDOG_INTERVAL", "3"))
UNREACHABLE_LIMIT = 10

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WATCH_SH = PLUGIN_ROOT / "scripts" / "watch-status.sh"


def run_dir() -> Path:
    base = os.environ.get("ZCODE_WATCHDOG_RUN_DIR")
    if not base:
        state_home = os.environ.get("XDG_STATE_HOME") or str(
            Path.home() / ".local" / "state"
        )
        base = str(Path(state_home) / "herdr-zcode-integration")
    return Path(base)


def safe_name(pane_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", pane_id)


def augment_path(env: dict) -> dict:
    # herdr runs plugin commands with the server's PATH, which can be a bare
    # system default; mirror common.sh so `herdr`/`zcode` resolve.
    env = dict(env)
    home = env.get("HOME", "")
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", f"{home}/.local/bin"):
        if directory and f":{directory}:" not in f":{env.get('PATH', '')}:":
            env["PATH"] = f"{directory}:{env.get('PATH', '')}"
    return env


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def unwrap(payload: dict) -> dict:
    result = payload.get("result")
    return result if isinstance(result, dict) else payload


class Watchdog:
    def __init__(self, interval: float = POLL_INTERVAL):
        self.interval = interval
        self.rundir = run_dir()
        self.seq_dir = self.rundir / "seq"
        self.claims_dir = self.rundir / "claims"
        self.log_file = self.rundir / "watchdog.log"
        self.lock_path = self.rundir / "daemon.lock"
        self.lock_handle = None
        self.candidates: dict[str, int] = {}
        self.shell_seen: dict[str, int] = {}
        self.release_retries: dict[str, int] = {}
        self.unreachable = 0

    # -- plumbing -----------------------------------------------------------

    def log(self, message: str) -> None:
        try:
            self.rundir.mkdir(parents=True, exist_ok=True)
            if self.log_file.exists() and self.log_file.stat().st_size > 256 * 1024:
                self.log_file.write_text(
                    self.log_file.read_text(errors="replace")[-128 * 1024:]
                )
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            with self.log_file.open("a") as handle:
                handle.write(f"{stamp} {message}\n")
        except OSError:
            pass

    def herdr(self, *args: str, timeout: float = 15.0,
              allow_empty: bool = False) -> dict | None:
        try:
            proc = subprocess.run(
                [herdr_bin(), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=augment_path(os.environ),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log(f"herdr-call-error args={args[:3]} {type(exc).__name__}: {exc}")
            return None
        if proc.returncode != 0:
            self.log(f"herdr-rc{proc.returncode} args={args[:3]} "
                     f"err={proc.stderr.strip()[:200]}")
            return None
        # report-agent / release-agent print nothing on success
        if not proc.stdout.strip():
            return {} if allow_empty else None
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return None

    def next_seq(self, pane_id: str) -> int:
        self.seq_dir.mkdir(parents=True, exist_ok=True)
        path = self.seq_dir / f"{safe_name(pane_id)}.txt"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.read(fd, 32).decode(errors="replace")
            digits = re.sub(r"[^0-9]", "", raw)
            seq = int(digits) + 1 if digits else int(time.time())
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(seq).encode())
            return seq
        finally:
            os.close(fd)

    def report_cmd(self, pane_id: str) -> list[str]:
        # herdr() prepends the binary itself
        return [
            "pane", "report-agent", pane_id,
            "--source", SOURCE, "--agent", AGENT, "--state", "idle",
            "--message", "auto-claimed by zcode.integration watchdog (zcode CLI foreground)",
            "--seq", str(self.next_seq(pane_id)),
        ]

    def release_cmd(self, pane_id: str) -> list[str]:
        return [
            "pane", "release-agent", pane_id,
            "--source", SOURCE, "--agent", AGENT,
            "--seq", str(self.next_seq(pane_id)),
        ]

    # -- claims registry ----------------------------------------------------

    def claim_path(self, pane_id: str) -> Path:
        return self.claims_dir / f"{safe_name(pane_id)}.json"

    def load_claims(self) -> dict[str, dict]:
        claims = {}
        try:
            for path in self.claims_dir.glob("*.json"):
                try:
                    claim = json.loads(path.read_text())
                except (OSError, ValueError):
                    path.unlink(missing_ok=True)
                    continue
                pane_id = claim.get("pane")
                if pane_id:
                    claims[pane_id] = claim
        except OSError:
            pass
        return claims

    def write_claim(self, pane_id: str, watcher_pid: int | None) -> None:
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        self.claim_path(pane_id).write_text(json.dumps({
            "pane": pane_id,
            "watcher_pid": watcher_pid,
            "claimed_at": int(time.time()),
        }))

    def drop_claim(self, pane_id: str) -> None:
        self.claim_path(pane_id).unlink(missing_ok=True)

    # -- watcher lifecycle --------------------------------------------------

    def spawn_watcher(self, pane_id: str) -> int | None:
        if not WATCH_SH.exists():
            return None
        env = augment_path(os.environ)
        env.update({
            "HERDR_PANE_ID": pane_id,
            "HERDR_ENV": "1",
            "HERDR_BIN_PATH": herdr_bin(),
            "ZCODE_WATCHDOG_RUN_DIR": str(self.rundir),
        })
        try:
            proc = subprocess.Popen(
                ["sh", str(WATCH_SH)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return None
        return proc.pid

    def stop_watcher(self, claim: dict) -> None:
        pid = claim.get("watcher_pid")
        if not isinstance(pid, int) or pid <= 0:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass

    def watcher_alive(self, claim: dict) -> bool:
        pid = claim.get("watcher_pid")
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    # -- detection ----------------------------------------------------------

    @staticmethod
    def basename(value: str | None) -> str:
        name = Path(value or "").name.lower()
        # login shells conventionally self-title argv0 with a leading dash
        return name[1:] if name.startswith("-") else name

    @classmethod
    def zcode_foreground(cls, info: dict | None) -> bool:
        if not info:
            return False
        for proc in info.get("foreground_processes") or []:
            values = [v for v in (proc.get("argv0"), proc.get("name")) if v]
            if any(cls.basename(value) in ZCODE_PROC_BASENAMES for value in values):
                return True
        return False

    @classmethod
    def shell_foreground(cls, info: dict | None) -> bool:
        if not info:
            return False
        names = [
            cls.basename(value)
            for proc in info.get("foreground_processes") or []
            for value in (proc.get("argv0"), proc.get("name"))
            if value
        ]
        return bool(names) and all(name in SHELL_BASENAMES for name in names)

    def pane_list(self) -> dict[str, dict] | None:
        payload = self.herdr("pane", "list")
        if payload is None:
            return None
        panes = unwrap(payload).get("panes")
        if not isinstance(panes, list):
            return None
        return {
            pane["pane_id"]: pane
            for pane in panes
            if isinstance(pane, dict) and pane.get("pane_id")
        }

    def process_info(self, pane_id: str) -> dict | None:
        payload = self.herdr("pane", "process-info", "--pane", pane_id)
        if payload is None:
            return None
        result = unwrap(payload)
        info = result.get("process_info")
        return info if isinstance(info, dict) else result

    # -- actions ------------------------------------------------------------

    def claim(self, pane_id: str) -> bool:
        payload = self.herdr(*self.report_cmd(pane_id), allow_empty=True)
        if payload is None:
            self.log(f"claim-failed pane={pane_id}")
            return False
        watcher_pid = self.spawn_watcher(pane_id)
        self.write_claim(pane_id, watcher_pid)
        self.log(f"claimed pane={pane_id} watcher={watcher_pid}")
        return True

    def release(self, pane_id: str, claim: dict) -> None:
        self.stop_watcher(claim)
        self.herdr(*self.release_cmd(pane_id), allow_empty=True)
        self.drop_claim(pane_id)
        self.log(f"released pane={pane_id}")

    # -- main loop ----------------------------------------------------------

    def tick(self) -> None:
        panes = self.pane_list()
        if panes is None:
            self.unreachable += 1
            if self.unreachable >= UNREACHABLE_LIMIT:
                self.log("herdr unreachable; watchdog exiting")
                raise SystemExit(0)
            return
        self.unreachable = 0
        claims = self.load_claims()

        # release / maintain existing claims
        for pane_id, claim in list(claims.items()):
            if pane_id not in panes:
                self.stop_watcher(claim)
                self.drop_claim(pane_id)
                self.log(f"reaped pane={pane_id} (pane gone)")
                continue
            info = self.process_info(pane_id)
            if info is None:
                continue
            if self.zcode_foreground(info):
                self.shell_seen.pop(pane_id, None)
                if not self.watcher_alive(claim):
                    claim["watcher_pid"] = self.spawn_watcher(pane_id)
                    self.write_claim(pane_id, claim.get("watcher_pid"))
                    self.log(f"watcher-respawned pane={pane_id}")
            elif self.shell_foreground(info):
                streak = self.shell_seen.get(pane_id, 0) + 1
                self.shell_seen[pane_id] = streak
                if streak >= 2:
                    self.shell_seen.pop(pane_id, None)
                    self.release(pane_id, claim)
            # other foreground programs (e.g. a TUI child like $EDITOR): hold

        # claim new zcode panes / fix up stale labels
        for pane_id, pane in panes.items():
            if pane_id in claims:
                # claim exists but herdr's authority was wiped (server
                # restart): re-assert while the TUI is still foreground.
                if "agent" not in pane:
                    info = self.process_info(pane_id)
                    if info is not None and self.zcode_foreground(info):
                        self.claim(pane_id)
                continue
            if pane.get("agent") not in (None, AGENT):
                self.candidates.pop(pane_id, None)
                self.release_retries.pop(pane_id, None)
                continue  # a foreign agent owns this pane
            info = self.process_info(pane_id)
            if info is not None and self.zcode_foreground(info):
                if pane.get("agent") == AGENT:
                    # labeled zcode already (earlier integration run): adopt
                    # it so state feeding and release are owned from here on
                    self.release_retries.pop(pane_id, None)
                    self.write_claim(pane_id, self.spawn_watcher(pane_id))
                    self.log(f"adopted pane={pane_id}")
                    continue
                streak = self.candidates.get(pane_id, 0) + 1
                self.candidates[pane_id] = streak
                if streak >= 2:  # skip `zcode --version`-style foreground blips
                    self.candidates.pop(pane_id, None)
                    self.claim(pane_id)
                continue
            self.candidates.pop(pane_id, None)
            if pane.get("agent") == AGENT:
                # labeled zcode but zcode not foreground: the release raced a
                # watcher report and was rejected as stale (report/release
                # exit 0 hide rejection), or an old integration left the
                # label. Re-release with a fresh seq, bounded.
                retries = self.release_retries.get(pane_id, 0) + 1
                self.release_retries[pane_id] = retries
                if retries <= 3:
                    self.herdr(*self.release_cmd(pane_id), allow_empty=True)
                    self.log(f"re-released pane={pane_id} (attempt {retries})")
                else:
                    self.log(f"release-gave-up pane={pane_id}")
            else:
                self.release_retries.pop(pane_id, None)

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        self.acquire_lock()
        self.log(f"watchdog started interval={self.interval}s")
        print(f"zcode.integration watchdog started (interval {self.interval}s)", flush=True)
        while True:
            try:
                self.tick()
            except SystemExit:
                raise
            except Exception as exc:  # never let one bad tick kill the daemon
                self.log(f"tick-error {type(exc).__name__}: {exc}")
            time.sleep(self.interval)

    # -- singleton lock -----------------------------------------------------

    def acquire_lock(self) -> None:
        self.rundir.mkdir(parents=True, exist_ok=True)
        self.lock_handle = self.lock_path.open("w")
        try:
            fcntl.flock(self.lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("watchdog already running", flush=True)
            raise SystemExit(0)
        self.lock_handle.write(str(os.getpid()))
        self.lock_handle.flush()

    def lock_held(self) -> int | None:
        try:
            handle = self.lock_path.open("r")
        except OSError:
            return None
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raw = handle.read(32).strip()
            return int(raw) if raw.isdigit() else -1
        finally:
            handle.close()
        return None


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "daemon"
    watchdog = Watchdog()
    if mode == "daemon":
        watchdog.run_forever()
    elif mode == "ensure":
        if not WATCH_SH.exists():
            print(f"watcher script missing: {WATCH_SH}", file=sys.stderr)
            return 1
        holder = watchdog.lock_held()
        if holder is not None:
            print(f"watchdog already running (pid {holder})")
            return 0
        watchdog.rundir.mkdir(parents=True, exist_ok=True)
        log = open(watchdog.log_file, "ab")
        try:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "daemon"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True,
            )
        finally:
            log.close()
        print("watchdog started")
        return 0
    elif mode == "status":
        holder = watchdog.lock_held()
        print(f"running (pid {holder})" if holder is not None else "not running")
        return 0 if holder is not None else 1
    elif mode == "next-seq":
        # atomic per-pane report counter shared by the daemon and the shell
        # watcher — herdr rejects non-increasing --seq per source, so every
        # writer must go through this locked increment
        if len(argv) < 3:
            print("usage: next-seq <pane_id>", file=sys.stderr)
            return 2
        print(watchdog.next_seq(argv[2]))
        return 0
    elif mode == "once":
        watchdog.tick()
        return 0
    print(f"usage: {Path(__file__).name} daemon|ensure|status|once", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
