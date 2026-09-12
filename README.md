# herdr-zcode-plugin

A [Herdr](https://herdr.dev) plugin that opens the [ZCode](https://zcode.dev) coding agent in Herdr panes.

It is a small POSIX-shell plugin with no build step. It gives you two things:

- a `ZCode` pane entrypoint that launches the ZCode TUI, `cd`-ed into the workspace directory Herdr reports;
- an `Open ZCode here` action that splits the focused pane (wide pane → right, tall pane → down, the same rule Herdr uses), renames the new pane to `zcode`, and starts the TUI there without moving your focus.

## Install

```bash
herdr plugin install Nofuture123/herdr-zcode-plugin
```

For local development, link a checkout instead:

```bash
git clone https://github.com/Nofuture123/herdr-zcode-plugin
herdr plugin link /path/to/herdr-zcode-plugin
```

## Usage

- Run the **Open ZCode here** action from the pane/workspace/tab context (or bind it to a key, below).
- Or open a ZCode pane directly:

  ```bash
  herdr plugin pane open --plugin zcode --entrypoint tui
  ```

- List what the plugin registered:

  ```bash
  herdr plugin list
  herdr plugin action list --plugin zcode
  ```

### Keybinding

```toml
[[keys.command]]
key = "prefix+z"
type = "plugin_action"
command = "zcode.open-here"
description = "Open ZCode here"
```

## Requirements

- Herdr 0.8.0 or newer.
- The ZCode CLI: a `zcode` executable on `PATH`, or ZCode.app on macOS (the
  plugin falls back to
  `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs` via `node`). Set
  `ZCODE_CJS` in the environment to point at a different `zcode.cjs`.
- `python3` is optional and only used to read the pane geometry for the
  split direction and to resolve the workspace directory; without it the
  action still works and splits right.

## How it works

The plugin is plain shell. Its scripts call back into Herdr through
`HERDR_BIN_PATH` (`pane edges`, `pane split`, `pane rename`, `pane run`) and
read their context from `HERDR_PLUGIN_CONTEXT_JSON`. No build commands run at
install time.

## Uninstall

```bash
herdr plugin uninstall zcode
```

## License

[MIT](LICENSE)
