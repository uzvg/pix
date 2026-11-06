# pix

**Batch AVIF shrink and image-host sync CLI.**

`pix` incrementally compresses local image directories to AVIF, mirrors originals to cloud storage, and publishes compressed images to an image host. Compression uses pyvips; sync is delegated to rclone.

[中文](https://github.com/uzvg/pix/blob/main/README.zh-CN.md) · [Architecture](https://github.com/uzvg/pix/blob/main/docs/architecture.md) · [Development](https://github.com/uzvg/pix/blob/main/docs/development.md) · [Decisions](https://github.com/uzvg/pix/blob/main/docs/decisions.md) · [Roadmap](https://github.com/uzvg/pix/blob/main/docs/roadmap.md)

> Status: core commands are implemented and tested. Real R2 and Windows smoke tests are still pending; see the [roadmap](https://github.com/uzvg/pix/blob/main/docs/roadmap.md).

## What pix does

| Command | Purpose |
|---|---|
| `pix shrink` | Recursively compress images to AVIF, re-encoding only changed files |
| `pix mirror` | Bidirectionally mirror an original photo directory to a cloud drive |
| `pix publish` | Bidirectionally mirror AVIF output to an image host; optionally emit public URLs |
| `pix doctor` | Report Python, libvips and rclone versions, plus the config path |
| `pix config init` | Write a commented default configuration file |

`pix` is deliberately narrow:

- It is not an rclone replacement and does not manage credentials.
- It does not read repository config or care about version control.
- It has no one-shot `pix run`; you chain the commands yourself.

## How it works

```text
photos/ ── shrink (pyvips) ──> out/*.avif + out/.pix/state.json
                                     │
                                     └── publish (rclone bisync) ──> r2:images
photos/ ── mirror (rclone bisync) ────────────────────────────────> onedrive:photos
```

| Command | What actually runs | Who does the work |
|---|---|---|
| `shrink` | pyvips thumbnail + AVIF encode | pix |
| `mirror` | `rclone bisync SRC REMOTE` | pix composes arguments, rclone executes |
| `publish` | `rclone bisync OUT REMOTE`, excluding `.pix/**` | pix + rclone |
| `doctor` / `config init` | Runtime report / config scaffold | pix |

**Sync is rclone.** `mirror` and `publish` only compose `rclone bisync` arguments; rclone handles authentication, transfers and conflict handling. Configure remotes with `rclone config` before using them. `pix` never reads or writes `rclone.conf`.

`mirror` / `publish` are **bidirectional mirrors**, not one-way copies. Deletions propagate in both directions.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- rclone >= 1.66 for `mirror` / `publish` (and for `pix doctor` to report a clean environment)
- At least one remote already configured in rclone

`pyvips[binary]` bundles libvips, so no separate vips installation is required.

## Install

Install from PyPI:

```bash
uv tool install pix-cli
pix --version
```

Upgrade with `uv tool upgrade pix-cli`; remove with `uv tool uninstall pix-cli`. Linux installs `~/.local/bin/pix`; Windows installs `%USERPROFILE%\.local\bin\pix.exe`.

To work on pix itself, install the checkout in editable mode:

```bash
git clone https://github.com/uzvg/pix.git
cd pix
uv tool install --editable .
pix --version
```

An editable install picks up source changes immediately. Reinstall with `uv tool install --force --editable .`.

## Configure rclone

Only needed for `mirror` / `publish`:

```bash
rclone config         # create a remote, for example onedrive or r2
rclone lsd onedrive:  # verify that it works
```

rclone stores credentials in `~/.config/rclone/rclone.conf` (Windows: `%APPDATA%\rclone\rclone.conf`). `pix` passes the remote name through and does not store or validate credentials.

## Configure pix (optional)

Default configuration paths:

- Linux/macOS: `$XDG_CONFIG_HOME/pix/config.toml`, or `~/.config/pix/config.toml`
- Windows: `%APPDATA%\pix\config.toml`

If the file is missing, built-in defaults are used. Generate a commented file with:

```bash
pix config init
```

Existing files are not overwritten; pass `--force` to replace one. Use `--config PATH` before the subcommand to point at another file; an explicit path must exist, while a missing default path is fine.

Common keys:

| Section | Key | Default | Meaning |
|---|---|---|---|
| `[shrink]` | `width` | `1920` | Longest edge in pixels |
| | `quality` | `85` | AVIF quality, 1–100 |
| | `effort` | `5` | libvips effort, 0–9 |
| | `min_size` | `0` | Skip sources smaller than this; accepts `500K`, `1M`, `2G` |
| | `include` | `jpg`, `jpeg`, `png`, `webp` | Extensions to scan, without dots |
| `[mirror]` | `include` | common image extensions | Extensions to mirror, without dots; `[]` disables the whitelist |
| | `exclude` | `[]` | rclone exclude globs; command-line values are appended |
| | `max_delete` | `10` | Delete limit as a percentage; `-1` disables, `0` forbids |
| `[publish]` | `base_url` | `""` | Public URL prefix; must start with `http://` or `https://` |
| | `urls` | `false` | Emit a JSON URL manifest |
| | `exclude` | `[]` | Same as mirror; `.pix/**` is always excluded first |
| | `max_delete` | `10` | Same as mirror |
| `[rclone]` | `binary` | `rclone` | rclone executable name or path |

Precedence is **command line > pix config > built-in defaults**. `--exclude` is appended to config excludes rather than replacing them. `-q` means quality; `--quiet` is errors-only; `-v` / `--verbose` adds per-file detail and rclone progress. `--no-urls` overrides `[publish].urls = true` for one run.

The default `[mirror].include` is `jpg`, `jpeg`, `png`, `webp`, `gif`, `bmp`, `tif`, `tiff`, `heic`, `heif`, `avif`, `jxl`, `svg`, `ico`, `cr2`, `cr3`, `nef`, `arw`, `dng`, `orf`, `raf`, `rw2`. Set it to `[]` to mirror every file.

## Quick start

1. Check the environment:

   ```bash
   pix doctor
   ```

2. Compress images:

   ```bash
   pix shrink -s ~/Pictures -d ~/Pictures-avif --dry-run
   pix shrink -s ~/Pictures -d ~/Pictures-avif -w 1920 -q 85
   ```

   The first run encodes everything; later runs skip unchanged files. State lives in `OUT/.pix/state.json`. `-w` is a maximum long edge, so smaller images are not upscaled; relative directories are preserved under OUT. Change detection uses source size/mtime and output size, not content hashes. Changing `width`, `quality` or `effort` triggers a full re-encode. `--dry-run` only prints the plan. `--prune` removes only outputs recorded in state; extra files in OUT are left alone.

3. Mirror originals:

   ```bash
   pix mirror -s ~/Pictures -d onedrive:photos --dry-run
   pix mirror -s ~/Pictures -d onedrive:photos
   ```

   The destination is created automatically. Only files matching `[mirror].include` are mirrored; other source files are ignored, and non-matching files already on the destination are left untouched. If no source file matches the filter, the source counts as empty and is refused unless you pass `--allow-empty --resync`. An empty destination initializes its own baseline, so no `--resync` is needed on the first run. Use `--resync` to rebuild the baseline when the destination already has matching content, or after changing `include`/`exclude`; `--dry-run` may create an empty destination but does not transfer or delete. After upgrading from a version without include filtering, the first run also needs `--resync`; preview with `--dry-run --resync` first (the preview does not write the baseline).

4. Publish the AVIF output:

   ```bash
   pix publish -s ~/Pictures-avif -d r2:images \
     --base-url https://img.example.com --urls > urls.json
   ```

   `publish` requires at least one `.avif` in the source unless you pass `--allow-empty --resync`; it syncs only `.avif` files and always excludes `.pix/**`. The URL manifest is printed to stdout as JSON only after a successful, non-dry-run sync. It lists every `.avif` under OUT that passed the same filters as the sync, so files excluded by `[publish].exclude` or `--exclude` do not appear. It is not limited to files changed in this run.

5. Re-run later:

   ```bash
   pix shrink -s ~/Pictures -d ~/Pictures-avif
   pix mirror -s ~/Pictures -d onedrive:photos
   pix publish -s ~/Pictures-avif -d r2:images \
     --base-url https://img.example.com --urls
   ```

   Only changes are encoded and transferred. `--base-url` is not persisted between runs; set `[publish].base_url` in config to avoid repeating it.

## Command cheat sheet

| Task | Command |
|---|---|
| Preview compression | `pix shrink -s SRC -d OUT --dry-run` |
| Re-encode everything | `pix shrink -s SRC -d OUT --force` |
| Remove tracked outputs whose source is gone | `pix shrink -s SRC -d OUT --prune` |
| Preview a sync | `pix mirror -s SRC -d REMOTE --dry-run` |
| First sync (new or empty destination) | `pix mirror -s SRC -d REMOTE` |
| Rebuild the baseline (non-empty destination, or after changing include/exclude) | `pix mirror -s SRC -d REMOTE --resync` |
| Emit a URL manifest | `pix publish -s OUT -d REMOTE --base-url https://img.example.com --urls` |
| Diagnose the environment | `pix doctor` |
| Generate a config file | `pix config init` |

## Safety rules

| Situation | What happens | Protection |
|---|---|---|
| Deletions on either side | Propagated in both directions; accidental deletion is irreversible | Preview with `--dry-run`; default `--max-delete 10` (percentage) |
| First sync (destination empty) | `pix` creates the destination and initializes the baseline | Preview with `--dry-run`; it may create an empty destination but does not transfer or delete. No manual rclone needed |
| Destination has matching content but no baseline | `pix` does not auto-resync; bisync fails instead of overwriting | Confirm which side is authoritative, then add `--resync` |
| Include/exclude filters changed | When filters are active, rclone detects the changed filters file and exits 7 | Run with `--resync` after changing `include` or `exclude`; `--dry-run --resync` previews but does not write the baseline |
| Non-image files in the source | `mirror` ignores them by default; `publish` ignores anything that is not `.avif`. If no file matches, the source counts as empty and is refused | Add extensions to `[mirror].include` if they should be mirrored; use `--allow-empty --resync` only for an intentional empty sync |
| Empty source | `mirror` / `publish` refuse. `shrink --prune` refuses only while state still has entries; empty state is a no-op | Use `--allow-empty` (plus `--resync` for sync) only when intentional |
| `a.jpg` and `a.jpeg` both exist | Both map to the same output | Detected before encoding; exits 2 |
| Two concurrent shrinks | They would write the same state and outputs | `OUT/.pix/lock` blocks the second process |
| State missing or corrupt | Full rebuild; corrupt state logs a warning | Only causes re-encoding; outputs stay valid |

## Output and exit codes

- Logs and progress go to stderr.
- stdout carries `doctor` reports, `shrink --dry-run` plans, `config init`'s `wrote <path>`, and `--urls` JSON. `mirror` / `publish --dry-run` output comes from rclone.
- Exit codes: `0` success, `1` runtime failure, `2` argument or configuration error.
- `mirror` / `publish` return rclone's bisync exit code unchanged; destination preflight failures return 1.

URL manifest example:

```json
{
  "2024/a.avif": "https://img.example.com/2024/a.avif"
}
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `pyvips/libvips unavailable` | Reinstall to bring libvips: `uv tool install --force --editable /path/to/pix`; or install libvips manually (Linux: `apt install libvips42`, Windows: libvips release zip on `PATH`) |
| rclone is older than 1.66 | Upgrade rclone |
| bisync reports a missing baseline or changed filters file | The destination has matching content, or the include/exclude rules changed (including the first run after upgrading). Confirm local should win, then add `--resync`; an empty destination initializes automatically. `--dry-run --resync` previews but does not write the baseline. |
| `OUT/.pix/lock` already exists | Check for another `shrink`, then remove a stale lock |
| Configuration fails to parse | The error names the file and location; fix the TOML or remove the file to fall back to defaults. `pix doctor` cannot run until the config parses |
| `publish` says the source has no `.avif` | Run `pix shrink` first |
| URL-related error | `base_url` must start with `http://` or `https://`, and `--urls` requires it |

## Development

```bash
uv sync
uv run pytest
uv run pix --version
uv build
```

The code must stay compatible with Python 3.11. See [development.md](https://github.com/uzvg/pix/blob/main/docs/development.md) for tests, packaging and release details. Behavior is specified in [architecture.md](https://github.com/uzvg/pix/blob/main/docs/architecture.md); rationale lives in [decisions.md](https://github.com/uzvg/pix/blob/main/docs/decisions.md).

The detailed docs are currently written in Chinese.
