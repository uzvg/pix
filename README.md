# pix

Batch AVIF shrink and image-host sync CLI.

`pix` 把本地图片目录增量压缩为 AVIF，再通过 rclone bisync 备份原图、发布压缩图到图床。只处理显式传入的路径和远端。

```bash
pix shrink  -s ./photos -d ./out -w 1920 -q 85
pix backup  -s ./photos -d onedrive:photos --resync
pix publish -s ./out -d r2:images --base-url https://img.example.com --urls
pix doctor
pix config init
```

`backup` / `publish` 是双向镜像，不是单向备份：删除会双向传播。默认 `--max-delete 10`（删除百分比，即 10%），超过该比例时 rclone 会拒绝；首次运行或修改排除项后必须显式 `--resync`，先跑 `--dry-run` 更安全。

> Status: implementation complete; real R2 and Windows smoke tests pending (see roadmap).

## Docs

- [Architecture](docs/architecture.md) — behavior spec
- [Development](docs/development.md) — setup, tests, build
- [Decisions](docs/decisions.md) — technical decisions and rationale
- [Roadmap](docs/roadmap.md) — status and tasks

## Quick start

```bash
uv sync
uv run pytest
```
