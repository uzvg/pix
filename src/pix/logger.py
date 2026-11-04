"""stderr logging."""

from __future__ import annotations

import sys


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
