"""Telltale: a local flight recorder for coding-agent sessions.

The version is read from the installed distribution metadata rather than written
here, so pyproject.toml holds the only copy of the number. A version restated in two
files is two facts that can disagree, and the CLI is where the disagreement would
surface.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("telltale")
except PackageNotFoundError:
    # Reachable only when `telltale` is imported from a source tree that was never
    # installed, which the src layout makes deliberate work (PYTHONPATH=src). The string
    # is a PEP 440 local version that no release can equal, so a report carrying it is
    # visibly not a report about a release.
    __version__ = "0+unknown"

__all__ = ["__version__"]
