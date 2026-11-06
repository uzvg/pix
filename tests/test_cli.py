import io
import json
import os
import time
import tomllib
from pathlib import Path

import pytest

from pix import cli, doctor, rclone
from pix import config as pix_config
from pix.logger import Logger


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("pix.sync.default_cache_dir", lambda: tmp_path / "cache")


def test_parse_size():
    assert pix_config.parse_size("0") == 0
    assert pix_config.parse_size("500K") == 500 * 1024
    assert pix_config.parse_size("1m") == 1024**2
    assert pix_config.parse_size("2G") == 2 * 1024**3
    assert pix_config.parse_size(123) == 123
    for bad in ("", "abc", "1.5M", "-1", "1T", True):
        with pytest.raises(ValueError):
            pix_config.parse_size(bad)


def test_logger_progress_rewrites_on_tty(monkeypatch):
    class FakeStderr(io.StringIO):
        def isatty(self):
            return True

    stream = FakeStderr()
    monkeypatch.setattr("pix.logger.sys.stderr", stream)

    log = Logger()
    log.progress("first")
    log.progress("second")
    log.info("done")

    assert stream.getvalue() == "\rfirst\033[K\rsecond\033[K\ndone\n"


def test_version_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"pix {cli.__version__}"


def test_missing_command_exits_two(capsys):
    assert cli.main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_config_priority_and_defaults(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text("[shrink]\nwidth = 1000\nquality = 70\n", encoding="utf-8")

    settings = pix_config.load_settings(config)

    assert settings["shrink"]["width"] == 1000
    assert settings["shrink"]["quality"] == 70
    assert settings["shrink"]["effort"] == pix_config.load_defaults()["shrink"]["effort"]
    assert settings["publish"]["max_delete"] == 10


def test_missing_explicit_config_exits_two(tmp_path: Path, capsys):
    missing = tmp_path / "missing.toml"
    assert cli.main(["--config", str(missing), "shrink", "-s", "a", "-d", "b"]) == 2
    assert "config file not found" in capsys.readouterr().err


def test_invalid_config_value_exits_two(tmp_path: Path, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[shrink]\nquality = 101\n", encoding="utf-8")
    assert cli.main(["--config", str(config), "shrink", "-s", "a", "-d", "b"]) == 2
    assert "shrink.quality" in capsys.readouterr().err


def test_invalid_mirror_include_exits_two(tmp_path: Path, capsys):
    config = tmp_path / "config.toml"
    config.write_text('[mirror]\ninclude = [".jpg"]\n', encoding="utf-8")
    assert cli.main(["--config", str(config), "mirror", "-s", "a", "-d", "b"]) == 2
    assert "mirror.include" in capsys.readouterr().err


def test_config_section_must_be_table(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text("shrink = 5\n", encoding="utf-8")
    with pytest.raises(pix_config.ConfigError):
        pix_config.load_settings(config)


def test_config_min_size_accepts_string(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text('[shrink]\nmin_size = "500K"\n', encoding="utf-8")
    assert pix_config.load_settings(config)["shrink"]["min_size"] == 500 * 1024


def test_config_init_writes_defaults(tmp_path: Path, capsys):
    config = tmp_path / "nested" / "config.toml"

    assert cli.main(["--config", str(config), "config", "init"]) == 0

    assert capsys.readouterr().out.strip() == f"wrote {config}"
    assert tomllib.loads(config.read_text(encoding="utf-8")) == pix_config.load_defaults()
    assert pix_config.load_settings(config)["shrink"]["width"] == 1920


def test_config_init_refuses_existing(tmp_path: Path, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[shrink]\nquality = 60\n", encoding="utf-8")

    assert cli.main(["--config", str(config), "config", "init"]) == 2

    assert "already exists" in capsys.readouterr().err
    assert config.read_text(encoding="utf-8") == "[shrink]\nquality = 60\n"


def test_config_init_force_overwrites(tmp_path: Path, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[shrink]\nquality = 60\n", encoding="utf-8")

    assert cli.main(["--config", str(config), "config", "init", "--force"]) == 0

    assert tomllib.loads(config.read_text(encoding="utf-8")) == pix_config.load_defaults()
    assert capsys.readouterr().out.strip() == f"wrote {config}"


def test_config_init_uses_default_path(tmp_path: Path, monkeypatch):
    target = tmp_path / "config.toml"
    monkeypatch.setattr(pix_config, "default_config_path", lambda: target)

    assert cli.main(["config", "init"]) == 0

    assert target.is_file()


def make_png(path: Path, width: int = 200, height: int = 100) -> None:
    import pyvips

    path.parent.mkdir(parents=True, exist_ok=True)
    pyvips.Image.black(width, height, bands=3).write_to_file(str(path))


def read_state(dest: Path) -> dict:
    return json.loads((dest / ".pix" / "state.json").read_text(encoding="utf-8"))


def run_shrink(source: Path, dest: Path, *extra: str) -> int:
    return cli.main(["shrink", "-s", str(source), "-d", str(dest), *extra])


def test_shrink_encodes_and_writes_state(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "sub" / "a.png")

    assert run_shrink(source, dest, "-w", "100") == 0

    output = dest / "sub" / "a.avif"
    assert output.is_file()
    import pyvips

    assert pyvips.Image.new_from_file(str(output)).width == 100
    state = read_state(dest)
    assert state["version"] == 1
    assert state["params"] == "w=100;q=85;e=5"
    entry = state["files"]["sub/a.png"]
    assert entry["src_size"] == (source / "sub" / "a.png").stat().st_size
    assert entry["out_size"] == output.stat().st_size


def test_shrink_reports_progress(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")

    assert run_shrink(source, dest, "-w", "100") == 0

    assert "processing 1/1: a.png" in capsys.readouterr().err


def test_shrink_second_run_skips_unchanged(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")

    assert run_shrink(source, dest, "-w", "100") == 0
    mtime = (dest / "a.avif").stat().st_mtime_ns
    capsys.readouterr()

    assert run_shrink(source, dest, "-w", "100") == 0
    assert (dest / "a.avif").stat().st_mtime_ns == mtime
    err = capsys.readouterr().err
    assert "converted=0" in err
    assert "skipped=1" in err


def test_cli_overrides_config(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    config = tmp_path / "config.toml"
    config.write_text("[shrink]\nwidth = 50\n", encoding="utf-8")

    assert cli.main(
        ["--config", str(config), "shrink", "-s", str(source), "-d", str(dest), "-w", "100"]
    ) == 0
    import pyvips

    assert pyvips.Image.new_from_file(str(dest / "a.avif")).width == 100


def test_shrink_reencodes_changed_source(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest, "-w", "100") == 0
    before = (dest / "a.avif").stat().st_mtime_ns

    make_png(source / "a.png", width=120)
    os.utime(source / "a.png", ns=(before + 10**9, before + 10**9))
    assert run_shrink(source, dest, "-w", "100") == 0

    assert (dest / "a.avif").stat().st_mtime_ns != before


def test_shrink_reencodes_when_params_change(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest, "-w", "200") == 0
    assert run_shrink(source, dest, "-w", "100") == 0

    import pyvips

    assert pyvips.Image.new_from_file(str(dest / "a.avif")).width == 100
    assert read_state(dest)["params"] == "w=100;q=85;e=5"


def test_shrink_params_change_drops_entries_skipped_by_min_size(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest, "-w", "100") == 0

    assert run_shrink(source, dest, "-w", "200", "-m", "1M") == 0
    assert read_state(dest)["files"] == {}

    assert run_shrink(source, dest, "-w", "200") == 0
    import pyvips

    assert pyvips.Image.new_from_file(str(dest / "a.avif")).width == 200


def test_shrink_reencodes_when_output_deleted(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest, "-w", "100") == 0
    (dest / "a.avif").unlink()
    assert run_shrink(source, dest, "-w", "100") == 0
    assert (dest / "a.avif").is_file()


def test_shrink_force_reencodes(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest) == 0
    capsys.readouterr()
    assert run_shrink(source, dest, "--force") == 0
    assert "converted=1" in capsys.readouterr().err


def test_shrink_min_size_skips(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest, "-m", "1M") == 0
    assert not (dest / "a.avif").exists()
    assert "too-small=1" in capsys.readouterr().err


def test_shrink_prune_removes_stale_output(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    make_png(source / "b.png")
    assert run_shrink(source, dest) == 0
    (source / "b.png").unlink()

    assert run_shrink(source, dest, "--prune") == 0

    assert not (dest / "b.avif").exists()
    assert "b.png" not in read_state(dest)["files"]


def test_shrink_prune_refuses_empty_source(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest) == 0
    (source / "a.png").unlink()

    assert run_shrink(source, dest, "--prune") == 1
    assert (dest / "a.avif").is_file()
    assert "refusing to prune" in capsys.readouterr().err

    assert run_shrink(source, dest, "--prune", "--allow-empty") == 0
    assert not (dest / "a.avif").exists()
    assert read_state(dest)["files"] == {}


def test_shrink_conflict_exits_two(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"x")
    (source / "a.jpeg").write_bytes(b"y")

    assert run_shrink(source, dest) == 2

    assert "a.avif" in capsys.readouterr().err
    assert not dest.exists()


def test_shrink_rebuilds_corrupt_state(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    (dest / ".pix").mkdir(parents=True)
    (dest / ".pix" / "state.json").write_text("{broken", encoding="utf-8")

    assert run_shrink(source, dest) == 0

    assert (dest / "a.avif").is_file()
    assert "rebuilding" in capsys.readouterr().err
    assert "a.png" in read_state(dest)["files"]


def test_shrink_dry_run_writes_nothing(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")

    assert run_shrink(source, dest, "--dry-run") == 0

    assert capsys.readouterr().out.splitlines() == ["a.png"]
    assert not dest.exists()


def test_shrink_dry_run_prune_only_lists(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest) == 0
    (source / "a.png").unlink()
    capsys.readouterr()

    assert run_shrink(source, dest, "--prune", "--dry-run", "--allow-empty") == 0

    assert capsys.readouterr().out.splitlines() == ["prune a.png"]
    assert (dest / "a.avif").is_file()


def test_shrink_lock_blocks_second_run(tmp_path: Path, capsys):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    make_png(source / "a.png")
    assert run_shrink(source, dest) == 0
    (dest / ".pix" / "lock").write_text("stale", encoding="utf-8")

    assert run_shrink(source, dest) == 1
    assert "another shrink" in capsys.readouterr().err


def test_shrink_failure_cleans_tmp_file(tmp_path: Path):
    source = tmp_path / "src"
    dest = tmp_path / "out"
    source.mkdir()
    (source / "bad.png").write_text("not an image", encoding="utf-8")

    assert run_shrink(source, dest) == 1

    assert not (dest / "bad.avif").exists()
    assert list(dest.rglob("*.tmp.avif")) == []
    assert read_state(dest)["files"] == {}


class FakeResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fake_rclone(
    monkeypatch,
    bisync_code: int = 0,
    version: str | Exception = "rclone v1.67.0\n",
    dest_entries: list[dict] | None = None,
    source_entries: list[dict] | None = None,
    mkdir_code: int = 0,
    lsjson_code: int = 0,
    source_lsjson_code: int = 0,
):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:] == ["version"]:
            if isinstance(version, Exception):
                raise version
            return FakeResult(0, version)
        if cmd[1] == "mkdir":
            return FakeResult(mkdir_code)
        if cmd[1] == "lsjson":
            if lsjson_code:
                return FakeResult(lsjson_code)
            if "--recursive" in cmd:
                if source_lsjson_code:
                    return FakeResult(source_lsjson_code)
                if source_entries is None:
                    root = Path(cmd[2])
                    entries = [
                        {"Path": path.relative_to(root).as_posix()}
                        for path in sorted(root.rglob("*"))
                        if path.is_file()
                    ]
                else:
                    entries = source_entries
                return FakeResult(0, json.dumps(entries))
            entries = dest_entries if dest_entries is not None else [{"Name": "existing"}]
            return FakeResult(0, json.dumps(entries))
        return FakeResult(bisync_code)

    monkeypatch.setattr(rclone.subprocess, "run", fake_run)
    return calls


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"x")
    return source


def filter_path(cmd: list[str], flag: str) -> Path:
    return Path(cmd[cmd.index(flag) + 1])


def test_mirror_builds_bisync_command(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        '[mirror]\nexclude = ["cfg", "dup"]\nmax_delete = 9\n', encoding="utf-8"
    )
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        [
            "--config",
            str(config),
            "mirror",
            "-s",
            str(source),
            "-d",
            "remote:photos",
            "--exclude",
            "cli",
            "--exclude",
            "dup",
            "-v",
        ]
    ) == 0

    assert calls[0] == ["rclone", "version"]
    assert calls[1] == ["rclone", "mkdir", "remote:photos"]
    listing_path = filter_path(calls[2], "--filter-from")
    assert calls[2] == [
        "rclone",
        "lsjson",
        "remote:photos",
        "--max-depth",
        "1",
        "--filter-from",
        str(listing_path),
        "--ignore-case",
    ]
    bisync_path = filter_path(calls[3], "--filters-file")
    assert bisync_path == listing_path
    assert calls[3] == [
        "rclone",
        "bisync",
        str(source),
        "remote:photos",
        "--filters-file",
        str(bisync_path),
        "--ignore-case",
        "--max-delete",
        "9",
        "--progress",
    ]
    lines = bisync_path.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["- cfg", "- dup", "- cli"]
    assert lines[3].startswith("+ *.{jpg,jpeg,png,webp,")
    assert lines[-1] == "- **"


def test_mirror_defaults_to_quiet_and_max_delete_ten(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 0

    assert calls[3][-3:] == ["--max-delete", "10", "--quiet"]


def test_mirror_empty_source_refuses(tmp_path: Path, monkeypatch, capsys):
    source = tmp_path / "src"
    source.mkdir()
    calls = fake_rclone(monkeypatch)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 2

    assert calls == []
    assert "source is empty" in capsys.readouterr().err


def test_mirror_non_image_source_refuses(tmp_path: Path, monkeypatch, capsys):
    source = tmp_path / "src"
    source.mkdir()
    (source / "note.md").write_text("x", encoding="utf-8")
    calls = fake_rclone(monkeypatch)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 2

    assert calls == []
    assert "source is empty" in capsys.readouterr().err


def test_mirror_empty_include_syncs_all_files(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    (source / "note.md").write_text("x", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text("[mirror]\ninclude = []\n", encoding="utf-8")
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        ["--config", str(config), "mirror", "-s", str(source), "-d", "remote:photos"]
    ) == 0

    assert calls[2] == ["rclone", "lsjson", "remote:photos", "--max-depth", "1"]
    assert calls[3] == [
        "rclone",
        "bisync",
        str(source),
        "remote:photos",
        "--max-delete",
        "10",
        "--quiet",
    ]


def test_mirror_allow_empty_requires_resync(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        ["mirror", "-s", str(source), "-d", "remote:photos", "--allow-empty"]
    ) == 2
    assert calls == []

    assert cli.main(
        [
            "mirror",
            "-s",
            str(source),
            "-d",
            "remote:photos",
            "--allow-empty",
            "--resync",
        ]
    ) == 0
    assert "--resync" in calls[2]


def _slow_rclone(monkeypatch):
    def slow_run(cmd, **kwargs):
        if cmd[1:] == ["version"]:
            return FakeResult(0, "rclone v1.67.0\n")
        time.sleep(0.05)
        return FakeResult(0)

    monkeypatch.setattr(rclone.subprocess, "run", slow_run)
    monkeypatch.setattr(rclone, "HEARTBEAT_SECONDS", 0.01)


def _fake_tty(monkeypatch):
    class FakeStderr(io.StringIO):
        def isatty(self):
            return True

    stream = FakeStderr()
    monkeypatch.setattr("pix.logger.sys.stderr", stream)
    return stream


def test_mirror_prints_heartbeat_on_tty(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    _slow_rclone(monkeypatch)
    stream = _fake_tty(monkeypatch)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 0
    assert "rclone still running" in stream.getvalue()


def test_mirror_heartbeat_is_off_when_stderr_is_not_a_tty(tmp_path: Path, monkeypatch, capsys):
    source = make_source(tmp_path)
    _slow_rclone(monkeypatch)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 0
    assert "rclone still running" not in capsys.readouterr().err


def test_mirror_quiet_suppresses_heartbeat(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    _slow_rclone(monkeypatch)
    stream = _fake_tty(monkeypatch)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos", "--quiet"]) == 0
    assert "rclone still running" not in stream.getvalue()


def test_mirror_dry_run_and_passthrough_exit_code(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch, bisync_code=7)

    assert cli.main(
        ["mirror", "-s", str(source), "-d", "remote:photos", "--dry-run", "--quiet"]
    ) == 7

    assert calls[3][-2:] == ["--dry-run", "--quiet"]


def test_mirror_initializes_empty_destination(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch, dest_entries=[])

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 0

    path = filter_path(calls[2], "--filter-from")
    assert calls[1] == ["rclone", "mkdir", "remote:photos"]
    assert calls[2] == [
        "rclone",
        "lsjson",
        "remote:photos",
        "--max-depth",
        "1",
        "--filter-from",
        str(path),
        "--ignore-case",
    ]
    assert calls[3][:4] == ["rclone", "bisync", str(source), "remote:photos"]
    assert "--resync" in calls[3]


def test_mirror_non_empty_destination_does_not_resync(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 0

    assert calls[1][:2] == ["rclone", "mkdir"]
    assert "--resync" not in calls[3]


def test_mirror_dry_run_initializes_empty_destination(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch, dest_entries=[])

    assert cli.main(
        ["mirror", "-s", str(source), "-d", "remote:photos", "--dry-run"]
    ) == 0

    assert calls[1][:2] == ["rclone", "mkdir"]
    assert "--resync" in calls[3]
    assert "--dry-run" in calls[3]


def test_mirror_preflight_failure_aborts(tmp_path: Path, monkeypatch, capsys):
    source = make_source(tmp_path)

    calls = fake_rclone(monkeypatch, mkdir_code=1)
    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 1
    assert not any(cmd[1] == "bisync" for cmd in calls)
    assert "cannot create destination" in capsys.readouterr().err

    calls = fake_rclone(monkeypatch, lsjson_code=3)
    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 1
    assert not any(cmd[1] == "bisync" for cmd in calls)
    assert "cannot inspect destination" in capsys.readouterr().err


def test_mirror_missing_baseline_hint(tmp_path: Path, monkeypatch, capsys):
    source = make_source(tmp_path)
    fake_rclone(monkeypatch, bisync_code=7)

    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 7
    assert "rerun with --resync" in capsys.readouterr().err


def test_mirror_rejects_old_or_missing_rclone(tmp_path: Path, monkeypatch, capsys):
    source = make_source(tmp_path)

    fake_rclone(monkeypatch, version="rclone v1.65.2\n")
    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 1
    assert "too old" in capsys.readouterr().err

    fake_rclone(monkeypatch, version=FileNotFoundError())
    assert cli.main(["mirror", "-s", str(source), "-d", "remote:photos"]) == 1
    assert "not found" in capsys.readouterr().err


def test_mirror_rejects_invalid_max_delete(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        ["mirror", "-s", str(source), "-d", "remote:photos", "--max-delete", "-2"]
    ) == 2
    assert cli.main(
        ["mirror", "-s", str(source), "-d", "remote:photos", "--max-delete", "101"]
    ) == 2
    assert calls == []


def make_avif_source(tmp_path: Path, *names: str) -> Path:
    source = tmp_path / "out"
    source.mkdir()
    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"avif")
    return source


def test_publish_builds_bisync_command_with_pix_exclude(tmp_path: Path, monkeypatch):
    source = make_avif_source(tmp_path, "a.avif")
    config = tmp_path / "config.toml"
    config.write_text('[publish]\nexclude = ["cfg", ".pix/**"]\n', encoding="utf-8")
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        [
            "--config",
            str(config),
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--exclude",
            "cli",
        ]
    ) == 0

    assert calls[2] == [
        "rclone",
        "lsjson",
        "r2:images",
        "--max-depth",
        "1",
        "--filter-from",
        str(filter_path(calls[2], "--filter-from")),
        "--ignore-case",
    ]
    path = filter_path(calls[3], "--filters-file")
    assert calls[3] == [
        "rclone",
        "bisync",
        str(source),
        "r2:images",
        "--filters-file",
        str(path),
        "--ignore-case",
        "--max-delete",
        "10",
        "--quiet",
    ]
    assert path.read_text(encoding="utf-8").splitlines() == [
        "- .pix/**",
        "- cfg",
        "- cli",
        "+ *.{avif}",
        "- **",
    ]


def test_publish_initializes_empty_destination_with_excludes(tmp_path: Path, monkeypatch):
    source = make_avif_source(tmp_path, "a.avif")
    calls = fake_rclone(monkeypatch, dest_entries=[])

    assert cli.main(["publish", "-s", str(source), "-d", "r2:images"]) == 0

    assert calls[1] == ["rclone", "mkdir", "r2:images"]
    path = filter_path(calls[2], "--filter-from")
    assert calls[2] == [
        "rclone",
        "lsjson",
        "r2:images",
        "--max-depth",
        "1",
        "--filter-from",
        str(path),
        "--ignore-case",
    ]
    assert "--resync" in calls[3]


def test_publish_requires_avif(tmp_path: Path, monkeypatch, capsys):
    source = tmp_path / "out"
    source.mkdir()
    (source / "note.txt").write_text("x", encoding="utf-8")
    calls = fake_rclone(monkeypatch)

    assert cli.main(["publish", "-s", str(source), "-d", "r2:images"]) == 2

    assert calls == []
    assert "no .avif" in capsys.readouterr().err


def test_publish_allow_empty_requires_resync(tmp_path: Path, monkeypatch):
    source = tmp_path / "out"
    source.mkdir()
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        ["publish", "-s", str(source), "-d", "r2:images", "--allow-empty"]
    ) == 2
    assert calls == []

    assert cli.main(
        [
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--allow-empty",
            "--resync",
        ]
    ) == 0
    assert "--resync" in calls[2]


def test_publish_outputs_sorted_encoded_url_manifest(tmp_path: Path, monkeypatch, capsys):
    source = make_avif_source(tmp_path, "2024/a b.avif", "z.avif")
    fake_rclone(monkeypatch)

    assert cli.main(
        [
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--base-url",
            "https://img.example.com/",
            "--urls",
        ]
    ) == 0

    manifest = json.loads(capsys.readouterr().out)
    assert list(manifest) == ["2024/a b.avif", "z.avif"]
    assert manifest["2024/a b.avif"] == "https://img.example.com/2024/a%20b.avif"
    assert manifest["z.avif"] == "https://img.example.com/z.avif"


def test_publish_manifest_uses_filtered_source_listing(tmp_path: Path, monkeypatch, capsys):
    source = make_avif_source(tmp_path, "keep.avif", "skip.avif", "nested/also.avif")
    config = tmp_path / "config.toml"
    config.write_text('[publish]\nexclude = ["skip.avif"]\n', encoding="utf-8")
    calls = fake_rclone(
        monkeypatch,
        source_entries=[{"Path": "keep.avif"}, {"Path": "nested/also.avif"}],
    )

    assert cli.main(
        [
            "--config",
            str(config),
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--base-url",
            "https://img.example.com",
            "--urls",
        ]
    ) == 0

    manifest = json.loads(capsys.readouterr().out)
    assert list(manifest) == ["keep.avif", "nested/also.avif"]
    listing = calls[-1]
    assert listing[:5] == [
        "rclone",
        "lsjson",
        str(source),
        "--recursive",
        "--files-only",
    ]
    assert "--filter-from" in listing
    assert "--ignore-case" in listing
    filters = filter_path(listing, "--filter-from").read_text(encoding="utf-8")
    assert filters.splitlines() == [
        "- .pix/**",
        "- skip.avif",
        "+ *.{avif}",
        "- **",
    ]


def test_publish_manifest_listing_failure_returns_one(tmp_path: Path, monkeypatch, capsys):
    source = make_avif_source(tmp_path, "a.avif")
    fake_rclone(monkeypatch, source_lsjson_code=4)

    assert cli.main(
        [
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--base-url",
            "https://img.example.com",
            "--urls",
        ]
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot inspect source" in captured.err


def test_publish_no_urls_overrides_config(tmp_path: Path, monkeypatch, capsys):
    source = make_avif_source(tmp_path, "a.avif")
    config = tmp_path / "config.toml"
    config.write_text(
        '[publish]\nbase_url = "https://img.example.com"\nurls = true\n', encoding="utf-8"
    )
    fake_rclone(monkeypatch)

    assert cli.main(
        ["--config", str(config), "publish", "-s", str(source), "-d", "r2:images", "--no-urls"]
    ) == 0
    assert capsys.readouterr().out == ""


def test_publish_urls_requires_base_url(tmp_path: Path, monkeypatch):
    source = make_avif_source(tmp_path, "a.avif")
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        ["publish", "-s", str(source), "-d", "r2:images", "--urls"]
    ) == 2
    assert calls == []


def test_publish_rejects_invalid_base_url(tmp_path: Path, monkeypatch):
    source = make_avif_source(tmp_path, "a.avif")
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        [
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--base-url",
            "ftp://img.example.com",
        ]
    ) == 2
    assert calls == []


def test_publish_dry_run_outputs_no_manifest(tmp_path: Path, monkeypatch, capsys):
    source = make_avif_source(tmp_path, "a.avif")
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        [
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--base-url",
            "https://img.example.com",
            "--urls",
            "--dry-run",
        ]
    ) == 0

    assert capsys.readouterr().out == ""
    assert "--dry-run" in calls[3]


def test_publish_passthrough_exit_code_hides_manifest(tmp_path: Path, monkeypatch, capsys):
    source = make_avif_source(tmp_path, "a.avif")
    fake_rclone(monkeypatch, bisync_code=5)

    assert cli.main(
        [
            "publish",
            "-s",
            str(source),
            "-d",
            "r2:images",
            "--base-url",
            "https://img.example.com",
            "--urls",
        ]
    ) == 5
    assert capsys.readouterr().out == ""


def test_doctor_reports_ok(tmp_path: Path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    fake_rclone(monkeypatch)

    assert cli.main(["--config", str(config), "doctor"]) == 0

    out = capsys.readouterr().out
    assert f"pix {cli.__version__}" in out
    assert "python " in out
    assert "vips  8.18.7 via pyvips 3.2.0 (API mode)" in out
    assert "rclone 1.67.0" in out
    assert "bisync ok" in out
    assert f"config {config} (ok)" in out


def test_doctor_missing_default_config(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "default_config_path", lambda: tmp_path / "missing.toml")
    fake_rclone(monkeypatch)

    assert cli.main(["doctor"]) == 0

    assert "missing (using defaults)" in capsys.readouterr().out


def test_doctor_reports_old_or_missing_rclone(tmp_path: Path, monkeypatch, capsys):
    fake_rclone(monkeypatch, version="rclone v1.65.2\n")
    assert cli.main(["doctor"]) == 1
    assert "too old" in capsys.readouterr().out

    fake_rclone(monkeypatch, version=FileNotFoundError())
    assert cli.main(["doctor"]) == 1
    assert "rclone missing" in capsys.readouterr().out


def test_doctor_invalid_config_exits_two(tmp_path: Path, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[publish\n", encoding="utf-8")
    assert cli.main(["--config", str(config), "doctor"]) == 2
    assert str(config) in capsys.readouterr().err



