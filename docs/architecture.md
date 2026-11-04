# pix 架构与行为规格

> 本文是 `pix` 行为规格的唯一事实来源。选型理由见 [decisions.md](decisions.md)，开发与测试见 [development.md](development.md)，未完成工作见 [roadmap.md](roadmap.md)。

## 1. 定位与边界

`pix` 是独立的图床流水线 CLI：

- 把本地图片目录增量压缩为 AVIF。
- 把原始图片目录备份到 OneDrive 等 rclone 兼容云盘。
- 把压缩结果双向同步到 Cloudflare R2 等 rclone 兼容图床。

`pix` 只处理用户显式传入的本地路径和 rclone 远端，不感知项目类型或版本控制状态。所有路径和远端都由命令行或用户级配置提供。

目标：

- 提供独立的跨平台 CLI 工具 `pix`。
- 递归批量压缩图片为 AVIF，重复运行时只处理变化的部分。
- 通过 rclone bisync 与云盘双向同步。
- 可通过 `uv tool install` 安装，Linux 为 `~/.local/bin/pix`，Windows 为 `pix.exe`。

### 范围

第一版做：

- `pix shrink`：递归扫描图片，按宽度等比缩放并输出 AVIF，带增量状态。
- `pix backup`：把原始图片目录与 rclone 远端双向同步。
- `pix publish`：把压缩目录与图床远端双向同步，可选输出公开 URL 清单。
- `pix doctor`：检查运行依赖、rclone bisync 能力和配置。
- `pix config init`：把带注释的默认配置写到默认路径，供用户修改。
- 可选的用户默认配置 `~/.config/pix/config.toml`。
- 单元测试和基本集成测试。

第一版不做：

- 一键流水线 `pix run`。
- 图片裁剪（crop）。
- 并发压缩（`--jobs` / `ThreadPoolExecutor`）。
- 自动 `--resync`。
- 远端可达性探测。
- 彩色/富文本日志。

## 2. 技术栈与模块结构

- Python `>=3.11`。
- 仅标准库 + `pyvips[binary]`。
- 运行时依赖外部 `rclone >= 1.66`（bisync 稳定版），由 `backup` / `publish` 调用。

```text
src/pix/
├── __init__.py     # 包版本
├── py.typed
├── cli.py          # 组合根：parser、main、子命令分派
├── config.py       # 默认值、TOML 读取与校验、parse_size、config init
├── default_config.toml  # config init 写入的模板（随包分发）
├── logger.py       # stderr 日志
├── shrink.py       # shrink 子命令
├── rclone.py       # rclone 探测、版本检查与执行
├── sync.py         # backup / publish 子命令
└── doctor.py       # doctor 子命令
tests/
└── test_cli.py
```

入口点：`pix = "pix.cli:main"`。`main()` 返回 `int`，`__main__` 中 `raise SystemExit(main())`。

`cli.py` 只承担参数解析、子命令分派和组合根职责；低层模块不反向依赖它。拆分理由见 [decisions.md](decisions.md) D27。

函数边界：

| 模块 | 函数 | 职责 |
|---|---|---|
| `cli` | `main(argv: list[str] \| None = None) -> int` | 解析全局参数，分派子命令，返回退出码 |
| `cli` | `build_parser() -> argparse.ArgumentParser` | 构建主 parser 和子命令参数 |
| `config` | `load_config(path: Path) -> dict` | 读取 TOML；默认路径不存在时返回空覆盖 |
| `config` | `load_settings(path: Path \| None) -> dict` | 合并默认值并校验 |
| `config` | `parse_size(value: str \| int) -> int` | 尺寸字符串转字节 |
| `config` | `init_config(args) -> int` | `config init` 子命令：写入默认配置 |
| `shrink` | `shrink(args, settings, log) -> int` | `shrink` 子命令 |
| `sync` | `backup(args, settings, log) -> int` | `backup` 子命令 |
| `sync` | `publish(args, settings, log) -> int` | `publish` 子命令 |
| `doctor` | `doctor(args, settings, log) -> int` | `doctor` 子命令 |
| `rclone` | `run_rclone(cmd: list[str]) -> int` | 调用 rclone 并返回其退出码 |

依赖方向：

```text
pix.cli ──> pix.config / pix.logger / pix.shrink / pix.sync / pix.doctor
pix.shrink ──> pix.config / pix.logger / pyvips（仅函数内延迟导入）
pix.sync ──> pix.config / pix.logger / pix.rclone
pix.doctor ──> pix.config / pix.rclone / pix.shrink（提示文本）/ pyvips（延迟导入）
pix.rclone ──> subprocess
```

## 3. CLI 契约

```bash
pix [--config PATH] [--version] {shrink,backup,publish,doctor,config}

pix shrink  -s SRC -d OUT
            [-w 1920] [-q 85] [-e 5] [-m 0]
            [--force] [--prune] [--allow-empty] [--dry-run] [-v|--quiet]

pix backup  -s SRC -d REMOTE
            [--exclude GLOB ...] [--max-delete PERCENT] [--allow-empty] [--resync]
            [--dry-run] [-v|--quiet]

pix publish -s OUT -d REMOTE
            [--base-url URL] [--urls|--no-urls] [--exclude GLOB ...]
            [--max-delete PERCENT] [--allow-empty] [--resync]
            [--dry-run] [-v|--quiet]

pix doctor

pix config init [--force]
```

约定：

- `--config` 属于主 parser，写在子命令之前。
- `--version` 输出 `pix <version>` 后以 0 退出。
- `-s` 为源，`-d` 为目标。
- `shrink` 的 `-d` 是本地目录；`backup` / `publish` 的 `-d` 是 rclone 路径，可以是 remote，也可以是本地目录。
- `-q` 只表示 quality；安静模式使用 `--quiet`，不提供短选项。
- `-m/--min-size` 表示“小于该大小的源文件跳过”，`0` 表示不限制；支持 `500K` / `1M` / `2G`，按 1024 进制。
- `-v` 与 `--quiet` 互斥。
- `--dry-run` 不产生本地写入；`backup` / `publish` 中原样传给 rclone。与 `shrink --prune` 同用时只列出待删除项。
- `--exclude` 可重复。配置中的排除项在前，命令行排除项追加在后，保留顺序并去重。
- `--max-delete` 只用于 `backup` / `publish`，按删除百分比限制（rclone bisync 语义，不是数量），默认 `10`（即 10%）；`0` 禁止删除，`-1` 不限制，最大 `100`。超过比例时 rclone 拒绝运行。
- `--allow-empty` 只用于 `shrink --prune`、`backup` 和 `publish`：允许源为空时继续执行破坏性操作。默认拒绝；`backup` / `publish` 还必须同时带 `--resync`。
- `--resync` 只用于 `backup` / `publish`，原样传给 rclone。
- `--force` 只用于 `shrink`。
- `--prune` 只用于 `shrink`，默认关闭。
- `--base-url` / `--urls` / `--no-urls` 只用于 `publish`；`--no-urls` 可临时关闭配置中的 `[publish].urls = true`。

参数校验在配置合并之后执行，配置中的非法值同样以退出码 2 拒绝：

- `width` 为正整数。
- `quality` 为 1–100。
- `effort` 为 0–9。
- `min_size` 为非负整数，或可解析为字节数的字符串（如 `500K`、`1M`、`2G`）。
- `max_delete` 为整数且位于 `-1..100`。
- `publish.urls` 为布尔值；开启时必须能解析出 `base_url`。
- `base_url` 必须以 `http://` 或 `https://` 开头；否则退出 2。

## 4. 配置

默认配置路径：

- Linux/macOS：`$XDG_CONFIG_HOME/pix/config.toml`，未设置时为 `~/.config/pix/config.toml`。
- Windows：`%APPDATA%\pix\config.toml`。

文件不存在时使用内置默认值。

```toml
[shrink]
width    = 1920
quality  = 85
effort   = 5
min_size = 0
include  = ["jpg", "jpeg", "png", "webp"]

[backup]
exclude    = []
max_delete = 10

[publish]
base_url   = ""
urls       = false
exclude    = []
max_delete = 10

[rclone]
binary = "rclone"
```

优先级：**命令行 > pix 配置 > 内置默认**。

合并时只有显式出现在命令行上的参数才覆盖配置值，因此 argparse 的默认值必须是 `None` 或等价哨兵，不能直接写内置默认值，否则会永久压过配置文件。

`--exclude` 是例外：它不覆盖配置，而是追加。合并顺序为配置项在前、命令行项在后，保留顺序并去重，避免加上一个排除项时丢掉另一个。

`--no-urls` 是显式命令行值，优先级高于 `[publish].urls = true`；`--max-delete` 同样按“命令行 > 配置 > 默认 10”合并。

`--config` 显式指向的文件不存在时视为配置错误，退出 2；默认路径不存在则静默使用内置默认。

生成配置：`pix config init` 把内置模板写到 `--config` 指定路径（未指定时为默认路径），自动创建父目录，成功后打印路径并以 0 退出。目标已存在时拒绝覆盖并退出 2，提示使用 `--force`。该命令不读取现有配置，因此在 `load_settings` 之前分派，`--config` 指向尚不存在的文件也能创建。

`include` 中的扩展名不带点，匹配时大小写不敏感。

远端 `-d` 只由命令行提供，不放进配置，避免一条命令在未指定目标时误同步到错误位置。

## 5. shrink 行为

1. 校验 `source` 是目录，`dest` 可创建。
2. 非 `--dry-run` 时创建 `OUT/.pix/lock`（`O_CREAT|O_EXCL`）；已存在则报错退出 1，提示可能有另一个 `shrink` 在运行，或需要手动删除陈旧锁。退出时在 `finally` 中删除。
3. 状态文件固定为 `OUT/.pix/state.json`。
4. 递归扫描 `include` 中的扩展名，大小写不敏感，按相对路径排序后处理。
5. 在编码前建立目标路径映射。两个源文件映射到同一目标（例如 `a.jpg` 与 `a.jpeg`，或 `a.png` 与 `a.PNG`）时，列出全部冲突并以 2 退出，不编码、不写状态。
6. 目标路径保持相对目录结构，扩展名替换为 `.avif`。
7. 跳过规则（全部满足时跳过）：
   - `min_size > 0` 且源文件小于该值（严格小于）；
   - 状态中有该相对路径的记录；
   - 状态顶层参数指纹与当前 `width:quality:effort` 一致；
   - 目标存在；
   - 源 `size` / `mtime_ns` 与记录一致；
   - 目标 `size` 与记录一致。目标 `mtime_ns` 只用于诊断，不参与跳过判断，避免外部 `touch` 或同步工具改变时间戳后触发重编码。
8. `--force` 忽略上述跳过规则，重编码所有源文件。
9. 写临时文件后原子替换，避免中断留下半截 AVIF：

   ```python
   tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp.avif")
   # encode...
   os.replace(tmp, dest)
   ```

   临时文件必须以 `.avif` 结尾，确保 libvips 选择 `heifsave`。失败时清理临时文件。

10. 编码调用：

   ```python
   img = pyvips.Image.thumbnail(str(src), width, size="down", linear=True)
   img.write_to_file(str(tmp), Q=quality, keep="none", effort=effort)
   ```

   `size="down"` 保证不放大小图。

11. 单个文件失败不中断整批，记录错误后继续；最后汇总，只要有失败就返回 1。
12. `--dry-run` 只打印将要转换的文件（每行一个，输出到 stdout），不创建目录、不编码、不写状态、不建锁。与 `--prune` 同用时，每行输出 `prune <相对路径>`，表示将要删除，但绝不删除。
13. 汇总输出：转换、跳过（状态命中）、跳过（太小）、跳过（空源保护）、失败、删除。
14. 状态在编码过程中每成功 200 个文件刷新一次（原子写），并在正常结束或 `KeyboardInterrupt` 时通过 `finally` 再写一次，避免 Ctrl-C 丢掉整批进度。写入失败记录错误并返回 1，下次运行会重新编码未记录的文件。
15. 状态损坏、版本不识别或 JSON 解析失败时输出警告，按空状态全量重建，不中止。

### 状态文件

```json
{
  "version": 1,
  "params": "w=1920;q=85;e=5",
  "files": {
    "sub/a.png": {
      "src_size": 123456,
      "src_mtime_ns": 1699999999999999999,
      "out_size": 45678,
      "out_mtime_ns": 1699999999999999999
    }
  }
}
```

- 键为源文件相对路径，统一使用 `/` 分隔。
- 参数变化时 `params` 不匹配，整批重编码。
- 只有成功写出的文件才进入 `files`。
- `out_mtime_ns` 只用于诊断，不参与跳过判断。
- 状态文件本身不会被扫描或上传。

### prune

`--prune` 在正常扫描后执行：

- 状态中存在、但源文件已不存在的条目，删除对应输出文件并移除条目。
- 状态中不存在、但输出目录里多出的文件不处理。
- 扫描结果为 0 个源图片、而状态中仍有条目时，视为源未挂载或路径错误：不删除任何文件并返回 1，除非显式传入 `--allow-empty`。`--dry-run` 同样返回 1，但只打印、不删除。
- `--dry-run` 与 `--prune` 同用时，只把待删除项按 `prune <相对路径>` 输出到 stdout，不删除。
- 删除失败记录错误，最后返回 1。
- 默认关闭，避免误删。

## 6. backup 行为

`backup` 把原始图片目录与远端做 **rclone bisync 双向同步**。它是双向镜像，不是单向备份。

命令拼装：

```python
cmd = [rclone, "bisync", str(source), dest]
for pattern in excludes:
    cmd += ["--exclude", pattern]
cmd += ["--max-delete", str(max_delete)]
if resync:
    cmd.append("--resync")
if dry_run:
    cmd.append("--dry-run")
cmd.append("--progress" if verbose else "--quiet")
```

- 首次运行必须显式加 `--resync`。`pix` 不自动添加，避免两边都有内容时悄悄覆盖一侧。
- 不加 `--resync` 且 bisync 基线不存在时，rclone 会报错，`pix` 原样返回其退出码。
- `-v` 映射 `--progress`，默认映射 `--quiet`。
- `--exclude` 按配置在前、命令行在后合并，逐条传给 rclone；`pix` 不内置任何备份排除规则。
- **修改排除项后必须先 `--resync`**：rclone 要求过滤器变更后重建基线，否则被排除的文件可能被当作删除传播。先跑 `--dry-run`。
- `--max-delete` 默认 `10`，是删除百分比；一次运行要删除的比例超过 10% 时 rclone 会拒绝。确认要大量删除时显式调高，或传 `-1` 取消限制。
- 双向同步意味着删除会传播：本地删除会同步到远端，远端删除也会同步到本地。首次操作建议先 `--dry-run`。
- `-s` 必须是已存在的本地目录。源为空时默认拒绝并退出 2；只有显式 `--allow-empty` 且带 `--resync` 时才允许。
- 启动时执行 `rclone version`，要求 `>= 1.66`；rclone 缺失或版本过旧时输出错误并返回 1。
- bisync 的基线由 rclone 自己维护在 `~/.cache/rclone/bisync/`，`pix` 不读写、不清理。
- 使用 `subprocess.run(cmd)`，不经 shell，直接返回 rclone 的退出码。

## 7. publish 行为

`publish` 把 shrink 输出目录与图床远端做 **rclone bisync 双向同步**，参数与 `backup` 相同，另加：

- `-s` 必须是已存在的本地目录，且至少包含一个 `.avif`。源为空时默认退出 2；只有显式 `--allow-empty` 且带 `--resync` 时才允许。
- 固定追加 `--exclude .pix/**`，防止状态文件被同步到图床。配置排除项随后，命令行排除项最后，去重。
- `--max-delete` 与 `backup` 相同，默认 `10`，按删除百分比计。
- `--base-url` 优先取命令行，其次取 `[publish].base_url`。值必须以 `http://` 或 `https://` 开头，否则退出 2。
- `--urls` / `--no-urls` 优先取命令行，其次取 `[publish].urls`，默认关闭；`--no-urls` 可临时关闭配置中的 `true`。
- 开启 URL 清单但没有可用的 `base_url` 时，在调用 rclone 前以退出码 2 拒绝。
- URL 清单只在 bisync 成功（退出码 0）且不是 `--dry-run` 时输出。
- URL 清单输出到 stdout，JSON 对象，键为相对 OUT 的路径，值为公开 URL，按路径排序：

  ```json
  {
    "2024/a.avif": "https://img.example.com/2024/a.avif"
  }
  ```

- 生成清单时递归收集 OUT 下所有 `.avif`（大小写不敏感），路径段用 `urllib.parse.quote(..., safe="/")` 编码。
- `base_url` 末尾的 `/` 在拼接时去掉。
- `--dry-run` 不生成 URL 清单。
- 修改排除项后必须先 `--resync`，规则同 `backup`。
- 双向同步同样会传播删除，首次操作建议先 `--dry-run`。

## 8. doctor

输出示例（报告输出到 stdout）：

```text
pix 0.1.0
python 3.14.9 (linux)
vips  8.18.7 via pyvips 3.2.0 (API mode)
rclone 1.67.0 (/home/uzvg/.local/bin/rclone) bisync ok
config /home/uzvg/.config/pix/config.toml (ok)
```

检查项与退出行为：

- 缺少 pyvips/libvips：打印安装提示（文案见 [development.md](development.md#7-缺少-libvips-时)），退出 1。
- 缺少 rclone：提示官方下载地址或 `winget search rclone`，退出 1。
- rclone 版本低于 1.66：提示升级，退出 1。`backup` / `publish` 启动时也执行同样的版本检查，不依赖用户先跑 `doctor`。
- 配置文件解析失败：指出文件和错误位置，退出 2。
- 配置文件不存在：显示 `missing (using defaults)`，属于正常状态，退出 0。
- 多个问题同时存在时，配置错误（2）优先于依赖缺失（1）。

## 9. 日志与退出码

- 默认：阶段信息和汇总，输出到 stderr。
- `-v`：输出每个文件的处理/跳过原因，以及 rclone 的进度。
- `--quiet`：只输出错误。
- `doctor` 报告输出到 stdout；`--dry-run` 的计划文件列表和 `--urls` 的 JSON 输出到 stdout。
- 退出码：`0` 成功，`1` 运行失败，`2` 参数/配置错误。
- 例外：`backup` / `publish` 直接返回 rclone 的退出码，不归一化；调用方按非零即失败处理。
- 不引入 colorama、rich、click；仅标准库 + pyvips。

## 10. 跨平台约定

- 路径处理使用 `pathlib`。
- 不通过 shell 执行外部命令，Windows 盘符和反斜杠不被 shell 解释。
- 安装后的命令名为 `pix`（Linux）或 `pix.exe`（Windows）。
- AVIF 输出统一使用 `.avif` 后缀。
- 状态文件使用 UTF-8 和 `/` 分隔的相对路径，保证跨平台可迁移。
