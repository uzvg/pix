"""pix command-line entry point."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _package_version

    __version__ = _package_version("pix-cli")
except PackageNotFoundError:  # pragma: no cover - only when running uninstalled
    __version__ = "0.1.0"


DEFAULTS: dict[str, Any] = {
    "shrink": {
        "width": 1920,
        "quality": 85,
        "effort": 5,
        "min_size": 0,
        "include": ["jpg", "jpeg", "png", "webp"],
    },
    "backup": {
        "exclude": [],
        "max_delete": 10,
    },
    "publish": {
        "base_url": "",
        "urls": False,
        "exclude": [],
        "max_delete": 10,
    },
    "rclone": {
        "binary": "rclone",
    },
}

_SIZE_RE = re.compile(r"^(\d+)([KMG]?)$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}

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


class ConfigError(Exception):
    """Invalid or unreadable configuration."""


class Logger:
    def __init__(self, verbose: bool = False, quiet: bool = False) -> None:
        self.verbose = verbose
        self.quiet = quiet

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def detail(self, message: str) -> None:
        if self.verbose and not self.quiet:
            print(message, file=sys.stderr)

    def warning(self, message: str) -> None:
        if not self.quiet:
            print(f"pix: warning: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        print(f"pix: error: {message}", file=sys.stderr)


def parse_size(value: str | int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise ValueError("size must not be negative")
        return value
    if not isinstance(value, str):
        raise ValueError(f"invalid size: {value!r}")
    match = _SIZE_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid size: {value!r}")
    return int(match.group(1)) * _SIZE_UNITS[match.group(2).upper()]


def default_config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "pix" / "config.toml"
        return Path.home() / "AppData" / "Roaming" / "pix" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "pix" / "config.toml"
    return Path.home() / ".config" / "pix" / "config.toml"


def load_config(path: Path) -> dict[str, Any]:
    """Read a TOML config file; a missing file is an empty override."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a table")
    return data


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _deep_update(current, value)
        else:
            target[key] = value


def load_settings(explicit: Path | None = None) -> dict[str, Any]:
    path = explicit if explicit is not None else default_config_path()
    if explicit is not None and not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    overrides = load_config(path)
    settings = copy.deepcopy(DEFAULTS)
    _deep_update(settings, overrides)
    validate_settings(settings)
    return settings


def _require_int(name: str, value: Any, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ConfigError(f"{name} must be >= {minimum}")
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _require_excludes(name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return value


def validate_settings(settings: dict[str, Any]) -> None:
    shrink = settings["shrink"]
    _require_int("shrink.width", shrink["width"], 1)
    _require_int("shrink.quality", shrink["quality"], 1, 100)
    _require_int("shrink.effort", shrink["effort"], 0, 9)
    try:
        shrink["min_size"] = parse_size(shrink["min_size"])
    except ValueError as exc:
        raise ConfigError(f"shrink.min_size: {exc}") from exc

    include = shrink["include"]
    if not isinstance(include, list) or not all(
        isinstance(item, str) and item and not item.startswith(".") for item in include
    ):
        raise ConfigError("shrink.include must be a list of extensions without dots")

    for section in ("backup", "publish"):
        _require_excludes(f"{section}.exclude", settings[section]["exclude"])
        _require_int(f"{section}.max_delete", settings[section]["max_delete"], -1)

    publish = settings["publish"]
    if not isinstance(publish["base_url"], str):
        raise ConfigError("publish.base_url must be a string")
    if not isinstance(publish["urls"], bool):
        raise ConfigError("publish.urls must be a boolean")

    if not isinstance(settings["rclone"]["binary"], str) or not settings["rclone"]["binary"]:
        raise ConfigError("rclone.binary must be a non-empty string")


def _add_logging_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="store_true", default=None)
    group.add_argument("--quiet", action="store_true", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pix", description="Batch AVIF shrink and image-host sync.")
    parser.add_argument("--config", type=Path, default=None, metavar="PATH")
    parser.add_argument("--version", action="version", version=f"pix {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    shrink = subparsers.add_parser("shrink", help="compress images to AVIF")
    shrink.add_argument("-s", "--source", type=Path, required=True)
    shrink.add_argument("-d", "--dest", type=Path, required=True)
    shrink.add_argument("-w", "--width", type=int, default=None)
    shrink.add_argument("-q", "--quality", type=int, default=None)
    shrink.add_argument("-e", "--effort", type=int, default=None)
    shrink.add_argument("-m", "--min-size", type=str, default=None)
    shrink.add_argument("--force", action="store_true")
    shrink.add_argument("--prune", action="store_true")
    shrink.add_argument("--allow-empty", action="store_true")
    shrink.add_argument("--dry-run", action="store_true")
    _add_logging_args(shrink)

    backup = subparsers.add_parser("backup", help="bidirectionally mirror originals")
    backup.add_argument("-s", "--source", type=Path, required=True)
    backup.add_argument("-d", "--dest", required=True)
    backup.add_argument("--exclude", action="append", default=None, metavar="GLOB")
    backup.add_argument("--max-delete", type=int, default=None)
    backup.add_argument("--allow-empty", action="store_true")
    backup.add_argument("--resync", action="store_true")
    backup.add_argument("--dry-run", action="store_true")
    _add_logging_args(backup)

    publish = subparsers.add_parser("publish", help="bidirectionally mirror compressed images")
    publish.add_argument("-s", "--source", type=Path, required=True)
    publish.add_argument("-d", "--dest", required=True)
    publish.add_argument("--base-url", default=None)
    publish.add_argument("--urls", action=argparse.BooleanOptionalAction, default=None)
    publish.add_argument("--exclude", action="append", default=None, metavar="GLOB")
    publish.add_argument("--max-delete", type=int, default=None)
    publish.add_argument("--allow-empty", action="store_true")
    publish.add_argument("--resync", action="store_true")
    publish.add_argument("--dry-run", action="store_true")
    _add_logging_args(publish)

    subparsers.add_parser("doctor", help="check runtime dependencies and configuration")

    return parser


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
        width = _require_int(
            "width", args.width if args.width is not None else config["width"], 1
        )
        quality = _require_int(
            "quality", args.quality if args.quality is not None else config["quality"], 1, 100
        )
        effort = _require_int(
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

    sources = _scan_sources(source, config["include"])
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

    try:
        for src in sources:
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


RCLONE_MIN = (1, 66)
RCLONE_HINT = (
    "install rclone >= 1.66: https://rclone.org/downloads/ "
    "(Windows: winget search rclone)"
)


def _merge_excludes(*groups: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            if item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def _has_files(path: Path) -> bool:
    return any(item.is_file() for item in path.rglob("*"))


def _probe_rclone(binary: str) -> tuple[str | None, tuple[int, int, int] | None, str | None]:
    try:
        result = subprocess.run([binary, "version"], capture_output=True, text=True)
    except FileNotFoundError:
        return None, None, f"rclone not found: {binary}"
    except OSError as exc:
        return None, None, f"cannot run rclone: {exc}"
    output = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
    match = re.search(r"rclone v(\d+)\.(\d+)(?:\.(\d+))?", output)
    if match is None:
        if result.returncode != 0:
            return None, None, f"rclone version failed with exit code {result.returncode}"
        return None, None, "cannot parse rclone version"
    parsed = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}", parsed, None


def _check_rclone(binary: str, log: Logger) -> str | None:
    version, parsed, error = _probe_rclone(binary)
    if parsed is None:
        log.error(error or "rclone unavailable")
        log.error(RCLONE_HINT)
        return None
    if parsed[:2] < RCLONE_MIN:
        log.error(f"rclone {version} is too old; need >= 1.66")
        log.error(f"upgrade rclone: {RCLONE_HINT}")
        return None
    return version


def run_rclone(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        print(f"pix: error: rclone not found: {cmd[0]}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"pix: error: cannot run rclone: {exc}", file=sys.stderr)
        return 1


def backup(args: argparse.Namespace, settings: dict[str, Any], log: Logger) -> int:
    config = settings["backup"]
    source = args.source
    if not source.is_dir():
        log.error(f"source is not a directory: {source}")
        return 2
    try:
        max_delete = _require_int(
            "max-delete",
            args.max_delete if args.max_delete is not None else config["max_delete"],
            -1,
        )
    except ConfigError as exc:
        log.error(str(exc))
        return 2

    if not _has_files(source) and not (args.allow_empty and args.resync):
        log.error("source is empty; use --allow-empty together with --resync to proceed")
        return 2

    binary = settings["rclone"]["binary"]
    version = _check_rclone(binary, log)
    if version is None:
        return 1

    excludes = _merge_excludes(config["exclude"], args.exclude)
    cmd = [binary, "bisync", str(source), args.dest]
    for pattern in excludes:
        cmd += ["--exclude", pattern]
    cmd += ["--max-delete", str(max_delete)]
    if args.resync:
        cmd.append("--resync")
    if args.dry_run:
        cmd.append("--dry-run")
    cmd.append("--progress" if args.verbose else "--quiet")

    log.info(f"bisync {source} <-> {args.dest} (rclone {version})")
    log.detail(" ".join(cmd))
    return run_rclone(cmd)


def _not_implemented(_args: argparse.Namespace, _settings: dict[str, Any], log: Logger) -> int:
    log.error("subcommand not implemented yet; see docs/architecture.md")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help/--version and argparse errors
        return int(exc.code) if isinstance(exc.code, int) else 0

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"pix: error: {exc}", file=sys.stderr)
        return 2

    log = Logger(verbose=bool(getattr(args, "verbose", False)), quiet=bool(getattr(args, "quiet", False)))
    handlers = {
        "shrink": shrink,
        "backup": backup,
        "publish": _not_implemented,
        "doctor": _not_implemented,
    }
    return handlers[args.command](args, settings, log)


if __name__ == "__main__":
    raise SystemExit(main())
