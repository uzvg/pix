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
| `pix doctor` | Check Python, libvips, rclone and configuration |
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
| `doctor` / `config init` | Dependency checks / config scaffold | pix |

**Sync is rclone.** `mirror` and `publish` only compose `rclone bisync` arguments; rclone handles authentication, transfers and conflict handling. Configure remotes with `rclone config` before using them. `pix` never reads or writes `rclone.conf`.

`mirror` / `publish` are **bidirectional mirrors**, not one-way copies. Deletions propagate in both directions.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- rclone >= 1.66 for `mirror` / `publish`
- At least one remote already configured in rclone

`pyvips[binary]` bundles libvips, so no separate vips installation is required.

## Install

Not yet published on PyPI; install from source:

```bash
git clone https://github.com/uzvg/pix.git
cd pix
uv tool install --editable .
pix --version
```

Linux installs `~/.local/bin/pix`; Windows installs `%USERPROFILE%\.local\bin\pix.exe`. An editable install picks up source changes immediately. Reinstall with `uv tool install --force --editable .`, or remove it with `uv tool uninstall pix-cli`.

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

Existing files are not overwritten; pass `--force` to replace one. Use `--config PATH` before the subcommand to point at another file.

Common keys:

| Section | Key | Default | Meaning |
|---|---|---|---|
| `[shrink]` | `width` | `1920` | Longest edge in pixels |
| | `quality` | `85` | AVIF quality, 1–100 |
| | `effort` | `5` | libvips effort, 0–9 |
| | `min_size` | `0` | Skip sources smaller than this; accepts `500K`, `1M`, `2G` |
| | `include` | `jpg`, `jpeg`, `png`, `webp` | Extensions to scan, without dots |
| `[mirror]` | `exclude` | `[]` | rclone exclude globs; command-line values are appended |
| | `max_delete` | `10` | Delete limit as a percentage; `-1` disables, `0` forbids |
| `[publish]` | `base_url` | `""` | Public URL prefix; must start with `http://` or `https://` |
| | `urls` | `false` | Emit a JSON URL manifest |
| | `exclude` | `[]` | Same as mirror |
| | `max_delete` | `10` | Same as mirror |
| `[rclone]` | `binary` | `rclone` | rclone executable name or path |

Precedence is **command line > pix config > built-in defaults**. `--exclude` is appended to config excludes rather than replacing them. `-q` means quality; silence is `--quiet` (no short form).

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

   The first run encodes everything; later runs skip unchanged files. State lives in `OUT/.pix/state.json`. Changing `width`, `quality` or `effort` triggers a full re-encode. `--dry-run` only prints the plan.

3. Mirror originals:

   ```bash
   pix mirror -s ~/Pictures -d onedrive:photos --dry-run
   pix mirror -s ~/Pictures -d onedrive:photos
   ```

   The destination is created automatically. An empty destination initializes its own baseline, so no `--resync` is needed on the first run. Use `--resync` only to rebuild the baseline when the destination already has content, or after changing excludes. Preview with `--dry-run` first.

4. Publish the AVIF output:

   ```bash
   pix publish -s ~/Pictures-avif -d r2:images \
     --base-url https://img.example.com --urls > urls.json
   ```

   `publish` requires at least one `.avif` in the source and always excludes `.pix/**`. The URL manifest is printed to stdout as JSON only after a successful, non-dry-run sync.

5. Re-run later:

   ```bash
   pix shrink -s ~/Pictures -d ~/Pictures-avif
   pix mirror -s ~/Pictures -d onedrive:photos
   pix publish -s ~/Pictures-avif -d r2:images --urls
   ```

   Only changes are encoded and transferred.

## Command cheat sheet

| Task | Command |
|---|---|
| Preview compression | `pix shrink -s SRC -d OUT --dry-run` |
| Re-encode everything | `pix shrink -s SRC -d OUT --force` |
| Remove outputs whose source is gone | `pix shrink -s SRC -d OUT --prune` |
| Preview a sync | `pix mirror -s SRC -d REMOTE --dry-run` |
| First sync (new or empty destination) | `pix mirror -s SRC -d REMOTE` |
| Rebuild the baseline (non-empty destination, or after changing excludes) | `pix mirror -s SRC -d REMOTE --resync` |
| Emit a URL manifest | `pix publish -s OUT -d REMOTE --base-url https://img.example.com --urls` |
| Diagnose the environment | `pix doctor` |
| Generate a config file | `pix config init` |

## Safety rules

| Situation | What happens | Protection |
|---|---|---|
| Deletions on either side | Propagated in both directions; accidental deletion is irreversible | Preview with `--dry-run`; default `--max-delete 10` (percentage) |
| First sync (destination empty) | `pix` creates the destination and initializes the baseline | Preview with `--dry-run`; no manual rclone needed |
| Destination has content but no baseline | `pix` does not auto-resync; bisync fails instead of overwriting | Confirm which side is authoritative, then add `--resync` |
| Excludes changed | The old baseline may treat excluded files as deletions | Run with `--resync` again after changing excludes |
| Empty source | `mirror` / `publish` refuse; `shrink --prune` also refuses | Use `--allow-empty` (plus `--resync` for sync) only when intentional |
| `a.jpg` and `a.jpeg` both exist | Both map to the same output | Detected before encoding; exits 2 |
| Two concurrent shrinks | They would write the same state and outputs | `OUT/.pix/lock` blocks the second process |
| State lost or corrupt | Warning, then a full rebuild | Only causes re-encoding; outputs stay valid |

## Output and exit codes

- Logs and progress go to stderr.
- stdout carries `doctor` reports, `--dry-run` plans and `--urls` JSON.
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
| bisync reports a missing baseline | The destination is non-empty. Confirm local should win, then add `--resync`; an empty destination initializes automatically. |
| `OUT/.pix/lock` already exists | Check for another `shrink`, then remove a stale lock |
| Configuration fails to parse | Run `pix doctor` to locate the file; fix the TOML or remove the file to fall back to defaults |
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
