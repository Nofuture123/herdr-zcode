# Shared helpers for herdr-zcode-plugin. Sourced by bin/*.sh, which run with
# the plugin directory as their working directory and Herdr's plugin env set.

plugin_herdr() {
  printf '%s\n' "${HERDR_BIN_PATH:-herdr}"
}

plugin_root() {
  printf '%s\n' "${HERDR_PLUGIN_ROOT:-$(pwd -P)}"
}

# Print one field from a JSON string: json_field <json> <dotted.path>
# Needs python3; prints nothing and returns 1 when python3 is missing or the
# path does not resolve.
json_field() {
  command -v python3 >/dev/null 2>&1 || return 1
  JSON_INPUT=$1 JSON_PATH=$2 python3 -c '
import json, os, sys
try:
    data = json.loads(os.environ["JSON_INPUT"])
    for part in os.environ["JSON_PATH"].split("."):
        data = data[part]
    if isinstance(data, (dict, list)):
        data = json.dumps(data)
    elif data is None:
        sys.exit(1)
    print(data)
except Exception:
    sys.exit(1)
'
}

# Print "<width> <height>" of the given pane from a `herdr pane edges` response.
pane_rect_size() {
  command -v python3 >/dev/null 2>&1 || return 1
  EDGES_JSON=$1 PANE_ID=$2 python3 -c '
import json, os, sys
try:
    data = json.loads(os.environ["EDGES_JSON"])
    for pane in data["layout"]["panes"]:
        if pane.get("pane_id") == os.environ["PANE_ID"]:
            rect = pane["rect"]
            print(rect["width"], rect["height"])
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
'
}

# Print the new pane id from a `herdr pane split` response, tolerating both the
# full CLI envelope and the bare result object.
split_pane_id() {
  command -v python3 >/dev/null 2>&1 || return 1
  SPLIT_JSON=$1 python3 -c '
import json, os, sys
data = None
try:
    data = json.loads(os.environ["SPLIT_JSON"])
except Exception:
    sys.exit(1)
for parts in (("result", "pane", "pane_id"), ("pane", "pane_id"), ("pane_id",)):
    node = data
    try:
        for part in parts:
            node = node[part]
        print(node)
        sys.exit(0)
    except Exception:
        continue
sys.exit(1)
'
}

# cd toward the workspace cwd (then the focused pane cwd) from the plugin
# context, best effort; stays in the plugin directory when nothing resolves.
cd_context_cwd() {
  [ -n "${HERDR_PLUGIN_CONTEXT_JSON:-}" ] || return 0
  _cwd=$(json_field "$HERDR_PLUGIN_CONTEXT_JSON" "workspace_cwd") || _cwd=""
  [ -n "$_cwd" ] || _cwd=$(json_field "$HERDR_PLUGIN_CONTEXT_JSON" "focused_pane_cwd") || _cwd=""
  if [ -n "$_cwd" ] && [ -d "$_cwd" ]; then
    cd "$_cwd" || :
  fi
}
