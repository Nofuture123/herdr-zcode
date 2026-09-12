# 发布 checklist

参考 https://herdr.dev/docs/plugins/ 与 https://herdr.dev/docs/marketplace/ 。
Herdr 插件没有专门的服务器:插件就是一个普通 GitHub 仓库,marketplace 是每 30 分钟
刷新一次的自动索引。

## 首次发布

```bash
# 1. 建公开仓库并推送(仓库根目录就是插件根目录,含 herdr-plugin.toml)
gh repo create Nofuture123/herdr-zcode --public --source=. --push

# 2. 加 GitHub topic,marketplace 靠它发现插件
gh repo edit Nofuture123/herdr-zcode --add-topic herdr-plugin
```

## 发布后验证

1. 等待索引刷新(最多 30 分钟),然后到 https://herdr.dev/plugins/ 搜索 "zcode"。
2. 安装验证:

   ```bash
   herdr plugin install Nofuture123/herdr-zcode
   herdr plugin list
   herdr plugin action list --plugin zcode
   herdr plugin action invoke zcode.open-here
   ```

   (本地开发验证用 `herdr plugin link .`,link 不会跑 [[build]],本项目也没有 build。)

## 日常更新

1. 改代码,把 `herdr-plugin.toml` 里的 `version` 加一位。
2. commit + push 到默认分支。
3. v1 没有 `plugin update`,让用户重新 `herdr plugin install` 即可刷新托管检出;
   也可以用 `--ref <revision>` 固定版本。

## 注意事项

- 仓库必须 public,`herdr-plugin.toml` 必须在默认分支根目录(或多插件时放子目录,
  安装写成 `owner/repo/subdir`)。
- 必填字段:`id`、`name`、`version`、`min_herdr_version`;`min_herdr_version` 高于
  用户二进制版本时 herdr 拒绝安装。
- id 命名只能用 ASCII 字母数字和 `. : _ -`;action/pane 的本地 id 不能带点。
- 安装预览后若 `herdr-plugin.toml` 有改动,安装会中止;有 [[build]] 时构建失败即安装失败。
- herdr 不审核也不沙箱插件,发布前自己再过一遍脚本内容。
