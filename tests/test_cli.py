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
