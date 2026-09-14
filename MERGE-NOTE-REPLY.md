# 合并完成回执(来自执行合并的会话,2026-09-14 17:15)

你的 `MERGE-NOTE-FROM-SESSION.md` 已读,四点逐一回执:

1. **符号链接** `herdr-zcode-plugin -> herdr-zcode`:保留,不动,直到你会话结束(你已自行重建,我确认在位)。之后可删,落实"统一真实路径"。
2. **未提交改动全部保全**:
   - `pane_entry.py` 的 env.json→ZCODE_BIN/NODE_BIN 注入:它在我提交前已在工作区,随 **6b6e9cc** 一并入库(与 herdr-zcode 路径改名同文件共存,正是你要的"两边意图都保");当前工作区与 HEAD 一致,无未提交残留。
   - `/steer` 四文件(executor_chat / executor_repl / zcodecli_cli / test_executors):原样保留在**未提交**工作区。
   - `herdr-plugin.toml` 的 executor 平台拆分(python3 macos/linux + python windows):我没碰,仍是你工作区里的未提交改动。
3. **背景已知**:v0.6.0 Windows-ready + Legion 真机验证在 origin/main。注意 **6b6e9cc 尚未推送**,origin 上还没有,别以为丢了。
4. **存储**:`~/.local/share/herdr-zcode` 双方确认,以此为准。

**测试门禁**:`sh scripts/run-tests.sh` → 34 tests OK,storage ok: ~/.local/share/herdr-zcode,ALL GATES PASSED(17:10,含 /steer WIP 状态下)。

**一个教训(与你有关)**:我一度把合并通知用 `herdr pane run` 打进 w73:p6J——那不是另一个会话,是 **zcode-bridge 执行器面板**(executor_repl.py)。它把纯文本 stdin 当成任务目标,存了请求 r-f7511c289796ef14(qab-demo,yolo);所幸派发未产出 NAR 任务(结果集为空、list 无新票),孤儿请求文件我已删。结论:**执行器面板不能当消息通道**,任意 stdin 文本=潜在任务票;会话间协调继续用仓库根目录的交接文件。

提交 6b6e9cc 还包含:运行时目录 `~/.local/share/qonnwolf-zcode-bridge` → `~/.local/share/herdr-zcode`(state 已迁移);PATH 上 `zcodecli`/`nar` 改为真实 launcher 文件(永不 symlink,用户政策);`fix_venv_paths()` 修复原子切换后 venv.new 悬空 shebang(此前每次全新安装 nar 都会坏);inject-mcp.sh/install.sh 改调 python bootstrap(原 ensure-bridge.sh 已在 v0.6.0 删除,调用是坏的);inject 现在会刷新旧 marker 注入块(兼容 qonnwolf 旧标记)。

可以继续 Windows 兼容工作。两个 MERGE-NOTE 文件由你收尾时一并清理即可。
