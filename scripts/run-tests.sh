#!/bin/sh
# One-command verification gate for zcode-bridge.
set -e
cd "$(dirname "$0")/.."
echo "== shell syntax =="; sh -n scripts/*.sh
echo "== python compile =="; python3 -m py_compile scripts/*.py
echo "== unit tests =="
python3 -m unittest discover -s tests -v > /tmp/zcodecli-ut.log 2>&1
UT_RC=$?
tail -3 /tmp/zcodecli-ut.log
[ "$UT_RC" -eq 0 ] || { echo "UNIT TESTS FAILED ($UT_RC)"; exit "$UT_RC"; }
echo "== broker live dirs =="; python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('b','scripts/broker.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.init_dirs(); print('storage ok:', m.BASE)"
echo "ALL GATES PASSED"
