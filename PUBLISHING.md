# 发布 checklist

参考 https://herdr.dev/docs/plugins/ 与 https://herdr.dev/docs/marketplace/ 。
Herdr 插件没有专门的服务器:插件就是一个普通 GitHub 仓库,marketplace 是每 30 分钟
刷新一次的自动索引。

## 发布（仓库已存在,改为公开即可）

```bash
# 1. 现有私有仓库改为公开(不要再 gh repo create,同名仓库已存在)
gh repo edit Nofuture123/herdr-zcode --visibility public --accept-visibility-change-consequences

# 2. 加 GitHub topic,marketplace 靠它发现插件
gh repo edit Nofuture123/herdr-zcode --add-topic herdr-plugin
```

注意:manifest 含 [[build]]（前置检查 + ZCode 登录态硬性校验 + 联网 bootstrap NAR 固定 commit）
与 [[startup]]，安装/启用时会执行并联网，发布页说明里必须披露这一点，以及执行器默认 yolo 全权限。
登录校验读 `~/.zcode/v2/credentials.json` 的 `oauth:*:access_token` 键（provider 段可能是 zai/bigmodel），
未登录即中止安装并给出 `zcode login` 三步指引。

## 发布后验证

1. 等待索引刷新(最多 30 分钟),然后到 https://herdr.dev/plugins/ 搜索 "zcode"。
2. 干净机器验证:`herdr plugin install Nofuture123/herdr-zcode` → build 不报错 →
   `zcodecli` 可用 → TUI pane 与 executor pane 均能打开。
