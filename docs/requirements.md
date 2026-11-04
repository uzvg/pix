# pix 项目需求

## 1. 背景与目标

现有 git hooks 使用 zsh 编写，只能在 Linux 运行。图片批量压缩、批量同步属于通用能力，不应和 git hooks 绑定。

目标：

- 提供独立的跨平台 CLI 工具 `pix`。
- 支持递归批量压缩图片为 AVIF。
- 支持通过 rclone 批量同步目录。
- 与 git hooks / TiddlyWiki 完全解耦。
- 通过 `uv tool install` 安装到 `~/.local/bin`，Windows 安装为 `pix.exe`。

## 2. 范围

### 做

- `pix shrink`：递归扫描图片，按宽度等比缩放并输出 AVIF。
- `pix sync`：封装 rclone，支持排除规则、`--delete-excluded`、`--dry-run`。
- `pix doctor`：检查运行依赖和配置。
- 可选的用户默认配置 `~/.config/pix/config.toml`。
- 单元测试和基本集成测试。

### 不做（第一版）

- 真正的图片裁剪（crop）。
- 并发压缩（`--jobs` / ThreadPoolExecutor）。
- per-repo 配置覆盖。
- 彩色/富文本日志。
- TiddlyWiki 插件同步。该能力属于 hooks 层。

## 3. 关键边界

`pix` 是纯通用工具，**不读取 `twks.toml`**。

`twks.toml` 属于 git hooks 层，由 `twks_hook.py` 读取、展开 `{repo}`，再把具体路径和参数作为命令行参数传给 `pix`。

## 4. 包结构与技术栈

```text
~/Projects/pix/
├── pyproject.toml
├── src/pix/
│   ├── __init__.py
│   └── cli.py
└── tests/
    └── test_cli.py
```

`pyproject.toml` 关键配置：

```toml
[project]
name = "pix-cli"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pyvips[binary]>=3.2"]

[project.scripts]
pix = "pix.cli:main"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/pix"]
```

安装：

```bash
uv tool install --editable ~/Projects/pix
```

- Linux：`~/.local/bin/pix`
- Windows：`%USERPROFILE%\.local\bin\pix.exe`
- 第一版所有逻辑放在 `src/pix/cli.py`，超过约 400 行再拆分。

## 5. 图像后端

优先使用 `pyvips[binary]` 绑定，而不是 `subprocess` 调外部 `vips` 命令。

理由：`pyvips[binary]` 自带 libvips，`uv tool install` 会把原生库一起装好，Windows 不需要单独下载 libvips。

已验证：pyvips 3.2.0 / libvips 8.18.7 下，以下 Python 写法与现有 vips CLI 生成的 AVIF 字节一致（测试图 MD5 相同）。

```python
import pyvips

img = pyvips.Image.thumbnail(src, width, size="down", linear=True)
img.write_to_file(tmp, Q=quality, keep="none", effort=effort)
```

对应现有 CLI：

```bash
vips thumbnail src 'out.avif[Q=85,strip,effort=5]' 1920 --size down --linear
```

实现要点：

- `keep="none"` 与旧的 `strip` 字节等价。
- `thumbnail(..., size="down", linear=True)` 默认旋转行为与 CLI 一致。
- libvips 8.18.7 没有 `avifsave` action；AVIF 通过 `heifsave`（`.avif` 后缀自动选择）保存。
- `effort` 是有效参数。
- `import pyvips` 延迟到 `shrink` / `doctor` 内部，避免 `pix sync` 因缺少图像依赖而失败。

### Windows 缺少 libvips 时的提示

如果 `import pyvips` 失败，输出：

```text
pyvips/libvips 不可用。

推荐重装（会自带 libvips）：
  uv tool install --force pix-cli

也可以手动安装 libvips：
  Windows: 从 https://github.com/libvips/libvips/releases 下载
           vips-dev-w64-web-*.zip，解压后把 <vips>\bin 加入 PATH
  Linux:   sudo apt install libvips42

注意：uv 只能安装 Python 包，不能单独安装原生 libvips。
      pyvips[binary] 的作用就是把 libvips 一起打包进 Python 环境。
```

## 6. CLI 规格

```bash
pix [--config PATH] [--version] {shrink,sync,doctor}

pix shrink -s SRC -d DEST
           [-w 1920] [-q 85] [-e 5] [-m 0]
           [--force] [--dry-run] [-v|--quiet]

pix sync   -s SRC -d DEST
           [--exclude GLOB ...] [--delete-excluded]
           [--dry-run] [-v|--quiet]

pix doctor
```

约定：

- `-q` 只表示 quality；quiet 使用 `--quiet`，避免短选项冲突。
- `-m/--min-size` 表示“小于该大小的源文件跳过”，支持 `500K` / `1M` / `2G`，按 1024 进制。
- `shrink` 只输出 AVIF，不负责 crop。
- `sync` 的 `-d` 可以是本地目录，也可以是 `OneDrive:wikis/...` 这类 rclone remote。
- `--config` 放在主 parser，写在子命令之前。
- `--version` 输出工具版本后退出。

## 7. 配置

`pix` 自己的默认配置：`~/.config/pix/config.toml`。文件不存在时使用内置默认值。

```toml
[shrink]
width    = 1920
quality  = 85
effort   = 5
min_size = "0"
include  = ["jpg", "jpeg", "png", "webp"]

[sync]
rclone = "rclone"
```

优先级：**命令行 > pix 配置 > 内置默认**。

## 8. shrink 行为

1. 校验 `source` 是目录，`dest` 可创建。
2. 递归扫描 `include` 中的扩展名，大小写不敏感。
3. 目标路径保持相对目录结构，扩展名替换为 `.avif`。
4. 跳过规则：
   - `min_size > 0` 且源文件更小；
   - 目标已存在且 `dest.mtime >= src.mtime`；
   - `--force` 时忽略后两条。
5. 写临时文件后原子替换，避免中断留下半截 AVIF：

   ```python
   tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp.avif")
   # encode...
   os.replace(tmp, dest)
   ```

   失败时清理临时文件。

6. 单个文件失败不中断整批，最后汇总；有失败返回 1。
7. `--dry-run` 只打印将要处理的文件，不创建目录、不编码。
8. 汇总输出：发现、转换、跳过（已存在）、跳过（太小）、失败。
9. 参数校验：width 正整数，quality 1–100，effort 0–9，min_size 非负。

## 9. sync 行为

命令拼装：

```python
cmd = [
    rclone, "sync", str(source), dest,
    "--exclude", "*.meta",
    "--exclude", "*.tid",
    "--delete-excluded",
    "--dry-run",
    "--progress" if verbose else "--quiet",
]
```

- 使用 `subprocess.run(cmd)`，不经过 shell，直接返回 rclone 的退出码。
- remote 判断：

  ```python
  def is_rclone_remote(value: str) -> bool:
      if re.match(r"^[A-Za-z]:[\\/]", value):
          return False  # Windows 盘符
      return bool(re.match(r"^[^\\/]+:", value))
  ```

  `OneDrive:wikis/x` 保持原样；`C:\images` 和 `/tmp/images` 按本地路径处理。

- `--delete-excluded` 在 `pix` 中默认关闭，避免手动误删；hooks 配置显式设为 `true`。
- `-v` 映射 `--progress`，默认 `--quiet`。
- `--dry-run` 原样传给 rclone。

## 10. doctor

输出示例：

```text
pix 0.1.0
python 3.14.9 (linux)
vips  8.18.7 via pyvips 3.2.0 (API mode)
rclone 1.7x (/home/uzvg/.local/bin/rclone)
config /home/uzvg/.config/pix/config.toml (ok)
```

- 缺少 pyvips/libvips：打印安装提示，退出 1。
- 缺少 rclone：提示官方下载地址或 `winget search rclone`，退出 1。
- 配置文件解析失败：指出文件和错误位置，退出 2。

## 11. 日志与退出码

- 默认：阶段信息和汇总，输出到 stderr。
- `-v`：输出每个文件的处理/跳过原因。
- `--quiet`：只输出错误。
- 退出码：`0` 成功，`1` 运行失败，`2` 参数/配置错误。
- 不引入 colorama、rich、click；仅标准库 + pyvips。

## 12. 测试要求

使用 pytest，重点测试逻辑，不依赖真实 rclone：

- `parse_size`：`500K`、`1M`、`2G`、`0`、非法值。
- `is_rclone_remote`：`OneDrive:x`、`C:\x`、`/tmp/x`、`\\server\share`。
- 目标路径映射和跳过规则（用 `os.utime` 控制 mtime）。
- `--force` 和 `--dry-run`。
- rclone 命令拼装（mock `subprocess.run`）。
- 配置合并优先级。
- 一个真实 shrink 测试：用 pyvips 生成小 PNG，运行 `pix shrink`，断言输出存在且宽度正确。

## 13. git hooks 层接口

`twks.toml` 示例：

```toml
[plugins]
source = ""

[shrink]
source = "tiddlers/images/public"
dest   = ".shrinks"
width  = 1920
quality = 85
effort = 5

[[sync]]
name    = "backup"
source  = "tiddlers/images"
dest    = "OneDrive:wikis/{repo}/images"
exclude = ["*.meta", "*.tid"]
delete_excluded = true

[[sync]]
name   = "publish"
source = ".shrinks"
dest   = "CloudFlare:images/{repo}"
```

`twks_hook.py` 负责：

- 检测 `.tiddlywiki`；
- 通过 `git rev-parse --show-toplevel` 获取仓库根；
- 从 `$2` remote URL 解析 repo 名；
- 用 `.replace("{repo}", repo_name)` 展开配置；
- 用 `shutil.copytree(..., dirs_exist_ok=True)` 同步 `published_plugins`；
- 调用 `pix shrink` 和 `pix sync`。

`pre-push` 是 sh shim，兼容 `python3` / `python`，并保留仓库本地 `.git/hooks/pre-push` 覆盖行为。

## 14. 待确认

1. 图像后端：使用 `pyvips[binary]` 绑定（推荐），还是调用外部 `vips` 命令？
2. 参数名：旧的 `--max-size` 是否改名为 `--min-size`，还是保留 `--max-size` 作为兼容别名？
3. `sync` 默认值：`pix` 默认不删除被排除的文件（`delete_excluded=false`），hooks 配置再打开，是否同意？

## 15. 风险

`pyvips[binary]` 与系统 vips 版本不一致时，输出可能有细微差异。目前仅对一张测试图验证了字节一致，落地后建议用真实图片再抽样对比。
