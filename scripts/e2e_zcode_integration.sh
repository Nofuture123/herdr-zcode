#!/usr/bin/env bash
# e2e_zcode_integration.sh — 官方 zcode CLI × herdr 集成端到端矩阵。
# codex(luna) + claude(sonnet) 在 herdr 里真实调用 zcode CLI,覆盖三种信息交接:
#   S1 文件接力:master 写 brief.txt(颜色)→ zcode 读它写 result1.txt → 验证内容
#   S2 会话记忆:zcode --prompt 记暗号 → 第二次 -c 续接写 secret.txt → 验证内容
#   S3 pane 交互(claude):master 开 zcode.integration 插件 pane → pane run 派活
#      → 等 agent_status=done → 验证 pane-task.txt
# 启动阶段用确定性状态机:长步骤写 TASK.md,TUI 只敲一行短触发语;吞字/信任框
# 竞态以屏幕标记回执检测并自动重试。只信磁盘证据;任一场景 FAIL 整体 exit 1。
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
HERDR="${HERDR_BIN_PATH:-herdr}"
TIMEOUT=600; CLOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2;;
    --close) CLOSE=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
MASTERS="${E2E_MASTERS:-codex,claude}"
TAGTS="$(date +%H%M%S)"
WS_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/herdr-zcode-int-e2e-$TAGTS.XXXXXX")"
STATE="$WS_ROOT/.state"; mkdir -p "$STATE"
st() { printf '%s' "$2" > "$STATE/$1.$3"; }
gv() { cat "$STATE/$1.$2" 2>/dev/null; }

tui_cmd() {
  case "$1" in
    codex)  echo "codex --dangerously-bypass-approvals-and-sandbox -m ${E2E_CODEX_MODEL:-gpt-5.6-luna} -c model_reasoning_effort='\"low\"'";;
    claude) echo "claude --dangerously-skip-permissions --model ${E2E_CLAUDE_MODEL:-sonnet}";;
    *) return 1;;
  esac
}
COLOR_OF()  { case "$1" in codex) echo crimson;; claude) echo amber;; esac; }
SECRET_OF() { case "$1" in codex) echo kiwi-42;; claude) echo mango-77;; esac; }
# agent==1 的就绪屏幕标记 / 回执标记(输入被吃掉的判据)
IDLE_MARK()   { case "$1" in codex) echo "Ask Codex to do anything";; claude) echo "for shortcuts";; esac; }
EATEN_MARK()  { case "$1" in codex) echo "Context 100% left";; claude) echo "";; esac; }

write_task_md() { # $1=ws $2=secret $3=claude_s3(0/1) $4=herdr_ws
  cat > "$1/TASK.md" <<TASKMDEOF
# 任务:用官方 zcode CLI 完成以下委派调用(逐条执行,不要做其他事)

1. 运行: zcode --prompt '阅读当前目录 brief.txt,把其中提到的颜色单词单独写入 result1.txt(文件只含这一个词)' --mode edit

2. 运行: zcode --prompt '记住暗号:$2' --mode plan

3. 运行: zcode --prompt '把之前告诉你的暗号原样写入 secret.txt(文件只含暗号本身)' -c --mode edit

4. 运行: cat result1.txt secret.txt,把两个文件的内容告诉我。

若某步 zcode 报错,原样报告错误,不要自行修补产物文件。
TASKMDEOF
  if [ "$3" = "1" ]; then
    cat >> "$1/TASK.md" <<TASKMDEOF

5. 测试 herdr 的 zcode pane 集成:运行 herdr plugin pane open --plugin zcode.integration --entrypoint task --placement tab --workspace $4 --cwd $1 并记下 pane_id;运行 herdr pane run <pane_id> '在当前目录创建 pane-task.txt,文件内容只写 integration 这一个词。';运行 herdr pane read <pane_id> --source visible --lines 15,若文字停在输入框未提交,运行 herdr pane send-keys <pane_id> enter(最多重试 3 次);用 herdr pane list 观察 <pane_id> 的 agent_status 直到 done(最长 120 秒);最后运行 cat $1/pane-task.txt 并告诉我内容。
TASKMDEOF
  fi
}

screen() { "$HERDR" pane read "$1" --source visible --lines 40 2>/dev/null; }
agent_of() {
  "$HERDR" pane list 2>/dev/null | PANE_ID="$1" python3 -c '
import json, os, sys
pid = os.environ["PANE_ID"]
try: d = json.load(sys.stdin)
except Exception: print(""); raise SystemExit
for p in d.get("result", {}).get("panes", []):
    if p.get("pane_id") == pid: print(p.get("agent") or ""); break'
}
status_of() {
  "$HERDR" pane list 2>/dev/null | PANE_ID="$1" python3 -c '
import json, os, sys
pid = os.environ["PANE_ID"]
try: d = json.load(sys.stdin)
except Exception: print(""); raise SystemExit
for p in d.get("result", {}).get("panes", []):
    if p.get("pane_id") == pid: print(p.get("agent_status") or ""); break'
}

# launch_and_type <master> <pane> <prompt>
# 确定性状态机:等 agent → 闭环信任框/重启 TUI → 等就绪标记 → 敲入 → 回执检测
launch_and_type() {
  local m="$1" PANE="$2" PROMPT="$3"
  local AG=$([ "$m" = "codex" ] && echo codex || echo claude)
  local IDLE="$(IDLE_MARK "$m")"
  local i tries
  i=0
  while [ "$i" -lt 30 ]; do
    [ "$(agent_of "$PANE")" = "$AG" ] && break
    i=$((i+1)); sleep 1
  done
  # boot settle: TUI 启动后约 60s 内 pane-run 输入会被吞(实测两代 TUI 皆然),
  # 就绪标记出现后再等一段死区时间才首次敲入。
  sleep 25
  local tries=0
  while [ "$tries" -lt 5 ]; do
    tries=$((tries+1))
    # 信任框闭环:codex 是「Folder access / Trust this folder?」对话框(回车即
    # 选 1. Trust and continue);claude 是小写「trust this folder」选择框(默认
    # 停在 No,需 down+enter)。shell 提示符出现 = TUI 已死,重启。
    local scr="$(screen "$PANE")"
    case "$scr" in
      *"Codex can read, edit, and run files"*|*"Folder access"*)
        "$HERDR" pane send-keys "$PANE" enter >/dev/null 2>&1
        sleep 5; ;;
      *"trust this folder"*)
        "$HERDR" pane send-keys "$PANE" down >/dev/null 2>&1
        "$HERDR" pane send-keys "$PANE" enter >/dev/null 2>&1
        sleep 4; ;;
      *"rocky@mymacbook"*|*"zsh:"*)
        "$HERDR" pane run "$PANE" "$(tui_cmd "$m")" >/dev/null 2>&1
        sleep 10; ;;
    esac
    # 就绪标记:agent 识别 + idle 标记同时成立才敲
    if [ "$(agent_of "$PANE")" = "$AG" ] && screen "$PANE" | grep -q "$IDLE"; then
      "$HERDR" pane run "$PANE" "$PROMPT" >/dev/null 2>&1
      sleep 8
      # 回执检测:codex 看 Context 是否离开 100%(且无对话框);claude 看 working
      local eaten=0
      local scr2="$(screen "$PANE")"
      case "$m" in
        codex)  case "$scr2" in *"Context 100% left"*) eaten=1;; *"Folder access"*) eaten=1;; esac;;
        claude) [ "$(status_of "$PANE")" != "working" ] && eaten=1;;
      esac
      if [ "$eaten" = 0 ]; then
        st "$m" "$(date +%s)" t_launch
        echo "[$m] 指令已被 TUI 接收(第 ${tries} 次敲入)"
        return 0
      fi
      # 吞字:esc 清框,长间隔(死区)后整体重来
      "$HERDR" pane send-keys "$PANE" esc >/dev/null 2>&1
      sleep 12
    else
      sleep 5
    fi
  done
  st "$m" "$(date +%s)" t_launch
  echo "[$m] WARN: ${tries} 次敲入后仍未确认接收,继续(窗口内可能自愈)" >&2
  return 0
}

for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
  WS="$WS_ROOT/$m"; mkdir -p "$WS"
  git -C "$WS" init -q 2>/dev/null
  printf 'the color word is: %s\n' "$(COLOR_OF "$m")" > "$WS/brief.txt"
  write_task_md_placeholder=1
  write_task_md "$WS" "$(SECRET_OF "$m")" "$([ "$m" = "claude" ] && echo 1 || echo 0)" "${E2E_HERDR_WS:-w8Z}"
  git -C "$WS" add -A; git -C "$WS" -c user.email=e2e@local -c user.name=e2e commit -qm seed 2>/dev/null
  st "$m" "$WS" ws
  ARGS="tab create --cwd $WS --label e2e-zc-$m-$TAGTS"
  [ -n "${E2E_HERDR_WS:-}" ] && ARGS="$ARGS --workspace $E2E_HERDR_WS"
  TABOUT=$("$HERDR" $ARGS 2>&1)
  PANE=$(printf '%s' "$TABOUT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d["result"].get("root_pane", {}).get("pane_id", ""))
except Exception:
    print("")')
  [ -z "$PANE" ] && { echo "[$m] FAIL: tab 创建: $TABOUT" >&2; st "$m" pane-failed out; continue; }
  st "$m" "$PANE" pane
  "$HERDR" pane run "$PANE" "$(tui_cmd "$m")" >/dev/null 2>&1
  launch_and_type "$m" "$PANE" "$(printf '只做一件事:完整执行当前目录 TASK.md 里的全部步骤,完成后告诉我 result1.txt 和 secret.txt 的内容。')"
done

echo "e2e root: $WS_ROOT · timeout: ${TIMEOUT}s · 等待各 master 完成 zcode 交接…"
DEADLINE=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  pending=0
  for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
    [ "$(gv "$m" out)" != "" ] && continue
    WS_M="$(gv "$m" ws)"
    R1=$(tr -d '[:space:]' "$WS_M/result1.txt" 2>/dev/null)
    SC=$(tr -d '[:space:]' "$WS_M/secret.txt" 2>/dev/null)
    PT=$(tr -d '[:space:]' "$WS_M/pane-task.txt" 2>/dev/null)
    ok1=0; ok2=0; ok3=1
    [ "$R1" = "$(COLOR_OF "$m")" ] && ok1=1
    [ "$SC" = "$(SECRET_OF "$m")" ] && ok2=1
    if [ "$m" = "claude" ]; then
      [ "$PT" = "integration" ] && ok3=1 || ok3=0
    fi
    if [ "$ok1" = 1 ] && [ "$ok2" = 1 ] && [ "$ok3" = 1 ]; then
      st "$m" done out; st "$m" "$(date +%s)" t_done; continue
    fi
    pending=1
  done
  [ "$pending" = 0 ] && break
  sleep 5
done

printf '\n== ZCode 集成 e2e 矩阵(%s) ==\n' "$(date +%H:%M:%S)"
overall=0
for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
  WS_M="$(gv "$m" ws)"; VERDICT=FAIL; REASON=""
  case "$(gv "$m" out)" in
    pane-failed) REASON="tab 创建失败";;
    "")          REASON="超 ${TIMEOUT}s 场景未全部达成";;
    done)
    VERDICT=PASS
    R1=$(tr -d '[:space:]' "$WS_M/result1.txt" 2>/dev/null)
    SC=$(tr -d '[:space:]' "$WS_M/secret.txt" 2>/dev/null)
    PT=$(tr -d '[:space:]' "$WS_M/pane-task.txt" 2>/dev/null)
    [ "$R1" = "$(COLOR_OF "$m")" ] || REASON="$REASON S1:result1=$R1(期望 $(COLOR_OF "$m"))"
    [ "$SC" = "$(SECRET_OF "$m")" ] || REASON="$REASON S2:secret=$SC(期望 $(SECRET_OF "$m"))"
    if [ "$m" = "claude" ] && [ "$PT" != "integration" ]; then
      REASON="$REASON S3:pane-task=$PT"
    fi
    [ -z "$REASON" ] && REASON=""
    [ -n "$REASON" ] && VERDICT=FAIL
    ;;
    *) REASON="未知状态";;
  esac
  [ "$VERDICT" = PASS ] || overall=1
  printf '%-8s %-4s %s\n' "$m" "$VERDICT" "${REASON:+reason:$REASON}"
done

if [ "$CLOSE" = 1 ]; then
  for m in $(printf '%s' "$MASTERS" | tr ',' ' '); do
    P="$(gv "$m" pane)"; [ -n "$P" ] && "$HERDR" pane close "$P" >/dev/null 2>&1
  done
  "$HERDR" pane list 2>/dev/null | WSROOT="$WS_ROOT" python3 -c '
import json, os, sys
root = os.environ["WSROOT"].replace("/var/folders/", "/private/var/folders/")
alt = os.environ["WSROOT"]
try: d = json.load(sys.stdin)
except Exception: raise SystemExit
for p in d.get("result", {}).get("panes", []):
    cwd = (p.get("cwd") or "") + (p.get("foreground_cwd") or "")
    if root in cwd or alt in cwd or (p.get("label") or "").startswith("e2e-zc-") or (p.get("label") or "") == "ZCode":
        print(p["pane_id"])' | while read -r p; do
    "$HERDR" pane close "$p" >/dev/null 2>&1
  done
  rm -rf "$WS_ROOT"
  echo "现场已清理"
else
  echo "窗口保留供围观;状态目录: $STATE"
fi
exit $overall
