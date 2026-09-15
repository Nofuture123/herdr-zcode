# 真实验证记录（2026-09-12，macOS，ZCode 0.16.5，NAR 1.0.0）

## 已验证（本机真实执行，非模拟）
| 项 | 证据 |
|---|---|
| nar doctor 全绿 | zcode CLI 指向 /Applications/ZCode.app/.../zcode.cjs，协议 0.16 支持 |
| 只读任务 | t-931f4e9c5395 succeeded，真实 usage 33833 tokens（来源 zcode:turn:turn.completed），git 零变更 |
| 修改任务 | t-6133e19b7cd2 succeeded（--mode edit），calc.py +3 行，verify 脚本通过 |
| 会话延续返工 | t-b559c71a4677 session_ref=t-6133e19b7cd2，resumed=true，同一 sess_944c9003 |
| 幂等重提 | 同 idempotency-key 返回原任务 t-6133e19b7cd2，未重复执行 |
| 越权防护 | plan 模式下 ZCode 无法写文件；scope 外文件改动会被 inspect 的 out_of_scope 标记 |
| 取消（已完成任务） | cancel 返回 "task already finished"，未假装取消成功 |
| 取消（执行中） | cancel 返回 confirmed=false / cancel_requested，区分"已请求"与"已确认停止"；zcode 子进程无残留 |
| 失败如实报告 | t-05a364fa86fc verification_failed（plan 模式无法写入），不谎报成功 |

## 未验证 / 已知缺口
- **Codex 主控完整链路**：MCP 已注册（codex mcp list 可见 qab），但"在 Codex 会话内发任务→验收→返工"的完整闭环尚未实跑。
- **套餐/额度优惠**：技术链路可用 ≠ 优惠确认。GLM Coding Plan 优惠是否覆盖外部驱动未向官方核实；token 数≠实际扣减量。账户归属（智谱国内 vs Z.AI 国际）未核验。
- **超时后任务孤儿**：CLI 短进程 + --wait 超时会导致任务标记 orphaned（诚实但中断）；长任务等待必须走常驻 MCP 桥（nar wait / MCP wait 工具），不要用短命 CLI 进程等待长任务。
- **强隔离不存在**：--scope/--forbid 是事后核对，不是沙箱。未验证 macOS Seatbelt 或 ZCode 沙箱能力。只在受控演示目录 (/Users/rocky/projects/qab-demo) 使用。
- **权限请求回传**：NAR 提供 policy allow/deny + 工具白名单，deny 时阻塞而非默认放行；但"权限请求实时回传给主控"的体验未实测。
- zcode-acp 未安装未测试（备选路线）。

## 复现方法
见 README "Setup" 和 "CLI use"；演示工作区 /Users/rocky/projects/qab-demo。

## 恢复 Codex 配置
cp ~/.codex/config.toml.bak-qab-20260912 ~/.codex/config.toml

## 插件层验证（2026-09-12 追加）
| 项 | 结果 | 证据 |
|---|---|---|
| herdr plugin link 注册 | ✅ | herdr plugin list 显示 qonnwolf.zcode-bridge enabled，5 个 action 识别 |
| 插件 action 实调 | ✅ | doctor/status 两个 action 经 herdr plugin action invoke 执行，log 状态 succeeded exit 0 |
| 固定路径运行时 | ✅ | ~/.local/share/qonnwolf-zcode-bridge/{venv,bin}，ensure-bridge 幂等，NAR 固定 v1.0.0 |
| Codex MCP 注入 | ✅ | codex mcp list 显示 qab enabled（手改块已回退，由插件 marker 块接管） |
| Claude MCP 注入 | ✅ | claude mcp list 显示 qab ✔ Connected |
| 共享技能安装 | ✅ | ~/.agents/skills/zcode-bridge/SKILL.md |
| **任意 CLI 端到端（Claude）** | ✅ | claude -p 经 mcp__qab__* 真实下发只读任务 t-fe283fd71d6b，ZCode 11s 返回摘要，7 turns 闭环；Claude 正确质疑了预存的未提交 diff（系此前 edit 任务所留，冒烟期间零新写入，git diff 仍为 +4 行） |
| Codex 主控端到端 | ⚠️ 未实跑 | MCP 已连通（同 wrapper），下次在 Herdr 的 Codex 会话中委派即可复现 claude 冒烟流程 |

### 复现
```bash
herdr plugin action invoke qonnwolf.zcode-bridge.doctor
claude -p "Use the qab MCP tools to submit a read-only task on /Users/rocky/projects/qab-demo ..." --allowedTools "mcp__qab__*"
```

## Herdr 面板架构验证（2026-09-12 第二轮）
| 项 | 结果 | 证据 |
|---|---|---|
| 窗口占位 | ✅ | 插件 [[panes]] executor，标签 zcode-bridge，窗口号 w73:p2X→p2Z→p20（重启换号，qab/zcodecli 按标签自动解析） |
| 最小 PATH 环境 | ✅ | herdr 插件命令环境无 node → ensure-bridge 现解析绝对路径并注入 env.sh；env -i 模拟下 nar doctor 全 OK |
| 面板发"你好"→ZCode 回复 | ✅ | t-c555f5baec1d，ZCode 面板内中文回复"我是 ZCode…" |
| 面板 JSON 修改任务 | ✅ | t-0eedd2b95119 succeeded，README.md 追加 hello-from-zcode-bridge，verify grep 通过，结果落盘 results/ |
| 改名 zcodecli | ✅ | bin/{nar,zcodecli,zcodecli-mcp}；MCP 服务更名后 claude ✔ Connected、codex enabled；旧 qab 已清除 |
| 共享 skill | ✅ | ~/.agents/skills/zcode-bridge + pi/claude 技能目录符号链接；内容改为面板协议+触发词 |
| 已知坑（已规避/记录） | ⚠️ | herdr pane wait-output 只匹配未来输出（zcodecli result 已先查现有）；recent-unwrapped 源对此类面板返回空（改用 visible）；面板 REPL 曾因最小 PATH 静默失败（doctor 日志暴露） |

### 复现（终端逐步执行）
```bash
zcodecli open
zcodecli send "你好"        # 立即返回 sent to pane <id>
zcodecli read               # 看 [zcodecli:busy] / ZCode 回复
zcodecli result             # 打印最新任务结构化结果
```

## 工作区跟随调用方（2026-09-12 追加）
- ✅ zcodecli send 自动携带调用者 cwd 为任务工作区（t-52a61f2870c2：从 qonnwolf-agent-bridge 发送，ZCode 在该仓库 plan 模式待命并描述了仓库内容）；--workspace 可覆盖；纯文本仍为只读。
- 说明：headless 会话存于 ~/.zcode/cli/rollout/model-io-sess_*.jsonl（本人登录），不出现在 ZCode 桌面 App 会话列表（App 只列 GUI 会话）——属官方 headless 模式设计；套餐是否同等计费仍未向官方核实。

## HERDR_ENV 与进程环境核验（2026-09-12）
- ✅ 执行器面板进程由 herdr 注入完整环境：HERDR_ENV=1、HERDR_PLUGIN_ID、HERDR_PLUGIN_ENTRYPOINT_ID=executor、HERDR_TAB_ID、HERDR_PLUGIN_STATE_DIR（ps -E 实证）。
- ✅ 桥外调用（本 agent shell，HERDR_ENV unset）经 herdr CLI 用户级 socket 操作面板/插件全部正常——HERDR_ENV 是面板内 agent 控制会话的守卫标记，不是 socket 鉴权条件。
- ⚠️ herdr 会话重启后旧面板 REPL 进程可能残留（挂在失效 pty 上），已手动清理；待办：executor-pane.sh 启动时清理同插件旧 REPL 进程。
- ✅ zcodecli/nar 已符号链接到 /opt/homebrew/bin，任何 CLI/交互 shell 裸命令可用。

## 程序化多跳通讯（2026-09-12 终验）
- ✅ 无焦点切换的 agent 链：本 agent（herdr 外）`herdr pane run` 发任务给 pi 面板 → pi 自动委派 ZCode（zcodecli send → 执行器面板 → NAR → ZCode）→ pi 读取结果并总结 → 本 agent `pane read` 读回报告。全程 51s / 6 条命令，无任何人键盘输入，无焦点切换。
- 结论：人对 master 说话只需 master 所在面板有焦点；master 与执行端之间全部经 herdr 协议程序化转发。

## 工具能力核验（2026-09-12）
- ✅ 执行器内 ZCode 自报 26 个工具：原生 Bash/Edit/Write/Read/WebFetch/WebSearch/Agent/Skill/TodoWrite/EnterPlanMode 等 + 自有 MCP（node_repl/web_reader 等）——headless 加载同一 ~/.zcode 配置，工具能力为完整版。
- ✅ 能力放行由桥的策略控制：纯文本=plan 只读；JSON 任务=edit+scope 白名单+verify；policy deny 阻断未预授权工具调用。
- ⚠️ 未核验：桌面 GUI 独占能力（computer-use 类）headless 下不可用；结果标记行过长会被终端折行破坏 JSON——已改为短标记（task_id/status/ok），完整结果按 results/<task_id>.json 约定落盘。
- ⚠️ 事故记录：清理旧会话 REPL 时误杀正在执行用户任务（桌面建 txt）的进程致其 orphaned；该任务本为 plan 模式不可写，桌面亦在任何 scope 之外——安全边界按预期拦截。

## 最大权限模式（2026-09-12，所有者授权）
- ✅ 执行器默认 yolo + policy allow（env QAB_DEFAULT_MODE/QAB_DEFAULT_POLICY 可全局收紧，单任务 JSON 可单独收紧）。
- ✅ 工作区外写入实测：Bash 写 /tmp/zcodecli-yolo-test.txt（JSON 任务，verify grep 通过）；纯文本路径同样可写（/tmp/zcodecli-plain.txt）。
- computer-use 不加载（headless 本就不含），无需处理。

## Herdr Agent Integration（对照 pi integration，2026-09-12）
参考 /Users/rocky/.pi/agent/extensions/herdr-agent-state.ts 移植其 socket 协议到执行器 REPL（Python stdlib，~50 行）：
- ✅ pane.report_agent：面板被 Herdr 识别为 agent "zcode"，状态跃迁真实可用——启动 idle → 任务中 working → 完成 idle（后台标签自动升级为 done，与 pi 行为一致）。此前面板是 agent:unknown。
- ✅ 队列化 best-effort 上报（0.5s 超时，socket 故障不阻塞任务队列）。
- ⚠️ pane.report_agent_session：herdr 返回 ok 但不为未知 integration 来源持久化 agent_session（pi/codex 的会话展示可用）——判定为 herdr 对 session 元数据的防伪造白名单。按"不伪造客户端身份"原则不做来源欺骗；会话追踪由桥自身承担（results/、/status、/continue、nar inspect）。
- pi integration 中未移植的部分及理由：blocked 状态（依赖宿主权限弹窗事件；桥用 policy 策略面非弹窗）、TUI mode 门控（我们是自管面板）、SessionStart 生命周期（无宿主会话系统）。

## 与 pi integration 的最终差距核对（2026-09-12）
已补齐三个健壮性细节：socket 发送失败重试（0.5s→1.5s 各一次）；状态去重（仅变化时上报，启动 force）；启动时从最新 results 预载最近原生会话 ID。
不适用项（pi 宿主特有，已在源码层面对照确认）：blocked 状态（宿主权限弹窗事件）、TUI mode 门控、SessionStart/agent_start 宿主生命周期、会话文件路径上报。
结论：功能与健壮性对齐 pi integration；仅会话元数据的 UI 展示受上游白名单限制（已记录）。

## 会话模式（一窗一会话，2026-09-12）
- ✅ zcodecli chat / chat-open：每面板一个独立 ZCode 原生会话；同面板跨轮记忆（"我叫Rocky"→"Rocky"）；跨面板隔离（新面板答"不知道"，sess_cd06… ≠ sess_35f3…）。
- ✅ 3 个 zcode agent 面板并存（reception + 2 chat），各自独立 idle/done 状态。
- ✅ zcodecli --pane <id> 精确寻址任意面板；10 会话 = 开 10 个 chat 面板。

## 修复轮回归（2026-09-12，按 docs/REMEDIATION-PLAN.md）
- ✅ broker 信任管道：send 收 [zcodecli:accepted] 回执（仅标识符）；result 按 request_id 寻址，证据只读 results/<task_id>.json（task_id 正则校验防穿越）；桥只报事实（status/verify_ok/out_of_scope），验收由 master 决定。
- ✅ 退出码：成功 0 / verify 失败或未验证不通过 3 / 提交被拒 5 / 无结果 1（实测 R1/R2/reg-005）。
- ✅ fail-closed：坏 JSON、缺 goal、未知字段、非法 mode/timeout/workspace 全部拒绝（不再降级为 yolo 文本任务）；edit/yolo 结构化任务缺 verify 直接拒。
- ✅ 幂等：同 key 同内容 → duplicate 回执；同 key 不同内容 → idempotency_conflict（指纹 sha256）；fcntl 跨进程锁（单测 12/12 含并发）。
- ✅ 返工：/continue 继承原 spec 全上下文，链上最多 2 轮，第 3 轮拒。
- ✅ 真取消：轮询循环内监听 /cancel → 同进程 kernel.cancel → NAR status=cancelled + headless 进程确认停止 + herdr 状态 done。
- ✅ 安装硬化：NAR 钉死 d65bd49（上游 v1.0.0 tag 已移动 afb7312，现装包 direct_url 校验 commit 一致才跳过重装）；doctor 门禁只认行首 [X]；glue 脚本去掉 || true。
- ✅ MCP：zcodecli-mcp 变为 pane 通道代理（submit/result/list/inspect/cancel），不再私持 NAR Kernel；实测 submit→result accepted=true。
- 待办已清（MVP 收敛轮 2026-09-12）：executor fake-kernel 测试（tests/test_executors.py，18/18）；桥去决策化（删 accepted 判定与返工上限，验收纪律上收 master skill）；幂等状态机 indeterminate 防盲跑；/continue 显式父请求+真实 session_ref（实测返工延续原生会话 root-v1 CONTINUED）；cancel 三态 already_terminal/cancelled/cancel_requested；输出 sanitize 限长；文档矛盾清理。按 Astra 裁决砍掉：GUI gate、自动返工决策、验收判定（上收 master）。

## Codex 5.6-sol (high) 修复后复审（2026-09-12，基线 2c5175c）
总评：broker 信任管道、fail-closed、verify 门禁、幂等指纹、返工上限、安装 SHA 校验已落实；但复审判定多项为"部分落实/未落实"：
- P0 未落实：GUI gate 仅进提示词非工具权限（NAR forbid 语义）；/continue 未设置 session_ref（实际开新会话而非延续）。
- P0 部分：幂等 claim/attach 间有崩溃窗口（task_id=None 永久卡死）；结果文件可被 marker 字段拼接（客户端应只信自己 request_id 的信号+文件全字段校验）；cancel 把自然完成也算 confirmed；权限 0644/0755 残留（env.sh/doctor.out/wrapper）；venv 重装先 rm -rf 非原子。
- P1 未落实：owner/重启恢复、错误脱敏、executor/CLI/MCP 自动化测试、文档同步（README 仍写 tag 固定、纯文本只读等）。
- 客户端语义：result 应"只信自己 request_id 的信号 + 磁盘文件全字段校验"，忽略 marker 业务字段。
详细：本次审计对话记录（file:line 级）见审计输出；下一轮修复按此清单执行。

## 第四轮（2026-09-12 深夜）：复审阻断项修复
- ✅ nonce 绑定：send 注入 nonce，accepted 信号回显，按 nonce 认领自己的回执（实测 aeb1b8b9）。
- ✅ 取消路由：zcodecli cancel <tid> 路由 /cancel <tid> 到 owner 面板；drain 识别带 tid 的取消（修复被 busy 拒绝的 bug）；三态实测：already_terminal / cancel_requested（最终态诚实上报"verify manually"）。
- ✅ busy 状态机：chat 超时→background_wait 线程持续持有 busy 至 terminal；submit 异常即清 busy；drain 期非取消行即时 busy 错误。
- ✅ CLI result 三段信号解析修复（此前 6 段检查导致主路径必失败——复审抓到的确定性断链）。
- 遗留：NAR 库中 cancel_requested 终态转换依赖上游；Codex 端到端证据见下一节。

## Codex 端到端闭环证据（2026-09-12，关闭最后阻塞项）
- ✅ codex exec（gpt-6-astra）经 zcodecli MCP 真实委派：submit(e2e任务) → ZCode 执行 → result 取回：r-085f6216383699a8 / t-d136dfef3b3e / succeeded / summary "e2e-ok"。三 CLI（pi/Claude/Codex）全链路证据齐备。
- 注：codex exec 非交互模式默认拒 MCP 工具调用（approval policy never），需 --dangerously-bypass-approvals-and-sandbox 跑通；交互模式无此问题。

## 第五轮（收敛补丁，2026-09-12）
- ✅ nonce 归属：生效 nonce=调用方自带优先；accepted/error 信号均绑定 nonce（实测回执 6ecd9f39）。
- ✅ task→owner 账本：attach 时记录 task_id→pane_id；zcodecli cancel 按 owner 账本路由（不再盲发默认面板）。
- ✅ cancel_requested 非终态：busy 保持 working 直至 terminal（后台 waiter 收口）；不再提前释放。
- 状态：Astra MVP 验收线 14-18 全部有实测证据；等待 5.6-sol 收敛确认。

## 第六~九轮收敛（2026-09-12/13）
- ✅ 复审全项关闭：3-token 信号、证据严格一致、nonce 全分支回显、cancel_requested 非终态 busy 保持、未知/陈旧任务明确拒绝并提示、README/SKILL 一致化、run-tests.sh 真实退出码门。
- ✅ 路由取消实测（sleep 任务）：cancel → cancel_requested（10s 诚实等待）→ kill → interrupted（终态）；进程确认停止；busy 全程真实。
- ⚠️ 已知小项：client cancel 的结果扫描窗口已放宽至 35s；NAR 文件库对 running 任务的可见性有延迟（以执行器面板/结果文件为准）。

## 发版门禁：多主控 × 多窗口分发矩阵（2026-09-15 起，强制）
`scripts/e2e_dispatch.sh`——**交互式**真机矩阵：每个主控（CLI+模型 TUI）常驻在自己的
独立 herdr tab 里（全程人可围观），harness 等其就绪后把派票指令敲进 TUI；主控自行
`zcodecli chat-open` 开独立 zcode 窗口（v0.7.3 workspace 定位），把一张 edit+verify 票
派到独立 git 工作区。harness 只信磁盘证据（requests/results）+ notes.txt 实际内容裁决，
任一主控 FAIL 整体 exit 1。默认保留窗口供检查，`--close` 才清理。

- 三主控交互基线（2026-09-15 20:52 实测，全 PASS）：

  | 主控 | 派发 | 执行 | 全程 | 备注 |
  |---|---|---|---|---|
  | codex gpt-5.6-luna(low) | 15s | 20s | 35s | 第 1 次敲入被 TUI 吞，重试兜住 |
  | claude sonnet | 9s | 12s | 21s | 信任对话框自动应答 |
  | pi deepseek-v4.1-flash | 2s | 11s | 13s | 一次成功 |

- 延迟构成（同日微基准）：chat-open 0.1s；**send→回执 0.6s（纯传输+接受）**；执行
  11–25s = ZCode 模型推理。dispatch 的大头是主控 TUI 启动 + LLM 轮次；0.6s 的传输层
  才是桥自身的延迟，慢了先分层再排查。
- 用法：`E2E_MASTERS="codex,claude,pi" bash scripts/e2e_dispatch.sh [--timeout 600]
  [--close]`；`--dry-run` 只看计划。任何 FAIL → 不得发版（PUBLISHING.md 门禁）。
- 主控 TUI 的三个坑（harness 已自动处理，值得知道）：
  1) claude 新目录首次启动弹工作区信任框且默认停在 "No, exit"——检测到即 down+enter 选 Yes；
  2) codex TUI 初始化期敲入的文本会**整段丢失**——敲入后校验「agent 转 working 或输入框
     可见指令片段」，否则补 Enter / 重敲（最多 3 次）；
  3) 跨轮证据撞车——票 tag 带运行时间戳，且只认 `ts > 本次启动` 的请求，否则上一轮的
     旧票会秒判本轮。
- 失败分层：超时未派票（herdr core 输入黑洞 / TUI 吞输入）≠ verify 失败（执行方或票面
  verify 写错）≠ 证据缺失（主控没走完流程）——三种锅分属 herdr / 执行方 / 主控。
- 环境注记：codex 走 ChatGPT 账号**周配额**（2026-09-15 只剩 ~1%，跑矩阵前先 /status
  看一眼）；pi 在 herdr 外直跑会打一条 pi-herdr-orchestrator 扩展报错（HERDR_ENV 门禁，
  无害噪音）。
- 已知上游风险（2026-09-15）：herdr 0.8.2 core 存在「pane 输入黑洞」——存活中的 pane
  会被静默停止转发输入（输出不受影响；rename/焦点切换/纯时间老化均排除，触发源未定，
  无 pane.move 记录的 pane 也中过）。症状即超时未派票：`pane run`/`send-text`/
  `send-keys` 全部无声丢失。绕行：重开面板。e2e FAIL 时先区分这一层。
