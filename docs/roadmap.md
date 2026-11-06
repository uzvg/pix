# 路线图与待办

> 行为规格见 [architecture.md](architecture.md)，技术选型见 [decisions.md](decisions.md)。

## 当前状态

截至 2026-11-06：

- 四个子命令 `shrink` / `mirror` / `publish` / `doctor` 已实现，`uv run pytest` 通过。
- `pix config init` 可生成带注释的默认配置；`--force` 覆盖已有文件。
- 真实 rclone 的本地目录端到端（首次 `--resync`、二次增量、双向删除）已验证；`pix shrink` 与 `vips thumbnail` 的 AVIF 字节一致性已验证。真实 R2 与 Windows 原生库仍待手工验证。
- 文档已按新定位重写，并完成 2026-11-04 审计修复。
- 2026-11-04：`cli.py` 已按职责拆分，`cli.py` 作为组合根（D27）。
- 2026-11-06：`mirror` / `publish` 自动创建目标；目标为空时自动初始化基线（D30）。本地端到端已验证：目标不存在时首次同步成功，第二次增量成功。
- 2026-11-06：`mirror` 默认只同步图片扩展名，`[mirror].include` 可配置；`publish` 固定只同步 `.avif`（D31）。
- 2026-11-06：README 按 0.1.4 的实际行为复核，修正安装、快速上手、空源、dry-run 与 URL 清单说明。

## M0. 文档重写（完成）

- [x] 移除旧项目的全部残留内容。
- [x] 定义 `shrink` / `mirror` / `publish` / `doctor` 四个子命令。
- [x] 定义 `.pix/state.json` 增量状态。
- [x] 定义 bisync 双向同步与 `--resync` 规则。
- [x] 更新 README、AGENTS.md、pyproject 描述。

## M0.1 审计修复（完成）

按 2026-11-04 审计逐项修正规格与脚手架：

- [x] `--prune` 空源保护与 `--allow-empty`。
- [x] `mirror` 明确为双向镜像，新增 `--max-delete`（默认 10%，rclone bisync 的删除百分比）。
- [x] 目标路径冲突检测（`a.jpg` / `a.jpeg` 等）。
- [x] 状态每 200 个文件刷新，`finally` 落盘；`out_mtime_ns` 退出跳过条件。
- [x] `publish` 源校验：必须是含 `.avif` 的目录。
- [x] 新增 `tests/test_cli.py` 与 Python 3.11/3.14 CI。
- [x] `--urls` / `--no-urls` 双向开关。
- [x] `--dry-run --prune` 只列出待删除项。
- [x] 排除项合并规则：配置在前、命令行追加、去重。
- [x] `min_size` 接受整数或尺寸字符串。
- [x] 修改排除项后必须 `--resync`。
- [x] 删除模板死代码；拆分规则改为按职责拆分。
- [x] rclone 版本在 `mirror` / `publish` 入口检查。
- [x] `shrink` 单实例锁 `OUT/.pix/lock`。
- [x] `base_url` 必须带 `http(s)://`。

## M1. CLI 骨架

- [x] argparse 主 parser + `shrink` / `mirror` / `publish` / `doctor` 子命令。
- [x] 全局 `--config`、`--version`。
- [x] 配置加载与三层合并，argparse 默认值使用哨兵。
- [x] 日志级别：默认、`-v`、`--quiet`。
- [x] 参数校验与退出码 2，包括 `min_size` 整数/字符串、`base_url` 协议、`--urls` / `--no-urls`。

验收：

- `uv run pix --version` 输出版本并以 0 退出。
- 缺少必填参数时以 2 退出。
- 配置优先级有测试覆盖。

## M2. shrink 与增量状态

- [x] `parse_size`：`500K` / `1M` / `2G` / `0` / 非法值。
- [x] 递归扫描与扩展名过滤（大小写不敏感）。
- [x] 目标路径映射与冲突检测，扩展名替换为 `.avif`。
- [x] `.pix/state.json` 读写、参数指纹、每 200 个文件刷新、`finally` 落盘。
- [x] 跳过规则：状态命中、`min_size`、目标 size；`--force` 覆盖，不比较输出 mtime。
- [x] `--prune` 清理源已消失的输出；0 个源时拒绝，除非 `--allow-empty`。
- [x] 非 dry-run 持有 `OUT/.pix/lock`，退出时删除。
- [x] 临时文件 + `os.replace` 原子写入，失败清理。
- [x] 单文件失败隔离与汇总，失败返回 1。
- [x] `--dry-run` 只打印计划（含 `prune <path>`），不写文件、不建锁。
- [x] 真实 shrink 测试：生成 PNG，断言 AVIF 存在且宽度正确。

验收：

- 首次运行全量编码，第二次运行全部跳过。
- 改参数后全量重编码；改单个源文件只重编码该文件。
- 中断或编码失败不会留下半截 AVIF。

## M3. mirror

- [x] 目标预检：自动 `rclone mkdir`，目标为空时自动 resync（D30）。
- [x] rclone 路径解析与本地源目录校验；空源默认拒绝，`--allow-empty` 加 `--resync` 才放行。
- [x] bisync 命令拼装：`--exclude`（合并去重）、`--max-delete`（默认 10%，百分比）、`--resync`、`--dry-run`、`-v` / `--quiet`。
- [x] mock `subprocess.run`，断言 argv 和退出码透传。
- [x] rclone 不存在或低于 1.66 时输出提示并返回 1。
- [x] 修改排除项后要求 `--resync`，并在文档和错误提示中说明。
- [x] `[mirror].include` 默认图片白名单；`include = []` 关闭白名单。
- [x] 图片过滤使用 filters 文件、`--ignore-case`、`--filters-file` / `--filter-from`；空源检查按 include 过滤。
- [x] include/exclude 变化由 rclone filters 文件 md5 检测，未 `--resync` 时以退出码 7 拒绝。
- [x] 首次 `--resync` 与二次增量的手工验证（2026-11-04，本地临时目录；含双向删除传播）。

验收：

- 测试不需要安装真实 rclone。
- 端到端验证：首次同步自动创建目标并初始化基线，第二次增量成功且不重复传输。
- 双向删除传播有记录（在临时目录中验证）。

## M4. publish

- [x] 目标预检与 `mirror` 共用，判空带上 `.pix/**`。
- [x] bisync 固定只同步 `.avif`，并排除 `.pix/**`；配置排除项随后，命令行追加。
- [x] `publish` 使用 filters 文件与 `--ignore-case`，修改排除项后要求 `--resync`。
- [x] `publish` 源必须是含至少一个 `.avif` 的目录；空源需 `--allow-empty` 加 `--resync`。
- [x] `--max-delete` 默认 10%，与 `mirror` 一致。
- [x] `--base-url` / `[publish].base_url` 合并，协议必须是 `http(s)://`。
- [x] `--urls` / `--no-urls` 合并与 JSON 输出，路径段编码正确。
- [x] 缺少 base_url 时退出 2，且在调用 rclone 前拒绝。
- [x] `--dry-run` 不输出 URL 清单。

验收：

- 上传后 stdout 的 URL 可直接访问（真实 R2 冒烟）。
- 状态文件不会出现在图床远端。

## M5. doctor 与发布验证

- [x] 输出 pix、Python、vips、rclone、bisync 能力、配置状态。
- [x] pyvips/libvips 缺失提示，退出 1。
- [x] rclone 缺失或低于 1.66 时提示，退出 1。
- [x] 配置解析失败退出 2；默认配置缺失显示 `missing (using defaults)`，退出 0。
- [x] 同时存在配置错误和依赖缺失时，退出 2。
- [x] `uv tool install --editable` 在 Linux 冒烟（2026-11-04，`pix --version` / `pix doctor` 通过）。
- [ ] Windows 上确认 `pix.exe` 可用，`pyvips[binary]` 能加载。
- [x] 用真实图片抽样对比 `pix shrink` 与 `vips thumbnail` 的 AVIF 字节（2026-11-04，libvips 8.18.7；450px 原图与 2700→1920px 缩图均 sha256 相同）。

验收：

- 正常环境退出 0。
- 三类失败路径都有测试或人工验证记录。

## Backlog（第一版不做）

- 一键流水线 `pix run`（shrink → mirror → publish）。
- 无条件自动 `--resync`（目标非空时仍需显式确认）。
- 远端可达性探测。
- 图片裁剪（crop）。
- 并发压缩（`--jobs` / `ThreadPoolExecutor`）。
- `--max-size` 兼容别名（出现真实需求再加）。
- 可配置的日志格式或彩色输出。

## 风险跟踪

| 风险 | 处理 |
|---|---|
| bisync 双向删除不可逆 | 默认 `--max-delete 10`（10% 删除比例）；目标为空时自动初始化，非空时显式 `--resync`；先 `--dry-run` |
| 空目录/挂载失败被当成“删除全部” | `--prune` 空源拒绝；`mirror` / `publish` 空源需 `--allow-empty` |
| `a.jpg` 与 `a.jpeg` 争用同一输出 | 编码前冲突检测，退出 2 |
| 修改 include/exclude 后未 resync 导致误删 | filters 文件 md5 检测；文档与错误提示要求先 `--resync` |
| 非图片文件被意外同步 | `[mirror].include` 默认白名单；`publish` 固定只同步 `.avif` |
| `pyvips[binary]` 与系统 libvips 版本不一致导致 AVIF 差异 | 已在 8.18.7 验证字节一致；升级 libvips 后重跑抽样对比 |
| Windows 原生库加载失败 | M5 Windows 冒烟；doctor 提供安装提示 |
| 临时文件后缀不是 `.avif` 导致格式选择错误 | 实现时固定临时文件名后缀，测试覆盖 |
| 状态文件损坏导致误跳过 | 解析失败按空状态重建，损坏不影响输出正确性 |
| 文档与实现漂移 | 行为只改 `architecture.md`，理由只改 `decisions.md` |
