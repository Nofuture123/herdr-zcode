#!/bin/sh
# One-command verification gate for zcode-bridge.
set -e
cd "$(dirname "$0")/.."
echo "== shell syntax =="; sh -n scripts/*.sh 2>/dev/null; true
echo "== manifest =="; python3 -c "import tomllib; tomllib.loads(open('herdr-plugin.toml').read())"
python3 - <<'PY'
import os, tomllib
m = tomllib.loads(open("herdr-plugin.toml").read())
assert m.get("id") == "zcode.integration", "manifest id must be zcode.integration"
assert any(p.get("id") == "task" for p in m.get("panes", [])), "task pane missing"
assert any(a.get("id") == "setup" for a in m.get("actions", [])), "setup action missing"
assert m.get("startup"), "startup entry missing (watchdog daemon)"
assert os.path.isfile("config/agent-detection/zcode.toml"), "agent-detection manifest missing"
print("manifest entries ok")
PY
echo "== python compile =="; python3 -m py_compile scripts/*.py
echo "== unit tests =="
# hermetic gate: color env must never flip results (executor_common decides at
# import); HERDR_* would flip broker behavior when run from inside a herdr pane
if env -u NO_COLOR -u QAB_EXEC_PLAIN -u HERDR_ENV -u HERDR_PANE_ID \
       -u HERDR_SOCKET_PATH -u HERDR_WORKSPACE_ID \
   python3 -m unittest discover -s tests -v > /tmp/zcodecli-ut.log 2>&1; then
  tail -3 /tmp/zcodecli-ut.log
else
  rc=$?
  tail -25 /tmp/zcodecli-ut.log
  echo "UNIT TESTS FAILED ($rc)"
  exit "$rc"
fi
echo "== broker live dirs =="; python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('b','scripts/broker.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.init_dirs(); print('storage ok:', m.BASE)"
echo "ALL GATES PASSED"
