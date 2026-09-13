#!/bin/sh
# Idempotent bridge installer. Safe to run repeatedly (startup hook + actions).
# Installs native-agent-router (pinned) into a FIXED path so MCP configs and
# skills stay valid across plugin reinstalls:
#   ~/.local/share/qonnwolf-zcode-bridge/{venv,bin}
set -u
BASE="$HOME/.local/share/qonnwolf-zcode-bridge"
VENV="$BASE/venv"
BIN="$BASE/bin"
PLUGIN_ROOT="${PLUGIN_ROOT:-$PWD}"
LOG="$BASE/last-ensure.log"
NAR_SHA="d65bd49755bf4f6637b3c103650175b1b789e3ae"   # verified runtime; upstream tag moved
NAR_PIN="native-agent-router @ git+https://github.com/BerineYang/native-agent-router.git@$NAR_SHA"

mkdir -p "$BASE" "$BIN"
{
  echo "ensure-bridge $(date '+%F %T')"

  # Locate ZCode CLI (app bundle on macOS, or $ZCODE_BIN, or PATH)
  ZCODE_BIN="${ZCODE_BIN:-}"
  if [ -z "$ZCODE_BIN" ] && [ -x "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs" ]; then
    ZCODE_BIN="/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"
  fi
  if [ -z "$ZCODE_BIN" ]; then
    ZCODE_BIN="$(command -v zcode || true)"
  fi
  [ -n "$ZCODE_BIN" ] && echo "zcode_bin: $ZCODE_BIN" || echo "zcode_bin: NOT FOUND"

  # Login state (informational here; plugin-build.sh hard-gates it at install).
  CRED_FILE="${ZCODE_DATA_BASE_DIR:-$HOME}/.zcode/v2/credentials.json"
  if [ -f "$CRED_FILE" ] && grep -q '"oauth:[a-z][a-z0-9_]*:access_token"' "$CRED_FILE"; then
    echo "zcode_login: ok"
  else
    echo "zcode_login: NOT LOGGED IN (run: zcode login)"
  fi

  # Herdr runs plugin commands with a minimal PATH; resolve node/python absolutely
  NODE_BIN="$(command -v node || true)"
  [ -n "$NODE_BIN" ] || for c in /opt/homebrew/bin/node /usr/local/bin/node; do [ -x "$c" ] && NODE_BIN="$c" && break; done
  PY3="$(command -v python3 || true)"
  [ -n "$PY3" ] || for c in /opt/homebrew/bin/python3 /usr/bin/python3; do [ -x "$c" ] && PY3="$c" && break; done
  echo "node: ${NODE_BIN:-NOT FOUND}"; echo "python3: ${PY3:-NOT FOUND}"
  PATH_PREFIX="$(dirname "${NODE_BIN:-/opt/homebrew/bin/node}"):/opt/homebrew/bin:/usr/local/bin"
  cat > "$BASE/env.sh" <<ENVEO
export PATH="$PATH_PREFIX:/usr/bin:/bin:/usr/sbin:/sbin:\$PATH"
export NODE_BIN="${NODE_BIN}"
export PY3="${PY3}"
ENVEO

  # Create venv + install pinned NAR; recreate if the installed commit differs (fail-closed)
  need_install=1
  if [ -x "$VENV/bin/nar" ]; then
    du_file="$VENV/lib/python"*/site-packages/native_agent_router-*.dist-info/direct_url.json
    got="$(grep -o '"commit_id": "[0-9a-f]*"' $du_file 2>/dev/null | cut -d'"' -f4)"
    if [ "$got" = "$NAR_SHA" ]; then
      need_install=0; echo "install: present (commit matches)"
    else
      echo "install: commit mismatch (got ${got:-none}, want $NAR_SHA); reinstalling"
      rm -rf "$VENV"
    fi
  fi
  if [ "$need_install" = 1 ]; then
    echo "installing: $NAR_PIN (atomic: build aside, verify, then switch)"
    rm -rf "$VENV.new"
    python3 -m venv "$VENV.new" && "$VENV.new/bin/pip" -q install "$NAR_PIN" \
      || { rm -rf "$VENV.new"; echo "install: FAILED"; exit 1; }
    got2="$(grep -o '"commit_id": "[0-9a-f]*"' "$VENV.new"/lib/python*/site-packages/native_agent_router-*.dist-info/direct_url.json 2>/dev/null | cut -d'"' -f4)"
    [ "$got2" = "$NAR_SHA" ] || { rm -rf "$VENV.new"; echo "install: SHA mismatch ($got2)"; exit 1; }
    if ZCODE_BIN="$ZCODE_BIN" "$VENV.new/bin/nar" doctor 2>&1 | grep -q "^[X]"; then
      rm -rf "$VENV.new"; echo "install: doctor FAILED on new venv"; exit 1
    fi
    [ -d "$VENV" ] && { rm -rf "$VENV.old"; mv "$VENV" "$VENV.old"; }
    mv "$VENV.new" "$VENV"
    rm -rf "$VENV.old"
    echo "install: ok (atomic switch complete)"
  fi

  # Stable wrappers that carry ZCODE_BIN so CLI agents need zero env setup
  cat > "$BIN/nar" <<WRAP
#!/bin/sh
. "$BASE/env.sh"
export ZCODE_BIN="\${ZCODE_BIN:-$ZCODE_BIN}"
exec "$VENV/bin/nar" "\$@"
WRAP
  cat > "$BIN/zcodecli-mcp" <<WRAP
#!/bin/sh
. "$BASE/env.sh"
export ZCODE_BIN="\${ZCODE_BIN:-$ZCODE_BIN}"
exec "$PY3" "$PLUGIN_ROOT/scripts/zcodecli_mcp.py"
WRAP
  chmod +x "$BIN/nar" "$BIN/zcodecli-mcp" "$BIN/zcodecli"

  # Health check: program must answer AND report no [X] problems (nar doctor exits 0 regardless)
  if ZCODE_BIN="$ZCODE_BIN" "$BIN/nar" doctor > "$BASE/doctor.out" 2>&1 && ! grep -q "^\[X\]" "$BASE/doctor.out"; then
    echo "doctor: ok"
  else
    echo "doctor: FAILED"; cat "$BASE/doctor.out" | tail -5; exit 1
  fi
} > "$LOG" 2>&1
cat "$LOG"

# zcodecli CLI wrapper: herdr-protocol client (send/read/result/close + nar passthrough)
cat > "$BIN/zcodecli" <<WRAP
#!/bin/sh
. "$BASE/env.sh"
export ZCODE_BIN="\${ZCODE_BIN:-$ZCODE_BIN}"
exec "$PY3" "$PLUGIN_ROOT/scripts/zcodecli_cli.py" "\$@"
WRAP
chmod +x "$BIN/zcodecli"
echo "zcodecli: $BIN/zcodecli (plugin root $PLUGIN_ROOT)"

# --- PATH links begin (never clobber foreign commands; record exactly what we own)
LINK_DIR=""
[ -d /opt/homebrew/bin ] && [ -w /opt/homebrew/bin ] && LINK_DIR=/opt/homebrew/bin
if [ -z "$LINK_DIR" ]; then
  mkdir -p "$HOME/.local/bin" 2>/dev/null && LINK_DIR="$HOME/.local/bin"
fi
LINKS_FILE="$BASE/path-links"
OWNED_OLD=""
[ -f "$LINKS_FILE" ] && OWNED_OLD="$(cat "$LINKS_FILE")"
OWNED_NEW=""
for name in zcodecli nar; do
  dst="$LINK_DIR/$name"
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$BIN/$name" ]; then
    ln -sf "$BIN/$name" "$dst"          # refresh our own link
    OWNED_NEW="$OWNED_NEW$dst\n"
  elif [ -e "$dst" ]; then
    echo "skip: $dst already exists (not ours); use $BIN/$name directly"
  else
    ln -s "$BIN/$name" "$dst" && OWNED_NEW="$OWNED_NEW$dst\n"
  fi
done
# prune links we own but that moved/changed location this run
if [ -n "$OWNED_OLD" ]; then
  printf "%b" "$OWNED_OLD" | while read -r oldl || [ -n "$oldl" ]; do
    [ -z "$oldl" ] && continue
    case "$OWNED_NEW" in *"$oldl"*) ;; *)
      if [ -L "$oldl" ] && case "$(readlink "$oldl")" in "$BASE"/*) true;; *) false;; esac; then
        rm -f "$oldl" && echo "pruned stale link $oldl"
      fi ;;
    esac
  done
fi
printf "%b" "$OWNED_NEW" > "$LINKS_FILE" 2>/dev/null || true
[ -s "$LINKS_FILE" ] && echo "PATH links: $(tr '\n' ' ' < "$LINKS_FILE")"
case ":$PATH:" in *":$LINK_DIR:"*) ;; *) echo "note: $LINK_DIR is not on PATH; add it";; esac
# --- PATH links end
case ":$PATH:" in *":$LINK_DIR:"*) ;; *) echo "note: $LINK_DIR is not on PATH; add it";; esac
