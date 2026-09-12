# 修复计划（Codex 审查基线 6a4a501）

本计划只修复基线 `6a4a501f2599155c8e23494ad7558298d66cb22b` 已确认的协议、并发、安全、可验证性和文档问题；不重写 Herdr、ZCode 或 native-agent-router（下称 NAR），不改变“Herdr pane 是传输层”的产品方向。

## 一、目标架构（broker/协议：request_id 生命周期图、结果只信磁盘、marker 仅信号、同进程 Kernel）

### 1. 单一协议

- 新增 `scripts/broker_protocol.py`：集中定义 ID 校验、请求/结果 schema、0600 原子 JSON 写入、0700 目录创建、`fcntl.flock` 锁、幂等索引和任务 owner 索引。`executor_repl.py`、`executor_chat.py`、`zcodecli_cli.py`、MCP 共用，禁止各自复制协议逻辑。
- 新增 `scripts/pane_client.py`：只负责生成请求文件、向目标 pane 发送 `/request <request_id>`、等待该 request 的 signal、读取并验证该 request 的结果文件。CLI 与 MCP 共用。
- `request_id` 固定为 `r-` 加 32 位小写十六进制，正则为 `^r-[0-9a-f]{32}$`；`task_id` 固定校验 `^t-[0-9a-f]{12}$`。任何 ID 在拼路径、查 owner、调用 Kernel 前都必须先通过校验。
- pane stdin 的机器协议只接受 `/request <request_id>`；直接输入 `{...}`、未知 `/命令`、畸形 JSON 或裸任务正文均拒绝，不再回退成普通任务。
- 人类仍可用 `zcodecli send "文本"`：客户端将其封装成 `op=chat`、`mode=plan`、`permission_policy=deny` 的只读请求。JSON 任务封装成 `op=run`；这两类在磁盘 schema 上明确区分。

### 2. 磁盘布局与信任边界

运行时根目录保持 `~/.local/share/qonnwolf-zcode-bridge/`，新增：

```text
requests/<request_id>.json        # 客户端提交的不可变请求，0600
results/<request_id>.json         # broker 状态/最终结果，0600，原子替换
owners/<task_id>.json             # task_id -> request_id + pane_id，0600
state/idempotency.json            # idempotency key 哈希 -> task/request，0600
locks/request-<request_id>.lock   # 单 request claim，0600
locks/idempotency.lock            # 跨进程 submit 临界区，0600
```

- 根目录及 `requests/`、`results/`、`owners/`、`state/`、`locks/` 均为 0700；进程入口先执行 `umask(0o077)`，安装脚本先执行 `umask 077`。
- JSON 只能用同目录临时文件写完、`fsync`、`chmod 0600` 后 `os.replace`；读端拒绝符号链接、非普通文件、owner 不是当前 uid、权限组/其他人可读写、超限文件、非 UTF-8、重复键、非对象根值和 schema 不匹配。
- marker 统一为 `[zcodecli:signal] <request_id> <state>`，其中 `state` 仅允许 `accepted|updated|terminal|error`。marker 只用于缩短等待，不携带 `task_id`、`ok`、摘要、diff、verify 或错误详情；看见 marker 后仍必须读盘。
- 唯一可信业务结果是 `results/<已验证 request_id>.json`；任务详情由同一进程 Kernel 从 NAR 的 `tasks/<已验证 task_id>.json` / `logs/<task_id>/` 读取后写入上述结果文件。终端可见文本、pane 历史、stdout JSON、marker 内容均不得作为结果。

### 3. request_id 生命周期

```text
CLI / MCP
  │  1. secrets.token_hex(16) 生成 request_id；校验 payload
  │  2. O_EXCL + 原子写 requests/<request_id>.json (0600)
  │  3. herdr pane run <owner-pane> "/request <request_id>"
  ▼
目标 executor pane
  │  4. 校验 request_id；flock 单 request；安全读请求文件
  │  5. 写 results/<request_id>.json: accepted；打印 signal
  │  6. 对 run/continue：在 idempotency flock 内由本进程唯一 Kernel.submit
  │  7. 写 owner(task_id→pane_id)；后台 Kernel.wait；主循环继续接收 cancel/inspect
  │  8. Kernel 到 terminal/blocked 后写完整结果，再打印 signal
  ▼
CLI / MCP
  │  9. 只等待匹配本 request_id 的 signal；超时也再读一次磁盘
  │ 10. 校验结果文件 version/request_id/state/task_id/schema
  └─ 11. 依据磁盘结果决定 stdout 与进程/MCP 结果；不解析 pane 结果正文
```

- 同一个 `request_id` 只能 claim 一次；重复 signal 只返回同一磁盘结果，不重做任务。
- `op=run` 必须带非空 `goal`、绝对且存在的 `workspace`、非空 `scope_files`、非空 `verify` 数组、合法 `mode`/`permission_policy`、1–3600 秒 `timeout_sec` 和 1–128 字符 `idempotency_key`。未知字段、错误类型、空字符串、越界相对路径一律拒绝。
- `op=chat` 只允许 `goal`、`workspace`、`timeout_sec`，broker 强制 `mode=plan`、`permission_policy=deny`、空 scope；调用方不能覆盖。
- `op=continue` 必须显式携带 `parent_request_id` 与新 `goal`。broker 从父 request/result 文件继承 `workspace`、`scope_files`、`forbid`、`verify`、`mode`、`permission_policy`、`timeout_sec` 和根链信息，仅把父 `task_id` 设为 `session_ref`。不得从“最近任务”或进程内 `last_task` 推断上下文。
- 根请求 round=0；继续请求保存同一 `root_request_id`，round 加一，最多 round=2。第三次继续返回协议错误且不调用 Kernel；继续任务的幂等键固定派生为 `continue:<root_request_id>:<round>`，重投不会创建第二个 NAR task。

### 4. 同进程 Kernel 与控制面

- 每个 `executor_repl.py` / `executor_chat.py` 进程启动时只构造一次 `Kernel(load_config(...), TaskStore(...))`，任务提交、等待、inspect、cancel 都调用该对象；删除 executor 内对 `nar run/wait/inspect/cancel` 的 `subprocess.run`。
- `Kernel.submit(..., wait_sec=None)` 立即返回；后台线程调用同一 Kernel 的 `wait` 并落盘，stdin 主线程保持可响应。一个 reception pane 同时只允许一个活动 run/continue；第二个返回 `busy`，不静默排队。
- `owners/<task_id>.json` 记录拥有该任务内存态的 pane。`zcodecli cancel <task_id>` 和 MCP cancel 先读 owner，再把 cancel request 发回该 pane；owner 不存在、pane 已死或任务不属于该 Kernel 时 fail closed，绝不退回短命 `nar cancel`。
- owner pane 收到 cancel 后调用本进程 `Kernel.cancel(task_id)`；只有返回 `confirmed=true` 才报告“已停止”。`requested=true, confirmed=false` 必须保留 `cancel_requested`/真实状态并以失败退出码返回。
- 安装时生成 bridge 专用 NAR 配置，给 `zcode` 设置显式 `tool_allowlist`。允许表只包含已验收的文本/代码工具（`Read`、`Grep`、`Glob`、`Bash`、`Edit`、`Write`、`WebFetch`、`WebSearch`、`Agent`、`Skill`、`TodoWrite`、`EnterPlanMode`）；所有 computer-use、GUI、desktop、screen、mouse、keyboard、browser-control 类工具不进入允许表，新出现的工具默认不可用。启动时若允许表包含上述禁用类别或 NAR 未回报 `toolAllowlist` 能力，executor 拒绝 ready。

## 二、P0 修复项（每项：改哪个文件、具体改法、验收标准）

### P0-1：落地 request-id broker，结果只读可信磁盘

- 改哪个文件：新增 `scripts/broker_protocol.py`、`scripts/pane_client.py`、`tests/test_broker_protocol.py`；修改 `scripts/zcodecli_cli.py`、`scripts/executor_repl.py`、`scripts/executor_chat.py`。
- 具体改法：按第一节实现请求/结果 schema、严格 ID、私有目录、原子文件、精确 signal 和 request claim；移除 `zcodecli_cli.py:147-173` 从 pane 可见文本抓最后 marker 并直接信任 JSON 的逻辑；移除两个 executor 将 NAR stdout 当完整结果再自行转存的逻辑。`result` 必须接收明确 `request_id`；无参数的“最新结果”模式删除，以免多调用方串结果。
- 验收标准：伪造/旧/折行 marker、别的 request marker、缺失结果文件、ID 路径穿越、结果内 request/task ID 不一致、权限过宽或 JSON 截断时均非零失败；正确请求只从 `results/<request_id>.json` 得到完整 summary/diff/verify/usage。

### P0-2：JSON fail closed，并强制结构化任务 verify

- 改哪个文件：`scripts/broker_protocol.py`、`scripts/zcodecli_cli.py`、`scripts/executor_repl.py`、`scripts/executor_chat.py`、`tests/test_request_validation.py`。
- 具体改法：使用 `json.loads(..., object_pairs_hook=...)` 拒绝重复键；按 op 白名单逐字段校验并拒绝未知字段。以 `{` 开头但解析失败、JSON 根非对象、缺 goal/scope/verify/idempotency key、verify 为空/非字符串数组、scope 为绝对路径或含 `..` 时直接写 error 结果，不再降级为聊天。只有 `op=chat` 可无 verify，且强制 plan+deny；所有 `op=run`，包括显式 plan 模式，都必须提供至少一条 verify。
- 验收标准：上述每一类非法输入都不调用 `Kernel.submit`；合法 run 传给 Kernel 的 verify 与磁盘请求完全一致，最终 `ok` 还必须同时满足 NAR succeeded、所有 verify exit_code=0、out_of_scope 为空和 worker_summary 非空。

### P0-3：同进程 Kernel、真实取消与 owner 路由

- 改哪个文件：新增 `scripts/broker_runtime.py`、`tests/test_broker_runtime.py`；修改 `scripts/executor_repl.py`、`scripts/executor_chat.py`、`scripts/zcodecli_cli.py`。
- 具体改法：`broker_runtime.py` 持有每进程唯一 Kernel、活动 task/request 映射和后台 waiter；executor 主线程仅做协议分发。run 不带 `--wait`，cancel 必须回 owner pane 并调用同一对象的 `Kernel.cancel`。所有异常路径在 `finally` 更新磁盘结果、agent 状态和活动槽，但不得把 unconfirmed cancel 改写成 cancelled。
- 验收标准：假 Kernel 测试证明 submit 与 cancel 的对象 identity 相同；活动任务取消返回 confirmed=true 才是 cancelled/exit 0；confirmed=false 为非零且 task 仍可 inspect/wait；任务完成后取消返回 already finished，不产生第二任务或残留活动槽。

### P0-4：`/continue` 继承上下文并限制两轮

- 改哪个文件：`scripts/broker_protocol.py`、`scripts/broker_runtime.py`、`scripts/zcodecli_cli.py`、`scripts/executor_repl.py`、`scripts/executor_chat.py`、`tests/test_continue.py`。
- 具体改法：CLI 语法改为 `zcodecli continue <parent_request_id> <goal>`；兼容显示名 `/continue` 时也必须显式给父 request ID。删除 `last_task`、`first_task` 作为安全上下文来源。继续请求只接受 parent+goal，从父链文件恢复不可变执行上下文，设置 `session_ref=parent.task_id`，保存 root/round/parent，并用 root+round 派生幂等键。
- 验收标准：round 1、2 的 workspace/scope/forbid/verify/mode/policy/timeout 与根请求逐字段相等且 native session 延续；round 3、父结果缺失/非终态、父 ID 非法、父 task_id 非法均在 submit 前拒绝。

### P0-5：跨进程幂等使用 `fcntl`

- 改哪个文件：`scripts/broker_protocol.py`、`scripts/broker_runtime.py`、`tests/test_cross_process_idempotency.py`。
- 具体改法：对 `locks/idempotency.lock` 使用 `fcntl.flock(LOCK_EX)`，在同一临界区完成幂等键哈希查询、`Kernel.submit` 和 `state/idempotency.json` 原子更新；索引存在但 task 文件缺失时返回 `idempotency_state_corrupt`，不自动重跑。所有 reception/chat/MCP 路径必须经过该入口，禁止直接调用 NAR submit 绕过锁。
- 验收标准：两个独立 Python 进程用同一 key 同时提交，最终只有一个 task_id、一次 submit 记录，两个 request 都指向同一 task；不同 key 可分别提交；异常退出后锁由内核释放且索引不产生半条记录。macOS/Linux 之外启动时明确报 unsupported，不做无锁降级。

### P0-6：computer-use/GUI 工具硬拒绝

- 改哪个文件：`scripts/ensure-bridge.sh`、新增 `scripts/nar_config.py`、`scripts/broker_runtime.py`、`tests/test_tool_gate.py`。
- 具体改法：`nar_config.py` 生成并校验 bridge 专用 `agents.json`，通过 NAR 的 `tool_allowlist` 只开放第一节列出的代码工具；executor 显式以该配置构建 Kernel。normal/yolo 与 permission allow 不能覆盖该表；用户请求中的 `tool_allowlist`、环境中的追加工具项和未知工具字段一律拒绝。启动探针不通过时不输出 `[zcodecli:ready]`。
- 验收标准：配置与请求大小写/连字符归一化后均不能出现 `computer`、`gui`、`desktop`、`screen`、`mouse`、`keyboard`、`browser-control`；测试证明 yolo+allow 下仍只向 ZCode session 发送安全 allowlist，探针失败时 executor 退出非零。

### P0-7：运行时权限统一为 0600/0700

- 改哪个文件：`scripts/ensure-bridge.sh`、`scripts/install-skill.sh`、`scripts/inject-mcp.sh`、`scripts/broker_protocol.py`、两个 executor 入口、`tests/test_permissions.py`。
- 具体改法：所有入口设置 077 umask；数据目录 0700，普通数据/配置/日志/备份 0600，可执行 wrapper 0700。创建后显式校验权限；发现既有文件/目录更宽时收紧，收紧失败即停止。禁止跟随运行时目录内符号链接。
- 验收标准：在 umask 022 环境运行安装、提交、完成、继续、取消后，目录均为 0700，JSON/日志/config/env/备份均为 0600，wrapper 为 0700；预置恶意 symlink 时安装/读写失败且目标文件不变。

### P0-8：NAR 安装固定 commit 且全链 fail closed

- 改哪个文件：`scripts/ensure-bridge.sh`、`scripts/bridge.sh`、`scripts/executor-pane.sh`、`scripts/inject-mcp.sh`、`herdr-plugin.toml`、`tests/test_install_contract.py`。
- 具体改法：把安装规格改为 `native-agent-router @ git+https://github.com/BerineYang/native-agent-router.git@d65bd49755bf4f6637b3c103650175b1b789e3ae`；不要再用可移动 tag `v1.0.0` 作为信任依据。安装到临时 venv，读取 dist-info `direct_url.json` 验证 `commit_id` 精确相等并运行 doctor，通过后才原子切换；任一步失败保留旧可用 venv并返回非零。删除 `bridge.sh:4`、`executor-pane.sh:6,9`、`inject-mcp.sh:6` 的 `|| true`；ZCode、node、python、pin 或 doctor 缺失均阻止 wrapper/MCP/pane 宣称成功。
- 验收标准：正确 SHA 安装 exit 0；tag、错误 SHA、缺 direct_url、pip 失败、doctor 失败均 exit 非零且不生成/切换可用 wrapper；已有错误版本不会输出 `install: present`；最终 `direct_url.json` 的 commit 精确为指定 40 位 SHA。

### P0-9：CLI exit code 表达真实结果

- 改哪个文件：`scripts/zcodecli_cli.py`、`scripts/pane_client.py`、`tests/test_cli_exit_codes.py`。
- 具体改法：定义并集中实现退出码：0=请求被接收或验收通过；1=业务失败、verify 失败、越界、空摘要、取消未确认；2=参数/schema/协议/owner 错误；124=等待超时且结果仍非终态；Herdr transport 启动失败保留其非零码。删除 JSON 解析失败仍 `return 0`、NAR rc 被内容掩盖、send 无条件打印 sent 等路径。`--json` 输出严格单个结果对象到 stdout，人类装饰输出只到 stderr/TTY。
- 验收标准：参数化测试覆盖 succeeded、failed、verification_failed、out_of_scope、empty summary、cancel confirmed/unconfirmed、protocol error、transport error、timeout；shell 看到的 `$?` 与磁盘状态一致。

## 三、P1 修复项（同上，较简略）

### P1-1：MCP 改为 pane 协议代理

- 改哪个文件：新增 `scripts/zcodecli_mcp.py`、`tests/test_zcodecli_mcp.py`；修改 `scripts/ensure-bridge.sh`、`scripts/inject-mcp.sh`、`herdr-plugin.toml`。
- 具体改法：wrapper 不再执行 `python -m native_agent_router.mcp_server`，改执行本仓库 MCP。保留 `agents/run/wait/inspect/cancel` 五个工具名，但全部调用 `pane_client.py`：写 request、向 owner pane 发 signal、读结果文件；不得直接构造 Kernel 或启动 `nar` 子进程。MCP 的 `run` 返回 request_id/accepted，`wait` 以 request_id 为主键，cancel 通过 owner pane；协议错误抛 MCP `isError`，业务失败返回稳定 `ok/status/code`。
- 验收标准：mock Herdr 测试证明五个工具均经过 pane client；静态搜索 `zcodecli_mcp.py` 不得出现 `native_agent_router.mcp_server`、`Kernel(` 或 `[NAR, ...]`；真实 smoke 能从 MCP run→wait 读到同一 request_id 的磁盘结果。

### P1-2：owner、重启与状态恢复可诊断

- 改哪个文件：`scripts/broker_runtime.py`、两个 executor、`scripts/zcodecli_cli.py`、`tests/test_owner_recovery.py`。
- 具体改法：owner 文件包含 pid、pane_id、request_id、created_at；cancel 前核验 pid/pane 存活。executor 重启只把本 pid 遗留的 active 请求标为 interrupted/unknown，不接管取消、不自动重跑；`inspect` 明确返回 owner_lost。Herdr agent state 由活动槽派生，所有异常路径最终回 idle。
- 验收标准：模拟 pane 崩溃后 result/inspect 保留，cancel 非零说明 owner_lost，重启不产生新 task；working→idle 状态各只上报一次。

### P1-3：错误信息最小化且可诊断

- 改哪个文件：`scripts/broker_protocol.py`、`scripts/broker_runtime.py`、两个 executor、MCP、测试。
- 具体改法：磁盘结果保存稳定 `code` 与截断、去控制字符的 message；pane marker 不含 stderr；CLI 默认不回显原始 NAR/ZCode 输出。详细诊断只经已验证 task_id 调 `inspect(..., diagnose)`，沿用 NAR 脱敏结果。
- 验收标准：含控制字符、超长 stderr、疑似 token/email 的 fake 错误不会进入 marker或默认 stdout；diagnose 仍可按需取得限长信息。

### P1-4：自动化测试与单命令验证

- 改哪个文件：新增 `tests/` 下上述测试、`scripts/run-tests.sh`；修改 `README.md` 和 `docs/VERIFICATION.md`。
- 具体改法：只用 Python 标准库 `unittest`、`tempfile`、`multiprocessing` 和 fake Herdr/fake Kernel，避免新增运行时依赖。`scripts/run-tests.sh` 执行 compileall、unit/integration tests、shell 语法检查和文档矛盾静态检查，任一失败即非零。
- 验收标准：全新 checkout 不启动真实 ZCode 即可一条命令 `sh scripts/run-tests.sh` 全绿；真实 ZCode/Herdr smoke 独立标记为 opt-in，不得被单元测试假绿替代。

### P1-5：文档只陈述新协议与已验证事实

- 改哪个文件：`README.md`、`docs/ORCHESTRATOR-GUIDE.md`、`docs/VERIFICATION.md`、`skills/zcode-bridge/SKILL.md`、`docs/UPSTREAM-REQUEST.md`。
- 具体改法：统一 request_id 用法、`result/wait` 主键、显式 parent continue、两轮上限、结构化 verify、owner cancel、MCP pane proxy、GUI deny gate、权限和退出码。删除“纯文本 full access”与“纯文本 read-only”的冲突、MCP 直通 NAR 的暗示、“headless 不加载所以无需处理”、tag pin、“CLI 超时必然孤儿”等过时结论。历史验证保留日期并标“基线前证据，不能证明修复后状态”。
- 验收标准：文档静态检查不再命中旧命令 `zcodecli result`（无 request_id）、`native_agent_router.mcp_server`、`v1.0.0` 作为安装 pin、`computer-use.*无需处理`、`/continue <text>`；所有示例可由 CLI parser 接受。

## 四、P2 清理项（列表）

- 删除 `executor_repl.py` 中永远不执行的 `handle_line(... ) if False`、未使用 `re`、可能未赋值的 `dur` 和重复输出格式代码。
- 删除 `zcodecli_cli.py` 中 `rc2/out2/err2` 死代码、重复 `re` import、`resolve_pane` 的脆弱文本 fallback；JSON pane list 失败应直接报 transport/protocol 错误。
- 把两个 executor 重复的 Herdr socket 状态上报抽到一个小模块；只抽重复逻辑，不引入类层级或通用事件总线。
- 将 `result`、`error`、`ready` 旧 marker 名称统一为 signal 协议，移除文档和注释里的 `[qab:result]` 残留。
- 更新 `herdr-plugin.toml` 版本与描述，使其写明 request broker/MCP proxy，而非“NAR 透传”。
- 给协议 JSON 增加 `version: 1`；未来不兼容变更必须升版本并 fail closed，不做猜测兼容。
- 在 README 只保留一份权限模型，其他文档链接过去，避免多份默认 mode/policy 描述再次漂移。
- 真实验证记录新增“静态/单元/多进程/真实 Herdr/真实 ZCode”证据分栏，不把 mock 结果描述成端到端。

## 五、实施顺序与风险（4 个独立可回退提交 + 主要破坏风险 + 最小回归清单 14 条）

### 1. 四个提交

每个提交完成后必须运行 `sh scripts/run-tests.sh` 并保持工作树除本提交白名单外无变化；不 squash，出现问题按 4→1 逆序回退，不跨提交混改同一问题。

1. `security: pin NAR and harden runtime files`：修改安装/入口脚本，固定 commit，加入 0600/0700、专用 NAR 配置和 GUI allowlist；新增对应安装/权限/工具门测试。此提交不改变 pane 命令语义，可单独回退。
2. `protocol: add request-id disk broker`：新增 `broker_protocol.py`、`pane_client.py`，一次性迁移 reception executor 与 CLI 的 send/result/inspect 基本路径；结果只读盘、JSON fail closed、结构化 verify、fcntl 幂等同时落地。提交内保留兼容提示但不保留不安全 fallback。
3. `runtime: keep Kernel in-process for lifecycle control`：新增 `broker_runtime.py`，迁移 reception/chat executor 的 submit/wait/cancel/continue、owner 路由和两轮 cap；删除 executor 的 NAR 子进程控制路径。该提交回退时同时回退其 owner/continue 测试，不触碰提交 2 的磁盘信任边界。
4. `integration: proxy MCP and align contracts`：新增 MCP proxy，收紧 CLI exit codes，补齐 14 条回归、单命令测试和全部文档清理。回退此提交会移除 MCP 新入口和文档声明，不影响前三提交的 broker 运行时。

### 2. 主要破坏风险

- **CLI 兼容性**：`result`/`wait` 必须显式 request_id，`continue` 必须显式 parent_request_id；依赖“最近 marker/最近任务”的旧脚本会失败。用明确 exit 2 和迁移示例处理，不保留隐式 fallback。
- **工具可用性**：显式 allowlist 可能因 ZCode 工具名变化误伤合法工具。应 fail closed 并更新 allowlist/真实 smoke，不能为恢复功能临时开放 GUI 或未知工具。
- **取消语义**：owner pane 崩溃后无法做真实确认；应报告 owner_lost，由人工决定是否使用 NAR 的显式 kill 边界，不能假报 cancelled。
- **并发行为**：reception pane 从隐式串行等待改为活动时拒绝第二任务；多任务并行必须显式开多个 chat pane/独立 workspace。客户端需处理 busy，不自动重试造成重复执行。
- **安装切换**：commit 校验和 doctor 会把原来“勉强可用”的错误环境变成启动失败。临时 venv + 原子切换必须保留旧可用版本，错误信息指出缺失项。
- **历史数据**：旧 `results/<task_id>.json` 不符合新 request schema。只读迁移工具可按需另开任务；本修复不把旧文件猜测转换成可信新结果。
- **fcntl 范围**：仅支持当前 manifest 声明的 macOS/Linux；网络文件系统的锁语义不保证。检测不到有效 flock 时拒绝启动。
- **验收口径**：NAR `ok=true` 不等于桥验收通过；桥新增 verify/diff/summary 门后，一些过去显示成功的任务会正确变为 exit 1。

### 3. 最小回归清单（恰好 14 条）

1. 合法 `op=chat` 生成唯一 request_id，强制 plan+deny，无 verify 也只提交一次。
2. 合法结构化 `op=run` 的 request→signal→磁盘 terminal 全链 request_id/task_id 一致。
3. 畸形 JSON、重复键、未知字段、错误类型和非对象根值均在 Kernel 前失败。
4. 结构化任务缺 goal、scope、verify 或 idempotency_key 任一项均失败；chat 不可覆盖 mode/policy。
5. marker 伪造、折行、过期、属于其他 request 或先于结果文件出现时不能产出成功结果。
6. request/task ID 路径穿越、结果 ID 不匹配、symlink、权限过宽、截断文件均 fail closed。
7. 两个独立进程同 key 并发提交只产生一个 task_id；不同 key 不被错误合并。
8. reception pane 活动时第二个 run 返回 busy，原任务不受影响且没有第二次 submit。
9. `/continue` round 1/2 完整继承上下文和 native session；round 3 在 submit 前拒绝。
10. 同进程 Kernel 的 running cancel：confirmed=true 才返回 cancelled/exit 0，且后台 waiter 正常收口。
11. cancel confirmed=false、owner_lost、already-finished 三种结果保持可区分，前两者不假报停止。
12. yolo+allow 下 ZCode session 仍只收到安全 tool allowlist，任何 GUI/computer-use 类项使启动失败。
13. 安装正确 SHA 成功；错误 SHA/缺依赖/pip 或 doctor 失败不切换 venv、不注入 MCP、不打开 ready pane。
14. CLI 与 MCP 对 succeeded、verify fail、out_of_scope、empty summary、protocol error、transport error、timeout 返回一致的状态/退出或 isError 语义；`sh scripts/run-tests.sh` exit 0。

## 六、明确不修的项及理由

- **不实现 OS 级强沙箱**：scope 仍是 NAR 的 diff/验收边界，不是 macOS Seatbelt/container。此项需要独立威胁模型和产品授权；本计划只增加 GUI 工具硬拒绝与 fail-closed 协议。
- **不修改 NAR 上游源码**：本仓库通过公开 Kernel API、专用 config 与外围 `fcntl` 补强；直接 vendoring/fork 会扩大维护面。若 NAR API 在固定 SHA 不满足计划，停止实施并另提最小上游补丁，不在本票私改 site-packages。
- **不把短命 CLI/MCP 进程变成第二个 Kernel owner**：所有有生命周期的动作回到 pane 内唯一 Kernel，避免再次出现取消无法确认；客户端只做代理。
- **不修 Herdr integrations 编译期白名单/未知 source 的 session 持久化**：这是 Herdr 上游能力，已有 `docs/UPSTREAM-REQUEST.md`，与本地 broker 正确性无关。
- **不让 headless 会话出现在 ZCode Desktop 历史中，也不判断套餐计费**：均属于 ZCode 产品/账户侧事实，仓库无法可靠控制或证明。
- **不自动 kill、接管或重跑 owner 丢失任务**：这些动作可能造成重复写入或释放仍在使用的锁；只报告 owner_lost/unknown，由人明确授权后走 NAR kill。
- **不迁移旧结果为新可信结果**：旧 marker 和 `results/<task_id>.json` 缺 request provenance，自动迁移会伪造信任链；保留为历史只读证据即可。
- **不改变所有者已授权的非 GUI yolo/allow 选择**：本票只保证结构化 verify、结果验收与工具 allowlist；若要把默认权限改成 deny/plan，应作为独立产品决定，避免借安全修复暗改既定工作流。
