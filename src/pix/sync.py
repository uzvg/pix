"""mirror and publish subcommands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import ConfigError, default_cache_dir, require_int
from .logger import Logger
from .rclone import (
    check_rclone,
    dest_is_empty,
    ensure_dest,
    filters_file,
    has_files,
    list_files,
    merge_excludes,
    run_rclone,
)


def _filter_args(
    kind: str,
    source: Path,
    dest: str,
    includes: list[str],
    excludes: list[str],
    log: Logger,
) -> tuple[list[str], list[str]] | None:
    try:
        path = filters_file(default_cache_dir(), kind, source, dest, includes, excludes)
    except OSError as exc:
        log.error(f"cannot write filters file: {exc}")
        return None
    return (
        ["--filters-file", str(path), "--ignore-case"],
        ["--filter-from", str(path), "--ignore-case"],
    )


def _run_bisync(
    binary: str,
    version: str,
    source: Path,
    dest: str,
    bisync_filter_args: list[str],
    listing_filter_args: list[str],
    max_delete: int,
    args: argparse.Namespace,
    log: Logger,
) -> int:
    if not ensure_dest(binary, dest, log):
        log.error(f"cannot create destination directory: {dest}")
        return 1

    resync = bool(args.resync)
    if not resync:
        empty = dest_is_empty(binary, dest, listing_filter_args, log)
        if empty is None:
            return 1
        if empty:
            log.info("destination is empty; initializing sync baseline")
            resync = True

    cmd = [binary, "bisync", str(source), dest, *bisync_filter_args]
    cmd += ["--max-delete", str(max_delete)]
    if resync:
        cmd.append("--resync")
    if args.dry_run:
        cmd.append("--dry-run")
    cmd.append("--progress" if args.verbose else "--quiet")

    log.info(f"bisync {source} <-> {dest} (rclone {version})")
    log.detail(" ".join(cmd))
    code = run_rclone(cmd, log)
    if code == 7 and not resync:
        log.error(
            "rerun with --resync to initialize the baseline or accept changed filters"
        )
    return code


def mirror(args: argparse.Namespace, settings: dict[str, Any], log: Logger) -> int:
    config = settings["mirror"]
    source = args.source
    includes = config["include"]
    if not source.is_dir():
        log.error(f"source is not a directory: {source}")
        return 2
    try:
        max_delete = require_int(
            "max-delete",
            args.max_delete if args.max_delete is not None else config["max_delete"],
            -1,
            100,
        )
    except ConfigError as exc:
        log.error(str(exc))
        return 2

    try:
        has_files_found = has_files(source, includes)
    except OSError as exc:
        log.error(f"cannot scan {source}: {exc}")
        return 1
    if not has_files_found and not (args.allow_empty and args.resync):
        log.error("source is empty; use --allow-empty together with --resync to proceed")
        return 2

    binary = settings["rclone"]["binary"]
    version = check_rclone(binary, log)
    if version is None:
        return 1

    excludes = merge_excludes(config["exclude"], args.exclude)
    if includes:
        filter_args = _filter_args("mirror", source, args.dest, includes, excludes, log)
        if filter_args is None:
            return 1
        bisync_filter_args, listing_filter_args = filter_args
    else:
        legacy_args: list[str] = []
        for pattern in excludes:
            legacy_args += ["--exclude", pattern]
        bisync_filter_args = listing_filter_args = legacy_args
    return _run_bisync(
        binary,
        version,
        source,
        args.dest,
        bisync_filter_args,
        listing_filter_args,
        max_delete,
        args,
        log,
    )


def _collect_avif(source: Path) -> list[Path]:
    files = [
        path for path in source.rglob("*") if path.is_file() and path.suffix.lower() == ".avif"
    ]
    files.sort(key=lambda path: path.relative_to(source).as_posix())
    return files


def publish(args: argparse.Namespace, settings: dict[str, Any], log: Logger) -> int:
    config = settings["publish"]
    source = args.source
    if not source.is_dir():
        log.error(f"source is not a directory: {source}")
        return 2
    try:
        max_delete = require_int(
            "max-delete",
            args.max_delete if args.max_delete is not None else config["max_delete"],
            -1,
            100,
        )
    except ConfigError as exc:
        log.error(str(exc))
        return 2

    try:
        avifs = _collect_avif(source)
    except OSError as exc:
        log.error(f"cannot scan {source}: {exc}")
        return 1
    if not avifs and not (args.allow_empty and args.resync):
        log.error(
            "source contains no .avif files; use --allow-empty together with --resync "
            "to proceed"
        )
        return 2

    base_url = args.base_url if args.base_url is not None else config["base_url"]
    urls = args.urls if args.urls is not None else config["urls"]
    if base_url and not base_url.startswith(("http://", "https://")):
        log.error("base-url must start with http:// or https://")
        return 2
    if urls and not base_url:
        log.error("--urls requires a base URL (--base-url or [publish].base_url)")
        return 2

    binary = settings["rclone"]["binary"]
    version = check_rclone(binary, log)
    if version is None:
        return 1

    excludes = merge_excludes([".pix/**"], config["exclude"], args.exclude)
    filter_args = _filter_args("publish", source, args.dest, ["avif"], excludes, log)
    if filter_args is None:
        return 1
    bisync_filter_args, listing_filter_args = filter_args
    code = _run_bisync(
        binary,
        version,
        source,
        args.dest,
        bisync_filter_args,
        listing_filter_args,
        max_delete,
        args,
        log,
    )
    if code != 0 or args.dry_run or not urls:
        return code

    root = base_url.rstrip("/")
    files = list_files(binary, source, listing_filter_args, log)
    if files is None:
        return 1
    manifest = {rel: f"{root}/{quote(rel, safe='/')}" for rel in files}
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return code
