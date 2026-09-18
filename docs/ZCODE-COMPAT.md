# ZCode 版本兼容指引(桥 ↔ ZCode 桌面版)

> 本文档回答一个问题:**哪个 ZCode 桌面版本能与 zcode 桥正常配合**,以及版本不符时如何识别与恢复。
> 最后验证:2026-09-18,macOS arm64,ZCode 桌面 3.10.2(内嵌 zcode CLI 0.16.5)。

## 兼容矩阵

| 桌面版本 | 内嵌 zcode.cjs | headless 建模 | 桥派票 | 说明 |
|---|---|---|---|---|
| **3.10.2**(Sep era) | 0.16.5 老构建 | ✅ | ✅ | **推荐,实测通过**(plan 票真实执行,52k tokens)|
| 3.12.3(Sep-16 重建) | 0.16.5 新构建 | ❌ 上游回归 | ❌ 全部失败 | `Model creation failed → Select a model before continuing`,见下 |

## 3.12.x 的两处破坏与桥的对应适配

1. **捆绑 provider 配置迁移**:从 `Resources/glm/provider/` 挪到 `Resources/config/provider/`,
   CLI 仍按老路径找 → 所有 submit 起来就死(`无法定位 CLI ZCode Built-in Provider Config`)。
   **桥已适配**:`pane_entry`/`tui_entry` 每次启动探测真实位置并导出
   `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE`。
2. **headless 模型创建回归(桥无法适配)**:即使配置找到了,官方裸命令
   `zcode --prompt "hi"` 全模式全 surface 死于
   `Model creation failed → Select a model before continuing`(turnPhase=model_creation)。
   CLI 没有 `--model` 旗标;帮助里写的 `--settings` 旗标解析器不认;registry 为空
   (`model.available: []`),`provider/updateAccountConfig` 推送被接收但 registry 不增长。
   **结论:该构建的 headless 模型创建在产品层就是坏的**,桌面交互不受影响
   (桌面自带宿主上下文),但任何程序化调用(NAR / 桥 / 官方 CLI)都无法建模。

## 识别坏版本(启动守卫)

`ensure_bridge` 在每次 herdr 启动时检测:若 `<zcode.cjs 目录>/../config/provider/zcode-builtin.json`
存在(= 3.12.x 布局),会在 `last-ensure.log` 与启动输出中打印:

```
[!] KNOWN-BROKEN ZCode build (3.12.x-era headless): ticket execution will fail with `Model creation failed`.
[!]   Roll back to the 3.10.2 desktop build — see docs/ZCODE-COMPAT.md.
```

看到它 = 派票必失败,先按下面回滚。

## 回滚步骤(3.12.x → 3.10.2)

1. 退出 ZCode 桌面;
2. `mv /Applications/ZCode.app /Applications/ZCode-3.12.3-broken.app`(留作对照);
3. 用 3.10.2 的 DMG:`hdiutil attach ZCode-3.10.2-mac-arm64.dmg -nobrowse -readonly`,
   `cp -R "/Volumes/ZCode 3.10.2-arm64/ZCode.app" /Applications/ZCode.app`,`hdiutil detach` 该卷;
4. 清 updater 待装缓存,防自动更回坏版:
   `rm -f ~/Library/Caches/@zcodedesktop-updater/pending/ZCode-3.12.*.zip`;
5. 验证:`cd 任意目录 && zcode.cjs 路径 --prompt "reply OK" --mode plan` 应正常回复;
   再跑一张桥票(`zcodecli send --mode plan ...`)确认 succeeded。

## 配置文件:两个存储,各管各的

| 文件 | 谁写 | 谁读 | 作用 |
|---|---|---|---|
| `~/.zcode/v2/config.json` | 桌面 App(会持续重写)| 桌面自身;NAR 的**第二**候选 | 桌面的账号/模型选择(id 形态随桌面版本变,**会被桌面覆盖,别当持久配置用**)|
| `~/.zcode/cli/config.json` | 用户/桥(一次性写好)| NAR 的**第一**候选;3.10.2 CLI 自身 | **headless 建模的稳定配置**,推荐格式见下 |

### `~/.zcode/cli/config.json` 推荐格式(3.10.2 验证可用)

```json
{
  "provider": {
    "builtin:bigmodel-coding-plan": {
      "api": "anthropic-messages",
      "kind": "anthropic",
      "name": "BigModel Coding Plan",
      "options": { "apiKey": "<你的 key>", "baseURL": "https://open.bigmodel.cn/api/anthropic" },
      "models": { "GLM-5.3": {}, "GLM-5.3-Flash": {} }
    }
  },
  "model": { "main": "builtin:bigmodel-coding-plan/GLM-5.3-Flash" }
}
```

**注意**:`model.main` 引用的 `provider/model` 字符串必须与 `provider.<key>` 键名和
`models` record 键名**逐字符一致**(大小写敏感)——不一致时 CLI 会静默回退到错误的
账号默认模型,报 `Unsupported model: …`。

## NAR 协议备注(0.16.5-era,stock d65bd49 即可)

- app-server 会向 client 发 `session/requestRuntimePreferences`,**必须应答**否则
  create/turn 阻塞超时——stock NAR 自带应答(`{nativeSearchEnhancementsEnabled: false}`),
  无需改动;
- `runtimeModel` 载荷在该版本被接受并生效(apiKey 经 payload 传递);
- 3.12.x 起拒绝 `runtimeModel`(Unrecognized key)且改走 setModel——但如上,其 headless
  建模本身已坏;桥的 `ensure_bridge patch_zcode_protocol` 按**新布局存在与否**条件生效,
  回滚到 3.10.2 后自动 no-op,保持 stock 协议。

## 派票失败的分层排查(速查)

1. `zcodecli list --machine` 正常、票报 `Model creation failed` → 本文档场景(查 ZCode 版本);
2. 票报 `Provider Registry 中不存在 Model: …` → 检查 `~/.zcode/cli/config.json` 的
   model.main 引用与 models 键是否逐字符一致;
3. 票报 `ProcessDied` + `无法定位 CLI ZCode Built-in Provider Config` → 旧版桥,升级到
   v0.8.4+(已自动注入配置路径);
4. 票完全无收据 → 检查收发台面板是否存活(输入黑洞时重开面板)。
