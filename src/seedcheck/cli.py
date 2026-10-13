"""Command-line entry point for Seedcheck."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from seedcheck import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedcheck",
        description="Test a cloud-init configuration across Ubuntu releases in LXD containers.",
    )
    parser.add_argument("--version", action="version", version=f"seedcheck {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
