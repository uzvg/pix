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
