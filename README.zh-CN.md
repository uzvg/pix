# pix

**批量 AVIF 压缩与图床同步 CLI。**

`pix` 把本地图片目录增量压缩为 AVIF，把原图双向镜像到云盘，并把压缩结果发布到图床。压缩使用 pyvips，同步交给 rclone。

[English](https://github.com/uzvg/pix/blob/main/README.md) · [行为规格](https://github.com/uzvg/pix/blob/main/docs/architecture.md) · [开发指南](https://github.com/uzvg/pix/blob/main/docs/development.md) · [技术决策](https://github.com/uzvg/pix/blob/main/docs/decisions.md) · [路线图](https://github.com/uzvg/pix/blob/main/docs/roadmap.md)

> 状态：核心命令已实现并通过测试；真实 R2 与 Windows 冒烟测试仍待完成，见[路线图](https://github.com/uzvg/pix/blob/main/docs/roadmap.md)。

## pix 做什么

| 命令 | 作用 |
|---|---|
| `pix shrink` | 递归压缩图片为 AVIF，只重新编码变化的文件 |
| `pix mirror` | 把原图目录与云盘做双向镜像 |
| `pix publish` | 把 AVIF 输出目录与图床做双向镜像，可选输出公开 URL |
| `pix doctor` | 检查 Python、libvips、rclone 与配置 |
| `pix config init` | 生成带注释的默认配置文件 |

`pix` 刻意保持边界清晰：

- 不是 rclone 替代品，不管理云盘凭据。
- 不读取仓库配置，不感知版本控制状态。
- 没有一键 `pix run`，命令由你自行组合。

## 工作原理

```text
photos/ ── shrink (pyvips) ──> out/*.avif + out/.pix/state.json
                                     │
                                     └── publish (rclone bisync) ──> r2:images
photos/ ── mirror (rclone bisync) ────────────────────────────────> onedrive:photos
```

| 命令 | 实际执行的内容 | 谁负责 |
|---|---|---|
| `shrink` | pyvips 缩放并编码 AVIF | pix |
| `mirror` | `rclone bisync SRC REMOTE` | pix 拼参数，rclone 执行 |
| `publish` | `rclone bisync OUT REMOTE`，排除 `.pix/**` | pix + rclone |
| `doctor` / `config init` | 依赖检查 / 配置脚手架 | pix |

**同步由 rclone 执行。** `mirror` / `publish` 只负责拼装 `rclone bisync` 参数；认证、传输和冲突处理都由 rclone 完成。使用前请先用 `rclone config` 配好 remote。`pix` 不读取也不写入 `rclone.conf`。

`mirror` / `publish` 是**双向镜像**，不是单向备份。删除会双向传播。

## 前置条件

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `mirror` / `publish` 需要 rclone >= 1.66
- 已在 rclone 中配置好至少一个 remote

`pyvips[binary]` 自带 libvips，不需要单独安装 vips。

## 安装

尚未发布到 PyPI，从源码安装：

```bash
git clone https://github.com/uzvg/pix.git
cd pix
uv tool install --editable .
pix --version
```

Linux 安装到 `~/.local/bin/pix`，Windows 为 `%USERPROFILE%\.local\bin\pix.exe`。editable 安装修改源码后立即生效。重装用 `uv tool install --force --editable .`，卸载用 `uv tool uninstall pix-cli`。

## 配置 rclone

仅 `mirror` / `publish` 需要：

```bash
rclone config         # 新建 remote，例如 onedrive 或 r2
rclone lsd onedrive:  # 验证是否可用
```

rclone 把凭据保存在 `~/.config/rclone/rclone.conf`（Windows：`%APPDATA%\rclone\rclone.conf`）。`pix` 只把 remote 名称透传给 rclone，不保存也不校验凭据。

## 配置 pix（可选）

默认配置路径：

- Linux/macOS：`$XDG_CONFIG_HOME/pix/config.toml`，未设置时为 `~/.config/pix/config.toml`
- Windows：`%APPDATA%\pix\config.toml`

文件不存在时使用内置默认值。生成带注释的配置文件：

```bash
pix config init
```

已有文件不会被覆盖；需要替换时加 `--force`。用 `--config PATH`（写在子命令之前）指向其他文件。

常用配置项：

| 段 | 键 | 默认值 | 说明 |
|---|---|---|---|
| `[shrink]` | `width` | `1920` | 最长边像素 |
| | `quality` | `85` | AVIF 质量，1–100 |
| | `effort` | `5` | libvips effort，0–9 |
| | `min_size` | `0` | 小于该值的源文件跳过；支持 `500K`、`1M`、`2G` |
| | `include` | `jpg`、`jpeg`、`png`、`webp` | 扫描扩展名，不带点 |
| `[mirror]` | `include` | 常见图片扩展名 | 需要镜像的扩展名，不带点；`[]` 关闭白名单 |
| | `exclude` | `[]` | rclone 排除 glob；命令行项会追加 |
| | `max_delete` | `10` | 删除百分比上限；`-1` 不限制，`0` 禁止删除 |
| `[publish]` | `base_url` | `""` | 公开 URL 前缀，必须以 `http://` 或 `https://` 开头 |
| | `urls` | `false` | 是否输出 JSON URL 清单 |
| | `exclude` | `[]` | 同 `mirror` |
| | `max_delete` | `10` | 同 `mirror` |
| `[rclone]` | `binary` | `rclone` | rclone 可执行文件名或路径 |

优先级为**命令行 > pix 配置 > 内置默认**。`--exclude` 是追加而非覆盖配置项。`-q` 表示 quality，静默模式用 `--quiet`（无短选项）。

`[mirror].include` 默认为 `jpg`、`jpeg`、`png`、`webp`、`gif`、`bmp`、`tif`、`tiff`、`heic`、`heif`、`avif`、`jxl`、`svg`、`ico`、`cr2`、`cr3`、`nef`、`arw`、`dng`、`orf`、`raf`、`rw2`。设为 `[]` 可镜像所有文件。

## 快速上手

1. 检查环境：

   ```bash
   pix doctor
   ```

2. 压缩图片：

   ```bash
   pix shrink -s ~/Pictures -d ~/Pictures-avif --dry-run
   pix shrink -s ~/Pictures -d ~/Pictures-avif -w 1920 -q 85
   ```

   首次全量编码，之后跳过未变化的文件。状态保存在 `OUT/.pix/state.json`。修改 `width`、`quality` 或 `effort` 会触发全量重编码。`--dry-run` 只打印计划。

3. 镜像原图：

   ```bash
   pix mirror -s ~/Pictures -d onedrive:photos --dry-run
   pix mirror -s ~/Pictures -d onedrive:photos
   ```

   目标目录会自动创建。只有匹配 `[mirror].include` 的文件会被镜像，源目录里的其他文件会被忽略。目标为空时会自动初始化基线，首次运行不需要 `--resync`；目标已有内容需要重建基线，或修改 `include` / `exclude` 后，才需要显式加 `--resync`。从没有 include 过滤的旧版本升级后，首次运行也需要 `--resync`；建议先跑 `--dry-run --resync` 预览。

4. 发布 AVIF 输出：

   ```bash
   pix publish -s ~/Pictures-avif -d r2:images \
     --base-url https://img.example.com --urls > urls.json
   ```

   `publish` 要求源目录至少有一个 `.avif`，只同步 `.avif` 文件，并固定排除 `.pix/**`。URL 清单只在同步成功且不是 `--dry-run` 时输出到 stdout。

5. 之后重复运行：

   ```bash
   pix shrink -s ~/Pictures -d ~/Pictures-avif
   pix mirror -s ~/Pictures -d onedrive:photos
   pix publish -s ~/Pictures-avif -d r2:images --urls
   ```

   只处理变化的部分。

## 常用命令

| 场景 | 命令 |
|---|---|
| 预览压缩 | `pix shrink -s SRC -d OUT --dry-run` |
| 强制全部重压 | `pix shrink -s SRC -d OUT --force` |
| 删除源已消失的输出 | `pix shrink -s SRC -d OUT --prune` |
| 预览同步 | `pix mirror -s SRC -d REMOTE --dry-run` |
| 首次同步（目标不存在或为空） | `pix mirror -s SRC -d REMOTE` |
| 重建基线（目标非空，或修改 include/exclude 后） | `pix mirror -s SRC -d REMOTE --resync` |
| 输出 URL 清单 | `pix publish -s OUT -d REMOTE --base-url https://img.example.com --urls` |
| 诊断环境 | `pix doctor` |
| 生成配置文件 | `pix config init` |

## 安全边界

| 情况 | 会发生什么 | 如何防护 |
|---|---|---|
| 任意一侧删除文件 | 删除双向传播，误删不可逆 | 先 `--dry-run`；默认 `--max-delete 10`（百分比） |
| 首次同步（目标为空） | `pix` 自动创建目标并初始化基线 | 先 `--dry-run` 预览；不需要手动运行 rclone |
| 目标已有内容但没有基线 | `pix` 不自动 resync，bisync 失败而不是覆盖 | 确认以哪边为准后显式加 `--resync` |
| 修改 include/exclude | rclone 会检测到 filters 文件变化并拒绝运行，或旧基线把被过滤的文件当成删除传播 | 修改后必须重新 `--resync` |
| 源目录含非图片文件 | `mirror` 默认忽略；`publish` 只同步 `.avif` | 需要镜像时把扩展名加入 `[mirror].include` |
| 源目录为空 | `mirror` / `publish` 默认拒绝；`shrink --prune` 同样拒绝 | 仅在确认要清空时用 `--allow-empty`（同步还需 `--resync`） |
| `a.jpg` 与 `a.jpeg` 同时存在 | 映射到同一输出 | 编码前检测并以 2 退出 |
| 同时运行两个 shrink | 会写同一份状态和输出 | `OUT/.pix/lock` 阻止第二个进程 |
| 状态文件丢失或损坏 | 告警后全量重建 | 只会重新编码，不会产生错误结果 |

## 输出与退出码

- 日志和进度输出到 stderr。
- stdout 用于 `doctor` 报告、`--dry-run` 计划和 `--urls` JSON。
- 退出码：`0` 成功，`1` 运行失败，`2` 参数或配置错误。
- `mirror` / `publish` 原样返回 rclone bisync 的退出码；目标预检失败返回 1。

URL 清单示例：

```json
{
  "2024/a.avif": "https://img.example.com/2024/a.avif"
}
```

## 故障排查

| 现象 | 处理 |
|---|---|
| 提示 `pyvips/libvips unavailable` | 重装以带入 libvips：`uv tool install --force --editable /path/to/pix`；或手动安装 libvips（Linux：`apt install libvips42`；Windows：下载 libvips release 压缩包并把 `bin` 加入 PATH） |
| rclone 版本低于 1.66 | 升级 rclone |
| bisync 报缺少基线或 filters 文件变化 | 目标非空，或 include/exclude 规则变化（含升级后首次运行）。确认本地为准后加 `--resync`；目标是空的会自动初始化 |
| `OUT/.pix/lock` 已存在 | 确认没有其他 `shrink` 在运行，再删除陈旧锁 |
| 配置解析失败 | 运行 `pix doctor` 定位文件；修复 TOML，或删除配置改用默认值 |
| `publish` 提示源目录没有 `.avif` | 先运行 `pix shrink` |
| URL 相关报错 | `base_url` 必须以 `http://` 或 `https://` 开头，且 `--urls` 依赖它 |

## 开发

```bash
uv sync
uv run pytest
uv run pix --version
uv build
```

代码必须兼容 Python 3.11。测试、打包与发布细节见[开发指南](https://github.com/uzvg/pix/blob/main/docs/development.md)。行为规格见[architecture.md](https://github.com/uzvg/pix/blob/main/docs/architecture.md)，选型原因见[decisions.md](https://github.com/uzvg/pix/blob/main/docs/decisions.md)。
