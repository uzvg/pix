# AGENTS.md

`pix` 是批量图片压缩（AVIF）与 rclone 图床同步 CLI。已实现 `shrink` / `mirror` / `publish` / `doctor` / `config init`；行为规格见 `docs/architecture.md`。

## 文档

| 文件 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 行为规格（唯一事实来源） |
| [docs/development.md](docs/development.md) | 环境、测试、构建、排障 |
| [docs/decisions.md](docs/decisions.md) | 技术决策与原因 |
| [docs/roadmap.md](docs/roadmap.md) | 当前目标与待办 |

实现前先读 `architecture.md`；对选型有疑问读 `decisions.md`。

## 命令

```bash
uv sync
uv run pytest
uv run pix ...
uv tool install --editable ~/Projects/pix
uv build
```

## 必须遵守

- 兼容 Python 3.11。
- 仅标准库 + `pyvips[binary]`，不引入 click/rich/colorama。
- 拆分 `cli.py` 前先读 `docs/decisions.md` D5，不按行数拆分。
- `pyvips` 只在 `shrink` / `doctor` 内延迟导入。
- 不通过 shell 调用 `vips` 或 `rclone`。
- `-q` 是 quality，`--quiet` 是静默。
- 退出码 0/1/2，日志走 stderr；`mirror` / `publish` 透传 rclone 退出码。
- `mirror` / `publish` 使用 `rclone bisync`；调用前先 `rclone mkdir` 目标并用 `rclone lsjson --max-depth 1` 判空。目标为空时自动加 `--resync`，目标非空时必须显式 `--resync`。用户不需要手动运行 rclone。它们是双向镜像，不是单向备份。
- `--max-delete` 默认 10（删除百分比，rclone bisync 语义）；`mirror` / `publish` 源为空时默认拒绝，`--allow-empty` 加 `--resync` 才放行。
- `mirror` 默认只同步 `[mirror].include` 中的图片扩展名，`include = []` 关闭白名单；`publish` 固定只同步 `.avif`。两者用 rclone filters 文件（bisync `--filters-file`、判空 `--filter-from`）并加 `--ignore-case`。
- 修改 include/exclude 后必须提示用户先 `--resync`；升级到带过滤的版本后首次运行也需要 `--resync`。`--exclude` 是配置在前、命令行追加并去重。
- 增量状态固定在 `OUT/.pix/state.json`，每 200 个成功文件刷新并在 `finally` 落盘；`publish` 必须排除 `.pix/**`。
- `shrink` 必须拒绝目标冲突，`--prune` 必须拒绝空源，非 dry-run 必须持有 `OUT/.pix/lock`。
- `publish` 源必须至少有一个 `.avif`；`base_url` 必须以 `http://` 或 `https://` 开头。
