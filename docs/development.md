# 开发指南

> 行为规格见 [architecture.md](architecture.md)，选型理由见 [decisions.md](decisions.md)。

## 1. 环境要求

- [uv](https://docs.astral.sh/uv/)。
- Python `>=3.11`。仓库的 `.python-version` 和本地虚拟环境是 3.14，但代码必须兼容 3.11，不能使用 3.12+ 语法。
- 图像后端 `pyvips[binary]` 由依赖自动安装，不需要单独安装 libvips。
- `mirror` / `publish` 需要外部 `rclone >= 1.66`；单元测试不需要安装真实 rclone。

## 2. 常用命令

```bash
uv sync                          # 创建/刷新 .venv
uv run pytest                    # 运行全部测试
uv run pytest tests/test_cli.py::test_name -q   # 单个测试
uv run pix --version             # 从源码运行
uv build                         # 构建 sdist/wheel
```

## 3. 安装

```bash
uv tool install --editable ~/Projects/pix
```

- Linux：`~/.local/bin/pix`
- Windows：`%USERPROFILE%\.local\bin\pix.exe`

editable 安装后修改源码立即生效。升级依赖后可用 `uv tool install --force pix-cli` 重装。

## 4. 测试

测试放在 `tests/`，入口为 `tests/test_cli.py`。`pyproject.toml` 的 `testpaths = ["tests"]` 要求该目录存在。

不依赖真实 rclone：用 mock 替换 `subprocess.run`。真实图像测试使用 pyvips 生成小图。

| 用例 | 覆盖点 |
|---|---|
| `parse_size` | `500K`、`1M`、`2G`、`0`、非法值 |
| 配置优先级 | 命令行 > 配置文件 > 内置默认 |
| 首次 shrink | 全量编码并写出 `.pix/state.json` |
| 第二次 shrink | 源和参数未变时全部跳过 |
| 源文件变化 | 只重编码 size/mtime 变化的文件 |
| 参数变化 | `width`/`quality`/`effort` 变化后全量重编码 |
| 输出变化 | 输出被删除或被改动后重编码 |
| `--force` | 忽略状态和 `min_size` |
| `--prune` | 删除源已消失的输出并清理状态条目 |
| `--prune` 空源 | 0 个源图片时拒绝删除并返回 1；`--allow-empty` 才继续 |
| 目标冲突 | `a.jpg` 与 `a.jpeg` 同时存在时列出冲突并退出 2，不编码 |
| 状态损坏 | 告警后按空状态全量重建 |
| 状态刷新 | 中断或异常时 `finally` 落盘；已完成条目保留 |
| `--dry-run` | 不创建目录、不编码、不写状态、不建锁；prune 只打印 |
| 原子写入 | 编码失败时临时文件被清理 |
| 并发锁 | 第二个 `shrink` 遇到 `OUT/.pix/lock` 时返回 1 |
| `min_size` 类型 | 配置写整数 `0` 或字符串 `"500K"` 均可解析 |
| `mirror` 命令拼装 | `bisync`、`--exclude`、`--max-delete`、`--resync`、`--dry-run`、`-v` / `--quiet` |
| 目标预检 | 首次同步自动 `rclone mkdir`；目标为空时自动 `--resync`；目标非空不自动 |
| 目标预检失败 | `mkdir` / `lsjson` 失败返回 1，不调用 bisync |
| `--dry-run` 首次同步 | 仍创建空目标并判空，但不传输、不删除 |
| `mirror` / `publish` 空源 | 默认退出 2；`--allow-empty` 加 `--resync` 才放行 |
| `publish` 命令拼装 | 固定带 `--exclude .pix/**`，配置排除项随后，命令行追加 |
| `publish` 无 AVIF | 源目录没有 `.avif` 时退出 2，不调用 rclone |
| `publish --urls` | 输出排序后的 JSON；路径段正确编码 |
| `--urls` / `--no-urls` | 配置 `urls = true` 时 `--no-urls` 能单次关闭 |
| `--urls` 缺少 base_url | 退出 2，且不调用 rclone |
| `base_url` 协议 | 非 `http(s)://` 时退出 2 |
| 退出码透传 | mock 返回非零时 `mirror` / `publish` 原样返回 |
| rclone 缺失/过旧 | `mirror` / `publish` 启动时检查版本，失败返回 1 |
| 真实 shrink | 生成小 PNG，运行 `pix shrink`，断言输出存在且宽度正确 |

mtime 测试用 `os.utime` 控制时间，避免依赖真实文件系统精度。状态测试直接读写 `.pix/state.json`，不要依赖真实 rclone。

## 5. 构建

使用 hatchling：

```bash
uv build
```

产物在 `dist/`，wheel 包含 `src/pix`。入口点由 `pyproject.toml` 定义：

```toml
[project.scripts]
pix = "pix.cli:main"
```

版本号来自 `pyproject.toml` 的 `version`。

## 6. 图像后端验证

`pyvips[binary]` 自带 libvips，`uv tool install` 会把原生库一起装好，Windows 不需要单独下载。

已在 pyvips 3.2.0 / libvips 8.18.7 下验证：下面的 Python 写法与现有 vips CLI 生成的 AVIF 字节一致（测试图 MD5 相同）。

```python
import pyvips

img = pyvips.Image.thumbnail(src, width, size="down", linear=True)
img.write_to_file(tmp, Q=quality, keep="none", effort=effort)
```

对应现有 CLI：

```bash
vips thumbnail src 'out.avif[Q=85,strip,effort=5]' 1920 --size down --linear
```

要点：

- `keep="none"` 与旧的 `strip` 字节等价。
- `thumbnail(..., size="down", linear=True)` 的默认旋转行为与 CLI 一致。
- libvips 8.18.7 没有 `avifsave` action；AVIF 通过 `heifsave`（`.avif` 后缀自动选择）保存。
- `effort` 是有效参数。
- `import pyvips` 延迟到 `shrink` / `doctor` 内部，保证 `mirror` / `publish` 在图像后端缺失时仍可用。

复验方法：对同一张真实图片分别用上述两种方式生成 AVIF，比较文件大小和 MD5/内容哈希。

## 7. 缺少 libvips 时

`import pyvips` 失败时输出：

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

## 8. rclone 与 bisync

- 需要 `rclone >= 1.66`。`mirror` / `publish` 启动时解析 `rclone version` 并检查；`doctor` 保留同样的检查。
- 首次运行 `pix mirror` / `pix publish` 会自动创建目标；目标为空时自动初始化基线，不需要 `--resync`。目标非空且没有基线时，rclone 会报错，此时显式加 `--resync` 以本地为准重建。
- 修改 `--exclude` 或配置排除项后，必须重新带 `--resync` 运行，否则被排除的文件可能被当作删除传播。
- `--max-delete` 默认 `10`，是 rclone bisync 的删除百分比（不是数量）；`0` 禁止删除，`-1` 不限制。超过比例时 rclone 拒绝执行，这是默认保护，不要静默改成 `-1`。
- `mirror` / `publish` 是双向镜像，不是单向备份，删除会双向传播。手动验证时先用 `--dry-run`，再在临时目录里测试。
- 源目录为空时默认拒绝；确实要清空远端时才用 `--allow-empty`（`publish` 还需 `--resync`）。
- bisync 基线在 `~/.cache/rclone/bisync/`，由 rclone 管理；排查问题时可以用 `rclone bisync --help` 查看当前版本的参数。
- 单元测试只断言 argv，不访问网络，也不需要真实 remote。

## 9. 编码约定

- 仅使用标准库 + pyvips，不引入 click、rich、colorama 等依赖。
- `pyvips` 只在 `shrink` / `doctor` 内导入。
- 不通过 shell 调 `vips` 或 `rclone`，统一用 Python API 或 `subprocess.run(list)`。
- `-q` 是 quality，`--quiet` 才是静默，不要合并。
- 状态文件是内部实现，不要把它暴露成用户需要编辑的配置。
- 新增逻辑先问是否必要；第一版不实现 `pix run`、crop、并发等未列入范围的能力。

## 10. CI

GitHub Actions 在 Python 3.11 和 3.14 上运行 `uv sync --dev` 与 `uv run pytest`，见 `.github/workflows/ci.yml`。本地默认环境是 3.14，但代码不得使用 3.12+ 语法。

## 11. 发布到 PyPI

使用 Trusted Publishing，本地不需要保存 PyPI token，见 `.github/workflows/publish.yml`。

首次发布前：

1. 在 <https://pypi.org/manage/account/publishing/> 添加 pending publisher：
   - PyPI Project Name：`pix-cli`
   - Owner：`uzvg`
   - Repository name：`pix`
   - Workflow name：`publish.yml`
   - Environment name：`pypi`
2. 在 GitHub 仓库的 Settings → Environments 创建 `pypi` 环境（可加 required reviewers）。

发布：

```bash
git tag -a v0.1.0 -m v0.1.0
git push origin v0.1.0
```

tag 必须与 `pyproject.toml` 的 `version` 一致；同一个版本不能重复发布。
