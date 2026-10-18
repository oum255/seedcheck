"""The seedcheck command line."""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from seedcheck import __version__
from seedcheck.config import Config, load_config
from seedcheck.errors import ConfigError, LxdError
from seedcheck.lxd import Lxd, LxdClient
from seedcheck.releases import SUPPORTED, Release, resolve
from seedcheck.report import (
    EXIT_FAILED,
    EXIT_INFRA,
    EXIT_OK,
    exit_code,
    render_json,
    render_markdown,
    render_table,
)
from seedcheck.runner import RunOptions, run_all
from seedcheck.userdata import has_errors, validate_userdata

__all__ = ["EXIT_FAILED", "EXIT_INFRA", "EXIT_INTERRUPTED", "EXIT_OK", "EXIT_USAGE", "main"]

EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive whole number of seconds, got {text!r}"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedcheck",
        description="Test a cloud-init configuration across Ubuntu releases in LXD containers.",
        epilog="Exit codes: 0 all passed, 1 a check failed, 2 invalid usage or configuration, "
        "3 infrastructure error, 130 interrupted.",
    )
    parser.add_argument("--version", action="version", version=f"seedcheck {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    run = commands.add_parser(
        "run",
        help="validate, launch, check and report",
        description="Launch the user-data on each release, run the checks and print the matrix.",
    )
    run.add_argument("config", type=Path, help="path to seedcheck.yaml")
    run.add_argument(
        "--release",
        action="append",
        metavar="REL",
        help="only test this release (version or codename); repeatable; overrides the config",
    )
    run.add_argument(
        "--markdown", type=Path, metavar="PATH", help="also write the matrix as Markdown"
    )
    run.add_argument("--json", type=Path, metavar="PATH", help="also write full results as JSON")
    run.add_argument(
        "--artifacts-dir",
        type=Path,
        metavar="DIR",
        help="save each container's cloud-init logs here",
    )
    run.add_argument(
        "--timeout", type=_positive_int, metavar="SECONDS", help="override the config's timeout"
    )
    run.add_argument("--keep", action="store_true", help="do not delete containers (for debugging)")
    run.add_argument(
        "-v", "--verbose", action="store_true", help="show every lxc command and its output"
    )

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


def main(argv: Sequence[str] | None = None, *, client: Lxd | None = None) -> int:
    """Run the CLI and return the exit code.

    Tests pass a fake `client` instead of the real LxdClient.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args, client)
        if args.command == "validate":
            return _validate(args.config)
        if args.command == "releases":
            return _releases()
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    parser.print_help(sys.stderr)
    return EXIT_USAGE


def _load_and_validate(config_path: Path) -> Config | None:
    """Load the config and check its user-data. Prints problems, returns None if invalid."""
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    problems = validate_userdata(config.user_data)
    for problem in problems:
        print(problem, file=sys.stderr)
    return None if has_errors(problems) else config


def _validate(config_path: Path) -> int:
    config = _load_and_validate(config_path)
    if config is None:
        return EXIT_USAGE
    releases = ", ".join(release.version for release in config.releases)
    print(
        f"{config_path}: valid (user-data: {config.user_data}; releases: {releases}; "
        f"{config.checks.count()} checks)"
    )
    return EXIT_OK


def _run(args: argparse.Namespace, client: Lxd | None) -> int:
    config = _load_and_validate(args.config)
    if config is None:
        return EXIT_USAGE
    try:
        if args.release:
            config = dataclasses.replace(config, releases=_releases_from_flags(args.release))
    except ConfigError as exc:
        print(f"error: --release: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.timeout is not None:
        config = dataclasses.replace(config, timeout=args.timeout)
    for flag, path in (("--markdown", args.markdown), ("--json", args.json)):
        if path is not None and not path.resolve().parent.is_dir():
            print(
                f"error: {flag}: directory does not exist: {path.resolve().parent}", file=sys.stderr
            )
            return EXIT_USAGE

    if client is None:
        client = LxdClient(verbose=args.verbose)
    try:
        client.ensure_available()
    except LxdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA

    options = RunOptions(timeout=config.timeout, keep=args.keep, artifacts_dir=args.artifacts_dir)
    result = run_all(client, config, options)
    result.user_data = _display_path(config.user_data)

    print(render_table(result, color=_use_color()), end="")
    try:
        if args.markdown is not None:
            args.markdown.write_text(render_markdown(result), encoding="utf-8")
        if args.json is not None:
            args.json.write_text(render_json(result), encoding="utf-8")
    except OSError as exc:
        print(f"error: could not write report: {exc}", file=sys.stderr)
        return EXIT_INFRA
    return exit_code(result)


def _releases_from_flags(names: list[str]) -> tuple[Release, ...]:
    releases: list[Release] = []
    for name in names:
        release = resolve(name)
        if release in releases:
            raise ConfigError(f"release {release.version} ({release.codename}) is given twice")
        releases.append(release)
    return tuple(releases)


def _display_path(path: Path) -> str:
    """Show the user-data path relative to the current directory when we can."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _use_color() -> bool:
    # See https://no-color.org
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _releases() -> int:
    rows = [("VERSION", "CODENAME", "IMAGE")]
    rows += [(release.version, release.codename, release.image) for release in SUPPORTED]
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )
    return EXIT_OK
