#!/usr/bin/env bash
# e2e_dispatch.sh — 发版门禁:多主控 × 多窗口任务分发矩阵(交互式真机,非模拟)。
#
# 每个主控以**交互 TUI**常驻在自己的独立 herdr tab 里(codex/claude/pi 界面可见),
# harness 等主控就绪后把派票指令打进 TUI 输入框;主控自行 zcodecli chat-open 开独立
# zcode 窗口、派 edit+verify 票到独立 git 工作区、等终态。全程窗口可见、人可围观。
# harness 只信磁盘证据(requests/results)+ notes.txt 实际内容裁决;任一主控 FAIL
# 整体 exit 1。
#
# 用法:
#   E2E_MASTERS="codex,claude,pi" bash scripts/e2e_dispatch.sh          # 全矩阵(默认保留窗口)
#   E2E_MASTERS="claude" bash scripts/e2e_dispatch.sh --close           # 单主控冒烟,跑完关窗
#   bash scripts/e2e_dispatch.sh --dry-run                              # 只打印计划
#
# 环境变量:
#   E2E_MASTERS      逗号分隔:codex,claude,pi(默认全开)
#   E2E_HERDR_WS     主控 tab 落放的 herdr workspace ID(默认:会话默认)
#   E2E_CODEX_MODEL  默认 gpt-5.6-luna(reasoning low)
#   E2E_CLAUDE_MODEL 默认 sonnet
#   E2E_PI_MODEL     默认 deepseek-v4.1-flash
#
# 兼容 macOS 自带 bash 3.2:不用关联数组,状态落状态目录。
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
HERDR="${HERDR_BIN_PATH:-herdr}"
RESULTS="$HOME/.local/share/herdr-zcode/results"
TIMEOUT=600; CLOSE=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2;;
    --close) CLOSE=1; shift;;
    --dry-run) DRY=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
MASTERS="${E2E_MASTERS:-codex,claude,pi}"
TAGTS="$(date +%H%M%S)"
WS_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/herdr-zcode-e2e-$TAGTS.XXXXXX")"
STATE="$WS_ROOT/.state"; mkdir -p "$STATE"
st() { printf '%s' "$2" > "$STATE/$1.$3"; }
gv() { cat "$STATE/$1.$2" 2>/dev/null; }

# 每个主控的交互 TUI 启动命令(在它的 tab pane 的 shell 里敲)
tui_cmd() {
  case "$1" in
    codex)   echo "codex --dangerously-bypass-approvals-and-sandbox -m ${E2E_CODEX_MODEL:-gpt-5.6-luna} -c model_reasoning_effort='\"low\"'";;
    claude)  echo "claude --dangerously-skip-permissions --model ${E2E_CLAUDE_MODEL:-sonnet}";;
    pi)      echo "pi --model ${E2E_PI_MODEL:-deepseek-v4.1-flash}";;
    *) return 1;;
  esac
}
AGENT_OF() { case "$1" in codex) echo codex;; claude) echo claude;; pi) echo pi;; *) echo "";; esac; }

make_prompt() { # 单行指令(TUI 输入框只吃一行);tag 带时间戳,防跨轮撞证据
  cat <<EOF
只做这一件事:用 zcodecli 派一张委派票并等到终态。步骤:1) 运行 zcodecli chat-open --workspace $2 ,记下输出的 chat pane id;2) 运行 zcodecli --pane <pane_id> send '{"goal":"用 bash 在 notes.txt 末尾追加一行 E2E-$1。不要改其他文件。","workspace":"$2","mode":"edit","verify":"grep -q E2E-$1 $2/notes.txt","timeout":300}' ,记下回执里的 request_id;3) 运行 zcodecli --pane <pane_id> result --request <request_id> --timeout 300000 --machine 等终态 JSON;4) 最后告诉我 task_id、status 和 verify 结果。除这些命令外不要做任何其他事。
EOF
}

# ---- 启动:每主控一个 tab,起交互 TUI,等就绪,打指令 ----
for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
  WS="$WS_ROOT/$m"; mkdir -p "$WS"
  git -C "$WS" init -q 2>/dev/null
  echo "seed" > "$WS/notes.txt"
  git -C "$WS" add -A
  git -C "$WS" -c user.email=e2e@local -c user.name=e2e commit -qm seed 2>/dev/null
  st "$m" "$WS" ws
  TAG_FULL="$m-$TAGTS"
  st "$m" "$TAG_FULL" tag
  if [ "$DRY" = 1 ]; then
    echo "[$m] ws=$WS tag=$TAG_FULL"
    echo "[$m] tab: herdr tab create --cwd $WS --label e2e-$m-$TAGTS → pane run '$(tui_cmd "$m")'"
    echo "[$m] 就绪后敲入: $(make_prompt "$TAG_FULL" "$WS" | cut -c1-60)…"
    continue
  fi
  ARGS="tab create --cwd $WS --label e2e-$m-$TAGTS"
  [ -n "${E2E_HERDR_WS:-}" ] && ARGS="$ARGS --workspace $E2E_HERDR_WS"
  TABOUT=$("$HERDR" $ARGS 2>&1)
  PANE=$(printf '%s' "$TABOUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d["result"].get("root_pane", {}).get("pane_id", ""))
except Exception:
    print("")')
  if [ -z "$PANE" ]; then echo "[$m] FAIL: tab 创建: $TABOUT" >&2; st "$m" pane-create-failed out; continue; fi
  st "$m" "$PANE" pane
  "$HERDR" pane run "$PANE" "$(tui_cmd "$m")" >/dev/null 2>&1

  # 等 TUI 就绪:herdr 识别出该 pane 的 agent(启动一般 2-8s)
  AG="$(AGENT_OF "$m")"
  i=0; READY=0
  while [ "$i" -lt 24 ]; do
    CUR=$("$HERDR" pane list 2>/dev/null | PANE_ID="$PANE" python3 -c '
import json, os, sys
pid = os.environ["PANE_ID"]
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
for p in d.get("result", {}).get("panes", []):
    if p.get("pane_id") == pid:
        print(p.get("agent") or ""); break')
    [ "$CUR" = "$AG" ] && { READY=1; break; }
    i=$((i+1)); sleep 1
  done
  [ "$READY" = 0 ] && sleep 4   # 识别不了也要给 TUI 起码的启动时间
  st "$m" "$(date +%s)" t_ready
  # claude TUI 在新目录会弹工作区信任对话框(默认停在 No, exit)——先把它选到 Yes
  TRUST=$("$HERDR" pane read "$PANE" --source visible --lines 40 2>/dev/null | grep -c "trust this folder")
  if [ "${TRUST:-0}" -gt 0 ]; then
    "$HERDR" pane send-keys "$PANE" down >/dev/null 2>&1
    "$HERDR" pane send-keys "$PANE" enter >/dev/null 2>&1
    sleep 3
  fi
  # 打入派票指令,并验证真的进了 TUI(输入框回显可见 / agent 转 working);
  # codex 等已知竞态:TUI 初始化期键入会整段丢失——丢了就重敲,最多 3 次
  PROMPT="$(make_prompt "$TAG_FULL" "$WS")"
  FRAG="E2E-$TAG_FULL"
  attempt=1
  while [ "$attempt" -le 3 ]; do
    "$HERDR" pane run "$PANE" "$PROMPT" >/dev/null 2>&1
    sleep 3
    SCREEN=$("$HERDR" pane read "$PANE" --source visible --lines 40 2>/dev/null)
    WORKING=$("$HERDR" pane list 2>/dev/null | PANE_ID="$PANE" python3 -c '
import json, os, sys
pid = os.environ["PANE_ID"]
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
for p in d.get("result", {}).get("panes", []):
    if p.get("pane_id") == pid:
        print(p.get("agent_status") or ""); break')
    [ "$WORKING" = "working" ] && break
    if printf '%s' "$SCREEN" | grep -q "$FRAG"; then
      # 文字在输入框里但没提交:补 Enter
      "$HERDR" pane send-keys "$PANE" enter >/dev/null 2>&1
      sleep 3
      WORKING=$("$HERDR" pane list 2>/dev/null | PANE_ID="$PANE" python3 -c '
import json, os, sys
pid = os.environ["PANE_ID"]
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
for p in d.get("result", {}).get("panes", []):
    if p.get("pane_id") == pid:
        print(p.get("agent_status") or ""); break')
      [ "$WORKING" = "working" ] && break
    fi
    attempt=$((attempt+1))
  done
  st "$m" "$(date +%s)" t_launch
  echo "[$m] pane=$PANE 指令已敲入(第 ${attempt} 次尝试) — 围观她的窗口吧"
done
[ "$DRY" = 1 ] && { rm -rf "$WS_ROOT"; exit 0; }

echo "e2e root: $WS_ROOT · timeout: ${TIMEOUT}s · 等待各主控派票并拿到终态…"
DEADLINE=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  pending=0
  for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
    [ "$(gv "$m" out)" = "" ] && st "$m" dispatched out
    [ "$(gv "$m" out)" != dispatched ] && continue
    WS_M="$(gv "$m" ws)"
    HIT=$(REQ_DIR="$HOME/.local/share/herdr-zcode/requests" TAG="$(gv "$m" tag)" LAUNCH="$(gv "$m" t_launch)" python3 - <<'PY'
import json, os, sys
d, tag = os.environ["REQ_DIR"], os.environ["TAG"]
launch = float(os.environ.get("LAUNCH") or 0)   # 只认本次启动之后落盘的请求,防跨轮撞旧证据
best = None
for f in os.listdir(d):
    if not f.endswith(".json"):
        continue
    try:
        r = json.load(open(os.path.join(d, f)))
    except Exception:
        continue
    if f"E2E-{tag}" in (r.get("spec", {}).get("goal") or ""):
        t = r.get("ts", 0)
        if t < launch:
            continue
        if best is None or t > best[0]:
            best = (t, r)
print(json.dumps({"ts": best[0], "tid": best[1].get("task_id") or ""}) if best else "")
PY
)
    if [ -n "$HIT" ]; then
      st "$m" "$HIT" hit
      TID=$(printf '%s' "$HIT" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tid",""))')
      if [ -n "$TID" ] && [ -f "$RESULTS/$TID.json" ]; then
        ST_NOW=$(python3 -c "import json;print(json.load(open('$RESULTS/$TID.json')).get('status',''))" 2>/dev/null)
        case "$ST_NOW" in
          succeeded|failed|cancelled|killed)
            st "$m" "$(date +%s)" t_done; st "$m" done out; continue;;
        esac
      fi
    fi
    pending=1
  done
  [ "$pending" = 0 ] && break
  sleep 5
done

# ---- 裁决:磁盘证据 + notes.txt 实际内容 ----
printf '\n== E2E 多主控交互分发矩阵(%s) ==\n' "$(date +%H:%M:%S)"
overall=0
for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
  WS_M="$(gv "$m" ws)"; VERDICT=FAIL; REASON=""
  HIT="$(gv "$m" hit)"
  TID=$(printf '%s' "$HIT" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tid",""))' 2>/dev/null)
  TACC=$(printf '%s' "$HIT" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("ts",0))' 2>/dev/null)
  TIMING="timing=n/a"
  if [ -n "$TID" ] && [ -f "$RESULTS/$TID.json" ]; then
    TIMING=$(TACC="$TACC" LAUNCH="$(gv "$m" t_launch)" RF="$RESULTS/$TID.json" python3 - <<'PY'
import os
t_acc = float(os.environ["TACC"] or 0); launch = float(os.environ["LAUNCH"] or 0)
t_res = os.path.getmtime(os.environ["RF"])
print(f"dispatch={t_acc - launch:.0f}s exec={t_res - t_acc:.0f}s total={t_res - launch:.0f}s")
PY
)
  fi
  case "$(gv "$m" out)" in
    pane-create-failed) REASON="tab 创建失败";;
    dispatched)         REASON="超 ${TIMEOUT}s 未到终态(票没派出?输入黑洞?看主控窗口)";;
    done)
      if [ -z "$TID" ]; then REASON="无 task_id"
      elif [ ! -f "$RESULTS/$TID.json" ]; then REASON="无结果文件"
      else
        EVAL=$(RF="$RESULTS/$TID.json" WS_M="$WS_M" TAG="$(gv "$m" tag)" python3 - <<'PY'
import json, os, sys
rf, ws, tag = (os.environ[k] for k in ("RF", "WS_M", "TAG"))
d = json.load(open(rf))
if d.get("status") != "succeeded":
    print(f"status={d.get('status')} error={d.get('error')}"); sys.exit()
ver = (d.get("result") or {}).get("verify") or []
if not ver:
    print("verify 未执行"); sys.exit()
if not all(v.get("ok") for v in ver):
    print(f"verify 失败: {ver}"); sys.exit()
body = open(os.path.join(ws, "notes.txt")).read()
if f"E2E-{tag}" not in body:
    print("notes.txt 里没有落上标记"); sys.exit()
diff = (d.get("result") or {}).get("diff") or {}
cf = diff.get("changed_files") or []
if cf and not any("notes.txt" in c for c in cf):
    print(f"changed_files 意外: {cf}"); sys.exit()
print("PASS")
PY
)
          if [ "$EVAL" = "PASS" ]; then VERDICT=PASS; else REASON="$EVAL"; fi
      fi;;
  esac
  [ "$VERDICT" = PASS ] || overall=1
  printf '%-8s %-4s task=%-18s %-38s %s\n' "$m" "$VERDICT" "${TID:--}" "$TIMING" "${REASON:+reason: $REASON}"
done

if [ "$CLOSE" = 1 ]; then
  for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
    P="$(gv "$m" pane)"; [ -n "$P" ] && "$HERDR" pane close "$P" >/dev/null 2>&1
  done
  "$HERDR" pane list 2>/dev/null | WSROOT="$WS_ROOT" python3 -c '
import json, os, sys
root = os.environ["WSROOT"].replace("/var/folders/", "/private/var/folders/")
alt = os.environ["WSROOT"]
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit
for p in d.get("result", {}).get("panes", []):
    cwd = (p.get("cwd") or "") + (p.get("foreground_cwd") or "")
    if root in cwd or alt in cwd or p.get("label", "").startswith("e2e-"):
        print(p["pane_id"])' | while read -r p; do
    "$HERDR" pane close "$p" >/dev/null 2>&1
  done
  rm -rf "$WS_ROOT"
  echo "现场已清理"
else
  echo "窗口保留供围观;确认后可跑: bash scripts/e2e_dispatch.sh --close(或手动 herdr pane close <id>)"
  echo "状态目录: $STATE"
fi
exit $overall
