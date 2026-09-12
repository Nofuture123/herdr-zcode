#!/usr/bin/env python3
"""zcodecli MCP proxy — tools are served over the pane channel (no private NAR kernel).

Standalone MCP stdio server (newline-delimited JSON-RPC). Tools mirror the zcodecli CLI:
  submit(goal, workspace?, mode?, scope?, verify?, policy?, timeout?, idempotency_key?)
  result(request_id, timeout_ms?)   -> trusted evidence from results/<task_id>.json
  list() | inspect(task_id, what?) | cancel(task_id)
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "zcodecli_cli.py")
PY = sys.executable

def cli(*args, timeout=320):
    p = subprocess.run([PY, CLI, *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

def tool_submit(a):
    spec = {"goal": a["goal"], "workspace": a.get("workspace") or os.getcwd()}
    for k in ("mode", "policy", "timeout", "idempotency_key"):
        if a.get(k) is not None: spec[k] = a[k]
    if a.get("scope"): spec["scope"] = a["scope"]
    if a.get("verify"): spec["verify"] = a["verify"]
    rc, out, err = cli("send", json.dumps(spec, ensure_ascii=False), "--workspace", spec["workspace"])
    if rc != 0: return {"ok": False, "error": err or out}
    line = next((l for l in out.splitlines() if l.startswith("accepted:")), None)
    if not line: return {"ok": False, "error": (err or out)[-200:]}
    parts = line.split()
    if len(parts) < 4: return {"ok": False, "error": f"bad receipt: {line!r}"}
    return {"ok": True, "request_id": parts[1], "task_id": parts[2], "status": parts[3]}

def tool_result(a):
    args = ["result", "--request", a["request_id"], "--machine",
            "--timeout", str(int(a.get("timeout_ms", 300000)))]
    rc, out, err = cli(*args, timeout=a.get("timeout_ms", 300000)/1000 + 30)
    if out.startswith("{"):
        try: return json.loads(out)
        except Exception: pass
    return {"ok": False, "exit_code": rc, "error": err or out[-200:]}

def tool_list(a):
    rc, out, err = cli("list", "--machine")
    try: return {"ok": rc == 0, "tasks": json.loads(out)}
    except Exception: return {"ok": False, "error": out or err}

def tool_inspect(a):
    rc, out, err = cli("inspect", a["task_id"], a.get("what", "summary"))
    try: return {"ok": rc == 0, "data": json.loads(out)}
    except Exception: return {"ok": rc == 0, "text": out or err}

def tool_cancel(a):
    rc, out, err = cli("cancel", a["task_id"])
    try: return {"ok": rc == 0, "data": json.loads(out)}
    except Exception: return {"ok": rc == 0, "text": out or err}

TOOLS = {
 "submit": {"description": "Delegate a task to the ZCode executor pane. Returns request_id/task_id immediately.",
   "inputSchema": {"type":"object","properties":{
     "goal":{"type":"string"},"workspace":{"type":"string"},"mode":{"type":"string","enum":["plan","build","edit","yolo"]},
     "scope":{"type":"array","items":{"type":"string"}},"verify":{"type":"string"},
     "policy":{"type":"string","enum":["allow","deny"]},"timeout":{"type":"integer"},
     "idempotency_key":{"type":"string"}},"required":["goal"]}},
 "result": {"description": "Wait for and return the FACTS of a request_id (status/verify_ok/out_of_scope/summary). Acceptance is decided by the caller.",
   "inputSchema": {"type":"object","properties":{"request_id":{"type":"string"},"timeout_ms":{"type":"integer"}},
     "required":["request_id"]}},
 "list": {"description": "List all tasks with status.", "inputSchema": {"type":"object","properties":{}}},
 "inspect": {"description": "Inspect a task's evidence.", "inputSchema": {"type":"object",
   "properties":{"task_id":{"type":"string"},"what":{"type":"string","enum":["status","summary","diff","verify","usage"]}},
   "required":["task_id"]}},
 "cancel": {"description": "Cancel a task; the result says whether it actually stopped.",
   "inputSchema": {"type":"object","properties":{"task_id":{"type":"string"}},"required":["task_id"]}},
}
FN = {"submit": tool_submit, "result": tool_result, "list": tool_list,
      "inspect": tool_inspect, "cancel": tool_cancel}

def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg["result" if error is None else "error"] = error if error is not None else result
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: msg = json.loads(line)
    except Exception: continue
    method = msg.get("method", ""); id_ = msg.get("id")
    if method == "initialize":
        reply(id_, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "zcodecli", "version": "0.4.0"}})
    elif method == "tools/list":
        reply(id_, {"tools": [{"name": n, "description": t["description"],
                               "inputSchema": t["inputSchema"]} for n, t in TOOLS.items()]})
    elif method == "tools/call":
        name = msg["params"]["name"]; args = msg["params"].get("arguments", {})
        try:
            data = FN[name](args)
            reply(id_, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                        "isError": not data.get("ok", True)})
        except Exception as e:
            reply(id_, {"content": [{"type": "text", "text": f"tool error: {e}"}], "isError": True})
    elif method.startswith("notifications/"):
        pass
    else:
        if id_ is not None:
            reply(id_, None, {"code": -32601, "message": f"unknown method {method}"})
