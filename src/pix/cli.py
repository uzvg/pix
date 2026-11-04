"""pix command-line entry point."""

from __future__ import annotations

import sys


def main() -> int:
    print("pix: not implemented yet; see docs/architecture.md", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
