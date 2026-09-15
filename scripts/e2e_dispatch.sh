#!/usr/bin/env bash
# e2e_dispatch.sh — 发版门禁:多主控 × 多窗口任务分发矩阵(真机,非模拟)。
#
# 每个主控(CLI+模型)在自己的独立 herdr tab 里运行,自行用 zcodecli 开独立
# chat pane(v0.7.3 workspace 定位),派一张 edit+verify 票到独立 git 工作区,
# 等终态后回打 E2E-RESULT 行。harness 只信磁盘证据(results/)与 notes.txt
# 实际内容裁决;任一主控 FAIL 则整体 exit 1。
#
# 用法:
#   E2E_MASTERS="codex,claude,pi" bash scripts/e2e_dispatch.sh        # 全矩阵
#   E2E_MASTERS="claude" bash scripts/e2e_dispatch.sh                 # 单主控冒烟
#   bash scripts/e2e_dispatch.sh --dry-run                            # 只打印计划
#   bash scripts/e2e_dispatch.sh --timeout 600 --keep                 # 秒数/保留现场
#
# 环境变量:
#   E2E_MASTERS      逗号分隔:codex,claude,pi(默认全开)
#   E2E_HERDR_WS     主控 tab 落放的 herdr workspace ID(默认:会话默认)
#   E2E_CODEX_MODEL  codex 模型(默认 gpt-5.6-luna,reasoning low)
#   E2E_CLAUDE_MODEL claude 模型(默认 sonnet)
#   E2E_PI_MODEL     pi 模型(默认 deepseek-v4.1-flash;需带 provider 时加 E2E_PI_EXTRA)
#
# 兼容 macOS 自带 bash 3.2:不用关联数组,状态落状态目录。
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
HERDR="${HERDR_BIN_PATH:-herdr}"
RESULTS="$HOME/.local/share/herdr-zcode/results"
TIMEOUT=600; KEEP=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2;;
    --keep) KEEP=1; shift;;
    --dry-run) DRY=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
MASTERS="${E2E_MASTERS:-codex,claude,pi}"
TAGTS="$(date +%H%M%S)"
WS_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/herdr-zcode-e2e-$TAGTS.XXXXXX")"
STATE="$WS_ROOT/.state"; mkdir -p "$STATE"
st() { printf '%s' "$2" > "$STATE/$1.$3"; }          # st <master> <value> <key>
gv() { cat "$STATE/$1.$2" 2>/dev/null; }             # gv <master> <key>

master_cmd() { # $1=master ; 输出命令模板,$P 为派票指令占位
  case "$1" in
    codex)
      local m="${E2E_CODEX_MODEL:-gpt-5.6-luna}"
      # exec 沙箱会拦 zcodecli 的 socket/写盘;用历史上验证过的旁路旗标
      echo "codex exec --skip-git-repo-check -m $m -c model_reasoning_effort='\"low\"' --dangerously-bypass-approvals-and-sandbox \$P";;
    claude)
      echo "command claude --dangerously-skip-permissions --model ${E2E_CLAUDE_MODEL:-sonnet} -p \$P";;
    pi)
      echo "pi -p --model ${E2E_PI_MODEL:-deepseek-v4.1-flash} \$P";;
    *) return 1;;
  esac
}

make_prompt() { # $1=tag $2=workspace
  cat <<EOF
你是主控 agent,只做下面这件事,全部用 zcodecli(已在 PATH)完成:
1) 运行: zcodecli chat-open --workspace $2
   从输出记下 chat pane id(形如 wNN:pX)。
2) 运行(严格单行,JSON 用单引号包裹):
   zcodecli --pane <pane_id> send '{"goal":"用 bash 在 notes.txt 末尾追加一行 E2E-$1。不要改其他文件。","workspace":"$2","mode":"edit","verify":"grep -q E2E-$1 $2/notes.txt","timeout":300}'
   从回执输出记下 request_id(形如 r-xxxxxxxxxxxxxxxx)。
3) 运行: zcodecli --pane <pane_id> result --request <request_id> --timeout 300000 --machine
   等它打印终态 JSON(最多 6 分钟)。
4) 最后单独一行输出(逐字此格式):
   E2E-RESULT $1 <task_id> <status> verify_ok=<true|false>
除上述命令外不要执行任何其他操作,不要读写其他目录。
EOF
}

shq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# ---- 计划/启动:每主控一个 tab(独立窗口) ----
for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
  WS="$WS_ROOT/$m"; mkdir -p "$WS"
  git -C "$WS" init -q 2>/dev/null
  echo "seed" > "$WS/notes.txt"
  git -C "$WS" add -A
  git -C "$WS" -c user.email=e2e@local -c user.name=e2e commit -qm seed 2>/dev/null
  st "$m" "$WS" ws
  PROMPT="$(make_prompt "$m" "$WS")"
  TPL="$(master_cmd "$m")" || { echo "unknown master: $m" >&2; exit 2; }
  if [ "$DRY" = 1 ]; then
    echo "[$m] workspace=$WS"
    echo "[$m] tab: herdr tab create --cwd $WS --label e2e-$m-$TAGTS$( [ -n "${E2E_HERDR_WS:-}" ] && printf ' --workspace %s' "$E2E_HERDR_WS" )"
    echo "[$m] cmd: $(printf '%s' "$TPL" | sed "s|\$P|<派票指令 $(printf '%s' "$PROMPT" | wc -c | tr -d ' ') 字节>|")"
    continue
  fi
  ARGS="tab create --cwd $WS --label e2e-$m-$TAGTS"
  [ -n "${E2E_HERDR_WS:-}" ] && ARGS="$ARGS --workspace $E2E_HERDR_WS"
  TABOUT=$("$HERDR" $ARGS 2>&1)
  PANE=$(printf '%s' "$TABOUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d["result"].get("root_pane", {}).get("pane_id")
          or d["result"].get("tab", {}).get("pane_id", ""))
except Exception:
    print("")')
  TAB=$(printf '%s' "$TABOUT" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["tab"]["tab_id"])' 2>/dev/null)
  if [ -z "$PANE" ] && [ -n "$TAB" ]; then
    sleep 1
    PANE=$(TAB_ID="$TAB" "$HERDR" pane list 2>/dev/null | TAB_ID="$TAB" python3 -c '
import json, os, sys
tab = os.environ.get("TAB_ID", "")
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
ps = [p for p in d.get("result", {}).get("panes", []) if p.get("tab_id") == tab]
print(ps[0]["pane_id"] if ps else "")')
  fi
  if [ -z "$PANE" ]; then
    echo "[$m] FAIL: tab/pane 创建失败: $TABOUT" >&2
    st "$m" pane-create-failed out; continue
  fi
  st "$m" "$PANE" pane
  echo "[$m] tab=$TAB pane=$PANE ws=$WS"
  CMD="${TPL/\$P/$(shq "$PROMPT")}"
  "$HERDR" pane run "$PANE" "$CMD" >/dev/null 2>&1
  st "$m" dispatched out
  st "$m" "$(date +%s)" t_launch
done
[ "$DRY" = 1 ] && { rm -rf "$WS_ROOT"; exit 0; }

echo "e2e root: $WS_ROOT · timeout: ${TIMEOUT}s · 等待主控…"
# master 完成判定以进程为准(pane run 会回显指令文本,文本标记会被回显污染):
# 前台进程组回到 shell 自身 = master 进程已退出
master_done() { # $1=pane -> 0=仍在跑 1=已退出
  "$HERDR" pane process-info --pane "$1" 2>/dev/null | python3 -c '
import json, sys
try:
    pi = json.load(sys.stdin)["result"]["process_info"]
except Exception:
    print(1); raise SystemExit   # pane 没了 = 退出
print(1 if pi.get("foreground_process_group_id") == pi.get("shell_pid") else 0)'
}
DEADLINE=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  pending=0
  for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
    [ "$(gv "$m" out)" = dispatched ] || continue
    P="$(gv "$m" pane)"
    SEEN=$("$HERDR" pane read "$P" --source visible --lines 200 2>/dev/null)
    if printf '%s' "$SEEN" | grep -qE "no receipt within|rejected \(fail-closed\)|idempotency_"; then
      st "$m" delivery-failed out; continue; fi
    if [ "$(master_done "$P")" = 1 ]; then st "$m" "$(date +%s)" t_done; st "$m" done out; continue; fi
    pending=1
  done
  [ "$pending" = 0 ] && break
  sleep 5
done

# ---- 裁决:磁盘证据 + notes.txt 实际内容 ----
printf '\n== E2E 多主控分发矩阵(%s) ==\n' "$(date +%H:%M:%S)"
overall=0
for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
  WS_M="$(gv "$m" ws)"; P="$(gv "$m" pane)"
  VERDICT=FAIL; REASON=""
  OUTTXT=$("$HERDR" pane read "$P" --source visible --lines 300 2>/dev/null)
  RESF=$(printf '%s' "$OUTTXT" | grep -o 't-[0-9a-f]\{8,\}' | head -1)
  # 分段时间(精确到文件时间戳):派票 = 启动→executor 落 request;执行 = request→result 落盘
  REQF=$(REQ_DIR="$HOME/.local/share/herdr-zcode/requests" TAG="$m" python3 - <<'PY'
import json, os, sys, time
d, tag = os.environ["REQ_DIR"], os.environ["TAG"]
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
        if best is None or t > best[0]:
            best = (t, f)
print(best[1] if best else "")
PY
)
  TIMING=""
  if [ -n "$REQF" ] && [ -n "$RESF" ] && [ -f "$RESULTS/$RESF.json" ]; then
    TIMING=$(REQ_TS="$STATE/$REQF.ts" RESF="$RESF" RESULTS="$RESULTS" REQF="$REQF" LAUNCH="$(gv "$m" t_launch)" DONE="$(gv "$m" t_done)" python3 - <<'PY'
import json, os, time
res = os.environ["RESULTS"]; reqp = os.path.join(os.path.dirname(res), "requests", os.environ["REQF"])
launch = float(os.environ["LAUNCH"])
try:
    t_acc = float(json.load(open(reqp)).get("ts", 0))
except Exception:
    t_acc = 0
try:
    t_res = os.path.getmtime(os.path.join(res, os.environ["RESF"] + ".json"))
except OSError:
    t_res = 0
done = os.environ.get("DONE") or "0"
parts = [f"dispatch={t_acc - launch:.0f}s" if t_acc else "dispatch=?",
         f"exec={t_res - t_acc:.0f}s" if t_acc and t_res else "exec=?",
         f"total={(float(done) or t_res) - launch:.0f}s"]
print(" ".join(parts))
PY
)
  fi
  case "$(gv "$m" out)" in
    pane-create-failed) REASON="tab/pane 创建失败";;
    delivery-failed)    REASON="投递失败(herdr 输入黑洞 / 无回执 / 拒单)";;
    dispatched)         REASON="超 ${TIMEOUT}s 未到终态";;
    *)
      if [ -z "$RESF" ]; then REASON="master 未产出 task_id"
      elif [ ! -f "$RESULTS/$RESF.json" ]; then REASON="无结果文件 results/$RESF.json"
      else
        EVAL=$(RESULTS="$RESULTS/$RESF.json" WS_M="$WS_M" TAG="$m" python3 - <<'PY'
import json, os, sys
rf, ws, tag = (os.environ[k] for k in ("RESULTS", "WS_M", "TAG"))
try:
    d = json.load(open(rf))
except Exception as e:
    print(f"结果文件不可读: {e}"); sys.exit()
if d.get("status") != "succeeded":
    print(f"status={d.get('status')} error={d.get('error')}"); sys.exit()
ver = (d.get("result") or {}).get("verify") or []
if not ver:
    print("verify 未执行"); sys.exit()
if not all(v.get("ok") for v in ver):
    print(f"verify 失败: {ver}"); sys.exit()
try:
    body = open(os.path.join(ws, "notes.txt")).read()
except OSError as e:
    print(f"notes.txt 不可读: {e}"); sys.exit()
if f"E2E-{tag}" not in body:
    print("notes.txt 里没有落上标记"); sys.exit()
diff = (d.get("result") or {}).get("diff") or {}
cf = diff.get("changed_files") or []
if cf and not any("notes.txt" in c for c in cf):
    print(f"changed_files 意外: {cf} (out_of_scope={diff.get('out_of_scope')})"); sys.exit()
print("PASS")
PY
)
        if [ "$EVAL" = "PASS" ]; then VERDICT=PASS; else REASON="$EVAL"; fi
      fi;;
  esac
  [ "$VERDICT" = PASS ] || overall=1
  printf '%-8s %-4s task=%-18s %-34s %s\n' "$m" "$VERDICT" "${RESF:--}" "${TIMING:-timing=n/a}" "${REASON:+reason: $REASON}"
done

if [ "$KEEP" != 1 ]; then
  for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
    P="$(gv "$m" pane)"; [ -n "$P" ] && "$HERDR" pane close "$P" >/dev/null 2>&1
  done
  rm -rf "$WS_ROOT"
  echo "现场已清理(--keep 可保留)"
else
  echo "现场保留: $WS_ROOT · panes: $(for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do printf '%s ' "$(gv "$m" pane)"; done)"
fi
exit $overall
