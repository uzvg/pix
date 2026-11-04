import json
import os
from pathlib import Path

import pytest

from pix import cli


def test_parse_size():
    assert cli.parse_size("0") == 0
    assert cli.parse_size("500K") == 500 * 1024
    assert cli.parse_size("1m") == 1024**2
    assert cli.parse_size("2G") == 2 * 1024**3
    assert cli.parse_size(123) == 123
    for bad in ("", "abc", "1.5M", "-1", "1T", True):
        with pytest.raises(ValueError):
            cli.parse_size(bad)


def test_version_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"pix {cli.__version__}"


def test_missing_command_exits_two(capsys):
    assert cli.main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_config_priority_and_defaults(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text("[shrink]\nwidth = 1000\nquality = 70\n", encoding="utf-8")

    settings = cli.load_settings(config)

    assert settings["shrink"]["width"] == 1000
    assert settings["shrink"]["quality"] == 70
    assert settings["shrink"]["effort"] == cli.DEFAULTS["shrink"]["effort"]
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


def test_config_section_must_be_table(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text("shrink = 5\n", encoding="utf-8")
    with pytest.raises(cli.ConfigError):
        cli.load_settings(config)


def test_config_min_size_accepts_string(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text('[shrink]\nmin_size = "500K"\n', encoding="utf-8")
    assert cli.load_settings(config)["shrink"]["min_size"] == 500 * 1024


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


def fake_rclone(monkeypatch, bisync_code: int = 0, version: str | Exception = "rclone v1.67.0\n"):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:] == ["version"]:
            if isinstance(version, Exception):
                raise version
            return FakeResult(0, version)
        return FakeResult(bisync_code)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return calls


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"x")
    return source


def test_backup_builds_bisync_command(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        '[backup]\nexclude = ["cfg", "dup"]\nmax_delete = 9\n', encoding="utf-8"
    )
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        [
            "--config",
            str(config),
            "backup",
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
    assert calls[1] == [
        "rclone",
        "bisync",
        str(source),
        "remote:photos",
        "--exclude",
        "cfg",
        "--exclude",
        "dup",
        "--exclude",
        "cli",
        "--max-delete",
        "9",
        "--progress",
    ]


def test_backup_defaults_to_quiet_and_max_delete_ten(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch)

    assert cli.main(["backup", "-s", str(source), "-d", "remote:photos"]) == 0

    assert calls[1][-3:] == ["--max-delete", "10", "--quiet"]


def test_backup_empty_source_refuses(tmp_path: Path, monkeypatch, capsys):
    source = tmp_path / "src"
    source.mkdir()
    calls = fake_rclone(monkeypatch)

    assert cli.main(["backup", "-s", str(source), "-d", "remote:photos"]) == 2

    assert calls == []
    assert "source is empty" in capsys.readouterr().err


def test_backup_allow_empty_requires_resync(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        ["backup", "-s", str(source), "-d", "remote:photos", "--allow-empty"]
    ) == 2
    assert calls == []

    assert cli.main(
        [
            "backup",
            "-s",
            str(source),
            "-d",
            "remote:photos",
            "--allow-empty",
            "--resync",
        ]
    ) == 0
    assert "--resync" in calls[1]


def test_backup_dry_run_and_passthrough_exit_code(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch, bisync_code=7)

    assert cli.main(
        ["backup", "-s", str(source), "-d", "remote:photos", "--dry-run", "--quiet"]
    ) == 7

    assert calls[1][-2:] == ["--dry-run", "--quiet"]


def test_backup_rejects_old_or_missing_rclone(tmp_path: Path, monkeypatch, capsys):
    source = make_source(tmp_path)

    fake_rclone(monkeypatch, version="rclone v1.65.2\n")
    assert cli.main(["backup", "-s", str(source), "-d", "remote:photos"]) == 1
    assert "too old" in capsys.readouterr().err

    fake_rclone(monkeypatch, version=FileNotFoundError())
    assert cli.main(["backup", "-s", str(source), "-d", "remote:photos"]) == 1
    assert "not found" in capsys.readouterr().err


def test_backup_rejects_invalid_max_delete(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    calls = fake_rclone(monkeypatch)

    assert cli.main(
        ["backup", "-s", str(source), "-d", "remote:photos", "--max-delete", "-2"]
    ) == 2
    assert cli.main(
        ["backup", "-s", str(source), "-d", "remote:photos", "--max-delete", "101"]
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

    assert calls[1] == [
        "rclone",
        "bisync",
        str(source),
        "r2:images",
        "--exclude",
        ".pix/**",
        "--exclude",
        "cfg",
        "--exclude",
        "cli",
        "--max-delete",
        "10",
        "--quiet",
    ]


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
    assert "--resync" in calls[1]


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
    assert "--dry-run" in calls[1]


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
    monkeypatch.setattr(cli, "default_config_path", lambda: tmp_path / "missing.toml")
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



