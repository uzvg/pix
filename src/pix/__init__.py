"""pix package."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("pix-cli")
except PackageNotFoundError:  # pragma: no cover - only when running uninstalled
    __version__ = "0.1.4"
