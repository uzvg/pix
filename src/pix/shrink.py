"""shrink subcommand."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, parse_size, require_int
from .logger import Logger

STATE_VERSION = 1
STATE_DIR = ".pix"

PYVIPS_HINT = """pyvips/libvips 不可用。

推荐重装（会自带 libvips）：
  uv tool install --force pix-cli

也可以手动安装 libvips：
  Windows: 从 https://github.com/libvips/libvips/releases 下载
           vips-dev-w64-web-*.zip，解压后把 <vips>\\bin 加入 PATH
  Linux:   sudo apt install libvips42

注意：uv 只能安装 Python 包，不能单独安装原生 libvips。
      pyvips[binary] 的作用就是把 libvips 一起打包进 Python 环境。"""


def _scan_sources(source: Path, include: list[str]) -> list[Path]:
    allowed = {item.lower() for item in include}
    paths = [
        path
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower().lstrip(".") in allowed
    ]
    paths.sort(key=lambda path: path.relative_to(source).as_posix())
    return paths


def _target_map(
    sources: list[Path], source: Path, dest: Path
) -> tuple[dict[Path, Path], dict[str, list[str]]]:
    targets: dict[Path, Path] = {}
    owners: dict[str, list[str]] = {}
    for src in sources:
        rel = src.relative_to(source)
        out_rel = rel.with_suffix(".avif")
        key = out_rel.as_posix()
        owners.setdefault(key, []).append(rel.as_posix())
        targets[src] = dest / out_rel
    conflicts = {key: rels for key, rels in owners.items() if len(rels) > 1}
    return targets, conflicts


def _load_state(path: Path, params: str, log: Logger) -> dict[str, Any]:
    empty = {"version": STATE_VERSION, "params": params, "files": {}}
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning(f"cannot read state {path}: {exc}; rebuilding")
        return empty
    if (
        not isinstance(data, dict)
        or data.get("version") != STATE_VERSION
        or not isinstance(data.get("files"), dict)
        or not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in data["files"].items()
        )
    ):
        log.warning(f"unrecognized state {path}; rebuilding")
        return empty
    return {
        "version": STATE_VERSION,
        "params": data.get("params") if isinstance(data.get("params"), str) else "",
        "files": data["files"],
    }


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _acquire_lock(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
    finally:
        os.close(fd)
    return True


def _release_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _skip_reason(
    src_stat: os.stat_result,
    dest_path: Path,
    entry: dict[str, Any] | None,
    params_match: bool,
    min_size: int,
) -> str | None:
    if min_size > 0 and src_stat.st_size < min_size:
        return "small"
    if entry is None or not params_match:
        return None
    try:
        out_stat = dest_path.stat()
    except OSError:
        return None
    if (
        entry.get("src_size") == src_stat.st_size
        and entry.get("src_mtime_ns") == src_stat.st_mtime_ns
        and entry.get("out_size") == out_stat.st_size
    ):
        return "state"
    return None


def _prune(
    state: dict[str, Any],
    source: Path,
    dest: Path,
    source_count: int,
    dry_run: bool,
    allow_empty: bool,
    log: Logger,
) -> tuple[int, int, int]:
    files = state["files"]
    if source_count == 0 and files:
        if not allow_empty:
            log.error(
                "source has no images but state has entries; refusing to prune "
                "(use --allow-empty to override)"
            )
            return 0, 0, len(files)
    stale = sorted(rel for rel in files if not (source / rel).is_file())
    deleted = 0
    failed = 0
    for rel in stale:
        if dry_run:
            print(f"prune {rel}")
            deleted += 1
            continue
        out_path = dest / Path(rel).with_suffix(".avif")
        try:
            out_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.error(f"prune {rel}: {exc}")
            failed += 1
            continue
        del files[rel]
        deleted += 1
        log.detail(f"pruned {rel}")
    return deleted, failed, 0


def _log_summary(log: Logger, counters: dict[str, int]) -> None:
    log.info(
        "summary: converted={converted} skipped={state} too-small={small} "
        "empty-protected={protected} failed={failed} deleted={deleted}".format(**counters)
    )


def shrink(args: argparse.Namespace, settings: dict[str, Any], log: Logger) -> int:
    config = settings["shrink"]
    try:
        width = require_int(
            "width", args.width if args.width is not None else config["width"], 1
        )
        quality = require_int(
            "quality", args.quality if args.quality is not None else config["quality"], 1, 100
        )
        effort = require_int(
            "effort", args.effort if args.effort is not None else config["effort"], 0, 9
        )
        min_size = (
            parse_size(args.min_size) if args.min_size is not None else config["min_size"]
        )
    except (ConfigError, ValueError) as exc:
        log.error(str(exc))
        return 2

    source = args.source
    dest = args.dest
    if not source.is_dir():
        log.error(f"source is not a directory: {source}")
        return 2
    if dest.exists() and not dest.is_dir():
        log.error(f"dest is not a directory: {dest}")
        return 2

    try:
        sources = _scan_sources(source, config["include"])
    except OSError as exc:
        log.error(f"cannot scan {source}: {exc}")
        return 1
    targets, conflicts = _target_map(sources, source, dest)
    if conflicts:
        log.error("multiple sources map to the same output:")
        for key, rels in sorted(conflicts.items()):
            print(f"  {key} <- {', '.join(rels)}", file=sys.stderr)
        return 2

    params = f"w={width};q={quality};e={effort}"
    state_file = dest / STATE_DIR / "state.json"
    lock_file = dest / STATE_DIR / "lock"

    if not args.dry_run:
        try:
            if not _acquire_lock(lock_file):
                log.error(
                    f"another shrink appears to be running ({lock_file}); "
                    "remove the file if it is stale"
                )
                return 1
        except OSError as exc:
            log.error(f"cannot create lock {lock_file}: {exc}")
            return 1

    log.info(f"scanning {source}: {len(sources)} image(s)")
    state = _load_state(state_file, params, log)
    params_match = state["params"] == params
    files = state["files"]
    counters = {
        "converted": 0,
        "state": 0,
        "small": 0,
        "protected": 0,
        "failed": 0,
        "deleted": 0,
    }
    status = 0
    pyvips = None
    encoded: set[str] = set()
    total = len(sources)

    try:
        for index, src in enumerate(sources, 1):
            rel = src.relative_to(source).as_posix()
            try:
                src_stat = src.stat()
            except OSError as exc:
                log.error(f"{rel}: {exc}")
                counters["failed"] += 1
                status = 1
                continue

            reason = None if args.force else _skip_reason(
                src_stat, targets[src], files.get(rel), params_match, min_size
            )
            if reason == "small":
                counters["small"] += 1
                log.detail(f"skip {rel} (too small)")
                continue
            if reason == "state":
                counters["state"] += 1
                log.detail(f"skip {rel} (unchanged)")
                continue

            if args.dry_run:
                print(rel)
                counters["converted"] += 1
                continue

            log.progress(f"processing {index}/{total}: {rel}")

            if pyvips is None:
                try:
                    import pyvips as pyvips_module
                except Exception as exc:
                    log.error(PYVIPS_HINT)
                    log.error(str(exc))
                    status = 1
                    break
                pyvips = pyvips_module

            dest_path = targets[src]
            tmp = dest_path.with_name(f".{dest_path.name}.{os.getpid()}.tmp.avif")
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    img = pyvips.Image.thumbnail(str(src), width, size="down", linear=True)
                    img.write_to_file(str(tmp), Q=quality, keep="none", effort=effort)
                    os.replace(tmp, dest_path)
                except Exception:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    raise
                out_stat = dest_path.stat()
            except Exception as exc:
                log.error(f"{rel}: {exc}")
                counters["failed"] += 1
                status = 1
                continue

            files[rel] = {
                "src_size": src_stat.st_size,
                "src_mtime_ns": src_stat.st_mtime_ns,
                "out_size": out_stat.st_size,
                "out_mtime_ns": out_stat.st_mtime_ns,
            }
            encoded.add(rel)
            counters["converted"] += 1
            log.detail(f"encoded {rel}")
            if counters["converted"] % 200 == 0:
                try:
                    _save_state(state_file, state)
                except OSError as exc:
                    log.error(f"cannot write state {state_file}: {exc}")
                    status = 1

        if args.prune:
            deleted, failed, protected = _prune(
                state, source, dest, len(sources), args.dry_run, args.allow_empty, log
            )
            counters["deleted"] += deleted
            counters["failed"] += failed
            counters["protected"] += protected
            if failed or protected:
                status = 1

        if not params_match and status == 0 and not args.dry_run:
            # Only keep entries written under the new parameters; stale entries
            # would otherwise be skipped after min_size or include changes.
            state["files"] = {
                rel: entry for rel, entry in state["files"].items() if rel in encoded
            }
    except KeyboardInterrupt:
        log.error("interrupted")
        status = 1
    finally:
        if not args.dry_run:
            if params_match or status == 0:
                state["params"] = params
            try:
                _save_state(state_file, state)
            except OSError as exc:
                log.error(f"cannot write state {state_file}: {exc}")
                status = 1
            _release_lock(lock_file)

    _log_summary(log, counters)
    return status
