"""Render a RunResult as a terminal table, Markdown or JSON, and pick the exit code.

Everything here is a pure function of the RunResult, so the output can be
tested with exact expected strings.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from seedcheck.models import CheckResult, ReleaseResult, RunResult, Status

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INFRA = 3

_COLORS = {
    Status.PASS: "\033[32m",  # green
    Status.FAIL: "\033[31m",  # red
    Status.WARN: "\033[33m",  # yellow
    Status.SKIP: "\033[2m",  # dim
    Status.ERROR: "\033[1;35m",  # bold magenta
}
_RESET = "\033[0m"

_MARKDOWN_ICONS = {
    Status.PASS: "✅",
    Status.FAIL: "❌",
    Status.WARN: "⚠️",
    Status.SKIP: "⏭️",
    Status.ERROR: "💥",
}


def exit_code(result: RunResult) -> int:
    """3 if anything hit an infrastructure error, else 1 if any check failed, else 0.

    An ERROR outranks a FAIL: the results are incomplete, so the run cannot
    honestly be called a plain failure of the configuration.
    """
    statuses = {release.status for release in result.releases}
    statuses |= {check.status for release in result.releases for check in release.checks}
    if Status.ERROR in statuses:
        return EXIT_INFRA
    if Status.FAIL in statuses:
        return EXIT_FAILED
    return EXIT_OK


def render_table(result: RunResult, color: bool = False) -> str:
    rows = _check_names(result)
    header = ["Check", *(release.release for release in result.releases)]
    cells = [[_cell(_find(release, name)) for release in result.releases] for name in rows]
    table = [header, *([name, *row] for name, row in zip(rows, cells, strict=True))]
    widths = [max(len(line[column]) for line in table) for column in range(len(header))]

    def line(texts: list[tuple[str, Status | None]]) -> str:
        parts = []
        for (text, status), width in zip(texts, widths, strict=True):
            padded = text.ljust(width)
            if color and status is not None:
                padded = padded.replace(status.value, f"{_COLORS[status]}{status.value}{_RESET}", 1)
            parts.append(padded)
        return "   ".join(parts).rstrip()

    out = [f"Seedcheck {result.tool_version}   user-data: {result.user_data}", ""]
    out.append(line([(text, None) for text in header]))
    for name, row in zip(rows, cells, strict=True):
        statuses = [_find_status(release, name) for release in result.releases]
        out.append(line([(name, None), *zip(row, statuses, strict=True)]))
    out += ["", _summary(result)]

    failures = _problem_lines(result, (Status.FAIL, Status.ERROR))
    if failures:
        out += ["", "Failures:", *(f"  {text}" for text in failures)]
    warnings = _problem_lines(result, (Status.WARN,))
    if warnings:
        out += ["", "Warnings:", *(f"  {text}" for text in warnings)]
    return "\n".join(out) + "\n"


def render_markdown(result: RunResult) -> str:
    releases = result.releases
    out = [
        "## Seedcheck results",
        "",
        f"Seedcheck {result.tool_version} · user-data `{result.user_data}` · {result.started_at}",
        "",
        "| Check | " + " | ".join(release.release for release in releases) + " |",
        "|---" * (len(releases) + 1) + "|",
    ]
    for name in _check_names(result):
        cells = []
        for release in releases:
            check = _find(release, name)
            status = check.status if check else Status.SKIP
            cells.append(f"{_MARKDOWN_ICONS[status]} {_escape(_cell(check))}")
        out.append(f"| {_escape(name)} | " + " | ".join(cells) + " |")
    out += ["", f"**{_summary(result)}**"]

    for title, statuses in (
        ("Failures", (Status.FAIL, Status.ERROR)),
        ("Warnings", (Status.WARN,)),
    ):
        lines = _problem_lines(result, statuses)
        if lines:
            out += ["", f"### {title}", "", *(f"- {_escape(text)}" for text in lines)]
    return "\n".join(out) + "\n"


def render_json(result: RunResult) -> str:
    data: dict[str, Any] = {
        "tool": "seedcheck",
        "version": result.tool_version,
        "started_at": result.started_at,
        "user_data": result.user_data,
        "exit_code": exit_code(result),
        "summary": _counts(result),
        "releases": [asdict(release) for release in result.releases],
    }
    # Status is a str enum, so json writes it as its plain value ("PASS").
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _check_names(result: RunResult) -> list[str]:
    """Row names in first-seen order: schema, cloud-init, then the config's order."""
    names: dict[str, None] = {}
    for release in result.releases:
        for check in release.checks:
            names.setdefault(check.name)
    return list(names)


def _find(release: ReleaseResult, name: str) -> CheckResult | None:
    return next((check for check in release.checks if check.name == name), None)


def _find_status(release: ReleaseResult, name: str) -> Status:
    check = _find(release, name)
    return check.status if check else Status.SKIP


def _cell(check: CheckResult | None) -> str:
    if check is None:
        return Status.SKIP.value
    return f"{check.status.value} ({check.value})" if check.value else check.status.value


def _counts(result: RunResult) -> dict[str, int]:
    checks = [check for release in result.releases for check in release.checks]
    return {
        "releases": len(result.releases),
        "releases_passed": sum(r.status in (Status.PASS, Status.WARN) for r in result.releases),
        "checks": len(checks),
        "failed": sum(check.status is Status.FAIL for check in checks),
        "warnings": sum(check.status is Status.WARN for check in checks),
        "errors": sum(check.status is Status.ERROR for check in checks)
        + sum(release.status is Status.ERROR for release in result.releases),
        "skipped": sum(check.status is Status.SKIP for check in checks),
    }


def _summary(result: RunResult) -> str:
    counts = _counts(result)
    parts = [
        _plural(counts["checks"], "check"),
        f"{counts['failed']} failed",
        _plural(counts["warnings"], "warning"),
    ]
    if counts["errors"]:
        parts.append(_plural(counts["errors"], "error"))
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped")
    return (
        f"Result: {counts['releases_passed']} of {_plural(counts['releases'], 'release')} passed "
        f"({', '.join(parts)})"
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _problem_lines(result: RunResult, statuses: tuple[Status, ...]) -> list[str]:
    """One line per problem. An errored release is one line, not one per skipped check."""
    lines = []
    for release in result.releases:
        if release.error and Status.ERROR in statuses:
            lines.append(f"[{release.release}] {release.error}")
        for check in release.checks:
            if check.status in statuses:
                detail = f": {check.detail}" if check.detail else ""
                lines.append(f"[{release.release}] {check.name}{detail}")
    return lines


def _escape(text: str) -> str:
    """Keep a pipe in a check name or detail from breaking the Markdown table."""
    return text.replace("|", "\\|")
