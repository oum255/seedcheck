"""Command-line entry point for Seedcheck."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from seedcheck import __version__
from seedcheck.config import load_config
from seedcheck.errors import ConfigError
from seedcheck.releases import SUPPORTED
from seedcheck.userdata import has_errors, validate_userdata

# Exit codes, as documented in the README.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INFRA = 3
EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedcheck",
        description="Test a cloud-init configuration across Ubuntu releases in LXD containers.",
    )
    parser.add_argument("--version", action="version", version=f"seedcheck {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    validate = commands.add_parser(
        "validate",
        help="statically validate a seedcheck.yaml and its user-data (no LXD needed)",
        description="Check a seedcheck.yaml file and the user-data it points to, "
        "without launching any container.",
    )
    validate.add_argument("config", type=Path, help="path to seedcheck.yaml")

    commands.add_parser(
        "releases",
        help="list the supported Ubuntu releases",
        description="List the supported Ubuntu releases, their codenames and LXD images.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.config)
    if args.command == "releases":
        return _releases()
    parser.print_help(sys.stderr)
    return EXIT_USAGE


def _validate(config_path: Path) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    problems = validate_userdata(config.user_data)
    for problem in problems:
        print(problem, file=sys.stderr)
    if has_errors(problems):
        return EXIT_USAGE

    releases = ", ".join(release.version for release in config.releases)
    print(
        f"{config_path}: valid (user-data: {config.user_data}; releases: {releases}; "
        f"{config.checks.count()} checks)"
    )
    return EXIT_OK


def _releases() -> int:
    rows = [("VERSION", "CODENAME", "IMAGE")]
    rows += [(release.version, release.codename, release.image) for release in SUPPORTED]
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )
    return EXIT_OK
