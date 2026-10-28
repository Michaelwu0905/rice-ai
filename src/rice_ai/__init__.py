"""rice-ai: aggregate r/unixporn and summarize rice setups for the terminal."""

from __future__ import annotations

__version__ = "0.1.0"


def main() -> None:
    """Console-script entry point (``[project.scripts] rice-ai``)."""
    from .cli import main as cli_main

    raise SystemExit(cli_main())
