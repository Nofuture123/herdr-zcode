# 主控 Agent 协作说明（Codex / Claude / pi 通用）

## 两种启动形态
- 会话模式（推荐并行多票）：`zcodecli chat-open` → 一窗一 ZCode 会话，用 `zcodecli --pane <id> send/result` 寻址。
- 收发台模式：`zcodecli open` → 串行队列窗（label zcode-bridge），直接 `zcodecli send/result`。
- MCP 加速器（Codex/Claude 已注入）：工具名 zcodecli 的 run/wait/inspect/cancel，语义与 CLI 相同。

## 何时委派给 ZCode
- 目标明确、验收可自动化、改动范围小的执行类任务 → 委派。
- 需求理解、架构决策、验收判断、跨任务协调 → Codex 亲自做。

## 如何写任务（run 工具参数）
- goal：一句话目标 + 明确约束（"只修改 X"）。
- workspace：独立 git 目录；同一时刻只跑一个任务（NAR 有工作区锁）。
- scope：允许修改的相对路径白名单。
- verify：可自动执行的检查命令（检查也是权限，须最小化）。
- mode：读任务用默认 plan；写任务必须 --mode edit。
- wait：设上限（如 300s）；超时≠失败，用 inspect 查真实状态。
- 权限现状：执行器默认 yolo+allow（所有者授权的全访问）。给不可信任务时在 JSON 里显式收紧
  （"mode":"plan"/"edit" + "policy":"deny"），或改执行器 env QAB_DEFAULT_MODE/QAB_DEFAULT_POLICY。
- idempotency-key：重试提交必须复用同一 key。
- 返工：--session-ref <原task_id> 复用原生会话；最多自动返工 2 轮，仍失败如实上报用户。

## 验收清单
inspect 看：status、worker_summary、diff.changed_files（是否越界）、verify 真实 exit code、usage。summary 为空或 verify 失败即不通过。

## 禁止
- 不在任务里要求删除文件、git push、改全局配置、付费操作。
- 不因执行端自称"完成"而跳过 verify 与 diff 核对。
- 不用短命 CLI 进程等待长任务（会孤儿化）；等待走常驻桥。
