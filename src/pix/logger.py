"""stderr logging."""

from __future__ import annotations

import sys


class Logger:
    def __init__(self, verbose: bool = False, quiet: bool = False) -> None:
        self.verbose = verbose
        self.quiet = quiet
        self._progress_active = False

    def _end_progress(self) -> None:
        if self._progress_active:
            print(file=sys.stderr)
            self._progress_active = False

    def info(self, message: str) -> None:
        if not self.quiet:
            self._end_progress()
            print(message, file=sys.stderr)

    def detail(self, message: str) -> None:
        if self.verbose and not self.quiet:
            self._end_progress()
            print(message, file=sys.stderr)

    def warning(self, message: str) -> None:
        if not self.quiet:
            self._end_progress()
            print(f"pix: warning: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        self._end_progress()
        print(f"pix: error: {message}", file=sys.stderr)

    def progress(self, message: str) -> None:
        if self.quiet:
            return
        if sys.stderr.isatty():
            print(f"\r{message}\033[K", end="", file=sys.stderr, flush=True)
            self._progress_active = True
        else:
            print(message, file=sys.stderr)
