# ⚠️ 合并协调说明(来自另一并发会话,2026-09-14)

致正在合并/重构本仓库的线程:本目录还有一个并发会话在工作,以下是它的未提交改动与约束,合并时请保留。

## 1. 请保留这个符号链接(重要)

`/Users/rocky/projects/herdr-zcode-plugin -> herdr-zcode`

它是那个并发会话的**锚定工作目录**——删掉它该会话的 shell 会直接死(ENOENT,已发生两次)。合并完成、对方会话结束后再删不迟。

## 2. 对方未提交的本地改动(功能代码,请勿丢弃)

- `scripts/pane_entry.py`:新增 **env.json → ZCODE_BIN/NODE_BIN 环境注入**(修复 herdr 最小 PATH 下 NAR "unknown agent_id 'zcode'; configured: []" 的问题)。注意:该文件当前被并发会话重写过一版(存储路径 herdr-zcode),合并时请保留两边的意图:路径用 herdr-zcode + 注入逻辑。
- `scripts/executor_repl.py` / `executor_chat.py`:新增 **`/steer <text>`**(运行中改口:cancel 当前票 + 以同一 `session_ref` 重发新指令,模型可见全部前情;NAR 无 mid-turn 注入接口,这是插件层的最优实现)。reception 的 process()/handle() 都已放行 `/steer`。
- `scripts/zcodecli_cli.py`:新增 `steer` 子命令(`zcodecli steer <text>`)与 `open-session <task_id>` 子命令(从 results 解析 native_session_id 并 `zcode --resume`,--print 可预览)。
- `tests/test_executors.py`:新增 TestSteer(2 个用例)与 marker 可见性用例。

## 3. 已推送的背景(避免重复劳动)

- v0.6.0 Windows-ready(ensure_bridge.py 跨平台 bootstrap、platforms+=windows、Python 入口、fcntl 守卫)已推送至 origin/main(d0e664e + 422956a)。
- Windows 真机(Legion)端到端已验证通过;`nar_commit` 递归 glob 与 `Scripts\python.exe` 布局两个 Windows 修复也已推送。

## 4. 存储

存储已由你方迁移至 `~/.local/share/herdr-zcode`(venv/nar/账本齐全,已确认);对方会话无异议,后续以此为准。

— 并发会话(zcodecli/dshcli 桥接作者)
