#!/usr/bin/env python3
"""Request broker for zcode-bridge executors.

Owns the trust boundary: request lifecycle, atomic disk records, cross-process
idempotency, and the ONLY sanctioned way to turn a task_id into a result path.
Markers on a terminal are signals; files here are evidence.
"""
import hashlib, hmac, json, os, re, secrets, tempfile, time
try:
    import fcntl
    def _lock_ex(f):
        fcntl.flock(f, fcntl.LOCK_EX)
    def _unlock(f):
        fcntl.flock(f, fcntl.LOCK_UN)
except ImportError:          # Windows: ledger writes are atomic-replace anyway
    def _lock_ex(f):
        pass
    def _unlock(f):
        pass

BASE = os.path.expanduser("~/.local/share/herdr-zcode")
REQUESTS = os.path.join(BASE, "requests")
LEDGERS = os.path.join(BASE, "ledgers")
RESULTS = os.path.join(BASE, "results")
OWNERS = os.path.join(BASE, "owners")
RECEIPTS = os.path.join(BASE, "receipts")
PANES = os.path.join(BASE, "panes")
TASK_RE = re.compile(r"^t-[0-9a-f]+$")
REQ_RE = re.compile(r"^r-[0-9a-f]{16}$")
NONCE_RE = re.compile(r"^[0-9a-zA-Z_-]{1,64}$")
ALLOWED_KEYS = {"goal", "workspace", "mode", "scope", "verify", "policy",
                "timeout", "idempotency_key", "session_ref", "forbid", "nonce",
                "request_id"}
MODES = {"plan", "build", "edit", "yolo"}
POLICIES = {"allow", "deny"}


def _secure(path, dir=False):
    os.makedirs(path, 0o700 if dir else os.path.dirname(path), exist_ok=True) if dir else None
    if dir:
        os.chmod(path, 0o700)


def init_dirs():
    for d in (REQUESTS, LEDGERS, RESULTS, OWNERS, RECEIPTS, PANES):
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if os.path.isfile(fp):
                os.chmod(fp, 0o600)


class SpecError(ValueError):
    pass


def normalize_spec(obj, default_ws, default_mode="yolo", default_policy="allow",
                   require_verify=None):
    """Fail-closed spec normalization. Returns spec dict; raises SpecError."""
    if not isinstance(obj, dict):
        raise SpecError("task must be a JSON object")
    unknown = set(obj) - ALLOWED_KEYS
    if unknown:
        raise SpecError(f"unknown field(s): {sorted(unknown)}")
    goal = obj.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise SpecError("goal is required and must be a non-empty string")
    ws = obj.get("workspace", default_ws)
    if not isinstance(ws, str) or not os.path.isdir(ws):
        raise SpecError(f"workspace is not a directory: {ws!r}")
    ws = os.path.realpath(ws)   # one canonical form everywhere: locks, sessions, JSON
    mode = obj.get("mode", default_mode)
    if mode not in MODES:
        raise SpecError(f"mode must be one of {sorted(MODES)}")
    policy = obj.get("policy", default_policy)
    if policy not in POLICIES:
        raise SpecError(f"policy must be one of {sorted(POLICIES)}")
    scope = obj.get("scope", [])
    if not isinstance(scope, list) or any(not isinstance(x, str) for x in scope):
        raise SpecError("scope must be a list of relative paths")
    verify = obj.get("verify", [])
    if isinstance(verify, str):
        verify = [verify]
    if not isinstance(verify, list) or any(not isinstance(v, str) or not v.strip() for v in verify):
        raise SpecError("verify must be a non-empty command string or list")
    if require_verify and mode in ("edit", "yolo") and not verify:
        raise SpecError(f"verify is required for {mode} tasks (fail closed)")
    timeout = obj.get("timeout", 600)
    if not isinstance(timeout, int) or not (10 <= timeout <= 7200):
        raise SpecError("timeout must be integer seconds in [10, 7200]")
    ikey = obj.get("idempotency_key")
    if ikey is not None and (not isinstance(ikey, str) or not ikey.strip()):
        raise SpecError("idempotency_key must be a non-empty string")
    sref = obj.get("session_ref")
    if sref is not None and not isinstance(sref, str):
        raise SpecError("session_ref must be a string task_id")
    nonce = obj.get("nonce")
    if nonce is not None and not isinstance(nonce, str):
        raise SpecError("nonce must be a string")
    rid = obj.get("request_id")
    if rid is not None and not REQ_RE.match(rid or ""):
        raise SpecError(f"bad request_id: {rid!r}")
    out = {"goal": goal, "workspace": ws, "mode": mode, "scope": scope,
           "verify": verify, "policy": policy, "timeout": timeout,
           "idempotency_key": ikey, "session_ref": sref, "nonce": nonce}
    if rid:
        out["request_id"] = rid
    return out


def fingerprint(spec):
    """Stable identity of the WORK, not the delivery: transport fields
    (request_id, nonce) are excluded so a retried send with the same
    idempotency_key lands as "duplicate", never as a false "conflict"."""
    body = {k: v for k, v in spec.items() if k not in ("request_id", "nonce")}
    canon = json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def new_request_id():
    return "r-" + secrets.token_hex(8)


def save_request(request_id, spec, extra=None):
    if not REQ_RE.match(request_id):
        raise ValueError("bad request_id")
    rec = {"request_id": request_id, "spec": spec, "ts": time.time(),
           "fingerprint": fingerprint(spec)}
    if extra:
        rec.update(extra)
    fd, tmp = tempfile.mkstemp(dir=REQUESTS, prefix="tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, ensure_ascii=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(REQUESTS, request_id + ".json"))
    return rec


def attach_task(request_id, task_id):
    if not (REQ_RE.match(request_id or "") and TASK_RE.match(task_id or "")):
        raise ValueError("bad id")
    p = os.path.join(REQUESTS, request_id + ".json")
    rec = json.load(open(p))
    rec["task_id"] = task_id
    fd, tmp = tempfile.mkstemp(dir=REQUESTS, prefix="tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, ensure_ascii=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def load_request(request_id):
    if not REQ_RE.match(request_id or ""):
        raise ValueError("bad request_id")
    return json.load(open(os.path.join(REQUESTS, request_id + ".json")))


def find_request_by_task(task_id):
    if not TASK_RE.match(task_id or ""):
        return None
    for f in os.listdir(REQUESTS):
        if not f.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(REQUESTS, f)))
        except Exception:
            continue
        if rec.get("task_id") == task_id:
            return rec
    return None


def claim_request(request_id, pane_id):
    """Exclusive delivery claim for a client-written request file.

    send 落盘 request 后,任何 executor 都可能把它领走(谁闲谁领, pane 死了票也不
    丢)。单一 claim 文件、内容记领取者:返回 "new"(本 pane 领取成功)/"mine"
    (本 pane 已领过)/"taken"(别的 pane 已领,本次投递作废)。
    """
    if not REQ_RE.match(request_id or ""):
        return "taken"
    safe = re.sub(r"[^A-Za-z0-9]", "", pane_id or "pane") or "pane"
    claimp = os.path.join(REQUESTS, f"{request_id}.claim")
    try:
        fd = os.open(claimp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, (safe + "\n").encode())
        os.close(fd)
        return "new"
    except FileExistsError:
        try:
            with open(claimp) as f:
                return "mine" if f.read().strip() == safe else "taken"
        except OSError:
            return "taken"
    except OSError:
        return "taken"


def unclaimed_requests():
    """Client-written, still-unclaimed disk tickets, oldest first.

    只有票面显式带 request_id 的才算磁盘投递票(opt-in)——executor 自己落盘的
    历史票没有这个字段,结构性排除,扫描线程绝不重跑历史。
    """
    out = []
    try:
        names = os.listdir(REQUESTS)
    except OSError:
        return out
    for f in sorted(names):
        if not f.endswith(".json"):
            continue
        rid = f[:-5]
        if not REQ_RE.match(rid):
            continue
        if f"{rid}.claim" in names:
            continue
        if os.path.exists(os.path.join(REQUESTS, rid + ".cancel")):
            continue
        try:
            rec = json.load(open(os.path.join(REQUESTS, f)))
        except Exception:
            continue
        if not (rec.get("spec") or {}).get("request_id"):
            continue
        out.append(os.path.join(REQUESTS, f))
    return out


def result_path(task_id):
    if not TASK_RE.match(task_id or ""):
        raise ValueError(f"invalid task_id: {task_id!r}")
    return os.path.join(RESULTS, task_id + ".json")


def idempotency_claim(key, fp):
    """Returns (status, record): status in {"new","duplicate","conflict"}.
    duplicate -> record has request_id of the ORIGINAL request.
    A key whose previous attempt reached a TERMINAL state starts fresh: the
    key dedups ONE submission, not all future ones (same key after terminal
    = a new task, per the field report)."""
    if not key:
        return "new", None
    h = hashlib.sha256(key.encode()).hexdigest()[:24]
    lockp = os.path.join(LEDGERS, h + ".lock")
    recp = os.path.join(LEDGERS, h + ".json")
    with open(lockp, "w") as lockf:
        _lock_ex(lockf)
        try:
            if os.path.exists(recp):
                rec = json.load(open(recp))
                if rec.get("state") == "terminal":
                    os.remove(recp)          # consumed: the key is free again
                elif hmac.compare_digest(rec.get("fingerprint", ""), fp):
                    if rec.get("state") in ("claimed", "submitting") or (
                            rec.get("state") == "attached" and not rec.get("task_id")):
                        return "indeterminate", rec   # previous owner died mid-flight; NEVER auto-rerun
                    return "duplicate", rec
                else:
                    return "conflict", rec
            rid = new_request_id()
            rec = {"key": key, "fingerprint": fp, "request_id": rid,
                   "task_id": None, "state": "claimed"}
            fd, tmp = tempfile.mkstemp(dir=LEDGERS, prefix="tmp-")
            with os.fdopen(fd, "w") as f:
                json.dump(rec, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, recp)
            return "new", rec
        finally:
            _unlock(lockf)


def idempotency_release(key):
    """Drop the ledger record so the same key can be legally resubmitted.

    ONLY for tasks that never started executing (e.g. rejected with
    workspace_busy) — a released key loses its once-only guarantee.
    """
    if not key:
        return
    h = hashlib.sha256(key.encode()).hexdigest()[:24]
    for suffix in (".json", ".lock"):
        try:
            os.remove(os.path.join(LEDGERS, h + suffix))
        except FileNotFoundError:
            pass


def idempotency_state(key, fp, state, task_id=None, request_id=None):
    """Transition ledger state: claimed -> submitting -> attached -> terminal."""
    if not key:
        return
    h = hashlib.sha256(key.encode()).hexdigest()[:24]
    recp = os.path.join(LEDGERS, h + ".json")
    lockp = os.path.join(LEDGERS, h + ".lock")
    with open(lockp, "w") as lockf:
        _lock_ex(lockf)
        try:
            rec = json.load(open(recp)) if os.path.exists(recp) else {}
            if request_id and rec.get("request_id") != request_id:
                return
            rec["state"] = state
            if task_id is not None:
                rec["task_id"] = task_id
            fd, tmp = tempfile.mkstemp(dir=LEDGERS, prefix="tmp-")
            with os.fdopen(fd, "w") as f:
                json.dump(rec, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, recp)
        finally:
            _unlock(lockf)


def idempotency_attach(key, fp, request_id, task_id):
    idempotency_state(key, fp, "attached", task_id=task_id, request_id=request_id)


def idempotency_terminal(key, fp):
    idempotency_state(key, fp, "terminal")


def set_owner(task_id, pane_id):
    """task -> owner pane mapping so cancels can be routed to the right pane."""
    if not TASK_RE.match(task_id or "") or not pane_id:
        return
    fd, tmp = tempfile.mkstemp(dir=OWNERS, prefix="tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump({"task_id": task_id, "pane_id": pane_id, "ts": time.time()}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(OWNERS, task_id + ".json"))


def owner_of(task_id):
    if not TASK_RE.match(task_id or ""):
        return None
    p = os.path.join(OWNERS, task_id + ".json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p)).get("pane_id")
    except Exception:
        return None


# ---------- pane registry: per-workspace visible executors ----------
# 每张票一个可见窗口的落点:workspace 专属 pane(`zcodecli open --cwd <wt>`)启动后
# 定期写心跳,send / 磁盘领取据此把票路由到对应 pane——可见性,不改变串行语义。

PANE_HEARTBEAT_TTL = 15   # seconds; writers refresh every 5

def _pane_alive(pid):
    """Liveness by signal-0 probe. POSIX only: on Windows os.kill(pid, 0)
    TERMINATES the process instead of probing, so there we trust the
    heartbeat freshness alone."""
    if not pid or os.name == "nt":
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False

def register_pane(pane_id, workspace, pid):
    """Write/refresh this executor's heartbeat. workspace=None marks the
    catch-all reception pane (claims anything); a concrete workspace marks a
    dedicated pane that only wants tickets for that workspace."""
    safe = re.sub(r"[^A-Za-z0-9]", "", pane_id or "pane") or "pane"
    rec = {"pane_id": pane_id, "pid": pid,
           "workspace": os.path.realpath(workspace) if workspace else None,
           "ts": time.time()}
    fd, tmp = tempfile.mkstemp(dir=PANES, prefix="tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(PANES, safe + ".json"))

def affine_pane_for(workspace, fresh=PANE_HEARTBEAT_TTL):
    """Live executor pane dedicated to this workspace, else None.
    Live = heartbeat written within `fresh` seconds AND its pid still exists."""
    if not workspace:
        return None
    ws = os.path.realpath(workspace)
    try:
        names = os.listdir(PANES)
    except OSError:
        return None
    now = time.time()
    hit = None
    for f in sorted(names):
        if not f.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(PANES, f)))
        except Exception:
            continue
        if rec.get("workspace") != ws:
            continue
        if now - float(rec.get("ts") or 0) > fresh:
            continue
        if not _pane_alive(rec.get("pid")):
            continue
        hit = rec.get("pane_id")
    return hit

def pickup_wants(affine_ws, spec_ws):
    """May THIS executor claim a disk ticket for spec_ws?
    affine_ws=None (catch-all pane): any workspace EXCEPT those served by a
    live dedicated pane — defer so the ticket shows up in its own window.
    affine_ws set (dedicated pane): only its own workspace."""
    if affine_ws:
        return bool(spec_ws) and os.path.realpath(spec_ws) == os.path.realpath(affine_ws)
    return affine_pane_for(spec_ws) is None


# ---------- NAR workspace locks: honest staleness ----------
# NAR 的锁文件记的 pid 是「提交者 executor」的 pid——跨进程 kill 改不了内存里的
# 锁对象,任务终了后锁文件会残留且 pid 活着 → NAR 的 acquire 只查 pid,永远
# 视为有效。桥层补上「holder 任务已终态 = 无锁」的判断与清扫(NAR 保持 pristine)。

NAR_HOME = os.environ.get("NAR_HOME") or os.path.expanduser("~/.native-agent-router")
NAR_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "killed", "unknown"}
NAR_ACTIVE = {"queued", "submitted", "running", "blocked", "cancel_requested", "cancelling"}

def nar_task_status(task_id):
    if not task_id:
        return None
    try:
        with open(os.path.join(NAR_HOME, "tasks", str(task_id) + ".json")) as f:
            return json.load(f).get("status")
    except Exception:
        return None

def _nar_pid_alive(pid):
    """POSIX-only liveness: os.kill(pid, 0) on Windows TERMINATES the target
    instead of probing, so there we never claim staleness from the pid."""
    if os.name == "nt" or not pid:
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False

def _lock_holder_stale(holder):
    st = nar_task_status(holder.get("task_id"))
    if st is not None:
        return st in NAR_TERMINAL
    if time.time() - float(holder.get("ts") or 0) > 86400:
        return True   # NAR's own acquire treats >24h holders as stale
    return not _nar_pid_alive(holder.get("pid"))

def workspace_lock_holder(workspace):
    """Live lock holder for this workspace after sweeping terminal residue,
    else None. Swept = holder task is in a terminal state (the recorded pid is
    the SUBMITTING executor and may outlive the task forever)."""
    locks_dir = os.path.join(NAR_HOME, "locks")
    h = hashlib.sha1(os.path.abspath(workspace).lower().encode("utf-8")).hexdigest()[:20]
    path = os.path.join(locks_dir, h + ".lock")
    try:
        holder = json.load(open(path))
    except Exception:
        return None
    if _lock_holder_stale(holder):
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    return holder

def sweep_workspace_locks(workspace=None, task_id=None):
    """Delete lock files whose holder task is terminal / pid gone. Scope:
    one workspace, one task, or everything. Returns removed paths."""
    locks_dir = os.path.join(NAR_HOME, "locks")
    removed = []
    try:
        names = os.listdir(locks_dir)
    except OSError:
        return removed
    for f in sorted(names):
        if not f.endswith(".lock"):
            continue
        p = os.path.join(locks_dir, f)
        try:
            holder = json.load(open(p))
        except Exception:
            continue
        if workspace and os.path.abspath(holder.get("workspace") or "") != os.path.abspath(workspace):
            continue
        if task_id and holder.get("task_id") != task_id:
            continue
        if _lock_holder_stale(holder):
            try:
                os.unlink(p)
                removed.append(p)
            except OSError:
                pass
    return removed


# ---------- offline cancel of queued disk tickets ----------

def cancel_request(request_id):
    """Cancel a disk ticket that has no task yet: write a tombstone the pickup
    scan respects. Returns "cancelled" | "has_task" (delegate to the task)
    | "unknown"."""
    if not REQ_RE.match(request_id or ""):
        return "unknown"
    try:
        rec = load_request(request_id)
    except Exception:
        return "unknown"
    if rec.get("task_id"):
        return "has_task"
    marker = os.path.join(REQUESTS, request_id + ".cancel")
    fd, tmp = tempfile.mkstemp(dir=REQUESTS, prefix="tmp-")
    with os.fdopen(fd, "w") as f:
        f.write("cancelled\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, marker)
    return "cancelled"

def request_cancelled(request_id):
    if not REQ_RE.match(request_id or ""):
        return False
    return os.path.exists(os.path.join(REQUESTS, request_id + ".cancel"))


# ---------- receipts: fold-proof submit acknowledgements ----------
# Pane markers can be soft-wrapped and lost to a narrow viewport; a receipt file
# is the durable ack a `send` client polls. Keyed by nonce (client-generated).

def receipt_path(nonce):
    if not NONCE_RE.match(nonce or ""):
        raise ValueError(f"invalid nonce: {nonce!r}")
    return os.path.join(RECEIPTS, "n-" + hashlib.sha256(nonce.encode()).hexdigest()[:24] + ".json")


def save_receipt(nonce, payload):
    try:
        p = receipt_path(nonce)
    except ValueError:
        return
    rec = dict(payload)
    rec["nonce"] = nonce
    rec["ts"] = time.time()
    fd, tmp = tempfile.mkstemp(dir=RECEIPTS, prefix="tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, ensure_ascii=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def load_receipt(nonce):
    try:
        p = receipt_path(nonce)
    except ValueError:
        return None
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None
