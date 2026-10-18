from __future__ import annotations

import json

import pytest

from seedcheck.models import CheckResult, Status
from seedcheck.report import (
    EXIT_FAILED,
    EXIT_INFRA,
    EXIT_OK,
    exit_code,
    render_json,
    render_markdown,
    render_table,
)

from .sample_results import E, F, P, S, W, all_pass, mixed, passing, release, run

ALL_PASS_TABLE = """\
Seedcheck 0.1.0   user-data: examples/nginx/user-data.yaml

Check            22.04           24.04           26.04
schema           PASS            PASS            PASS
cloud-init       PASS            PASS            PASS
package: nginx   PASS (1.18.0)   PASS (1.24.0)   PASS (1.28.3)
service: nginx   PASS            PASS            PASS

Result: 3 of 3 releases passed (12 checks, 0 failed, 0 warnings)
"""

MIXED_TABLE = """\
Seedcheck 0.1.0   user-data: examples/nginx/user-data.yaml

Check            22.04   24.04           26.04
schema           PASS    PASS            SKIP
cloud-init       PASS    WARN            SKIP
package: nginx   FAIL    PASS (1.24.0)   SKIP
service: nginx   FAIL    PASS            SKIP

Result: 1 of 3 releases passed (12 checks, 2 failed, 1 warning, 1 error, 4 skipped)

Failures:
  [22.04] package: nginx: not installed
  [22.04] service: nginx: expected active, found inactive
  [26.04] timed out: container was not ready after 600s

Warnings:
  [24.04] cloud-init: Deprecated cloud-config provided: apt_update
"""

MIXED_MARKDOWN = """\
## Seedcheck results

Seedcheck 0.1.0 · user-data `examples/nginx/user-data.yaml` · 2026-10-13T18:00:00+00:00

| Check | 22.04 | 24.04 | 26.04 |
|---|---|---|---|
| schema | ✅ PASS | ✅ PASS | ⏭️ SKIP |
| cloud-init | ✅ PASS | ⚠️ WARN | ⏭️ SKIP |
| package: nginx | ❌ FAIL | ✅ PASS (1.24.0) | ⏭️ SKIP |
| service: nginx | ❌ FAIL | ✅ PASS | ⏭️ SKIP |

**Result: 1 of 3 releases passed (12 checks, 2 failed, 1 warning, 1 error, 4 skipped)**

### Failures

- [22.04] package: nginx: not installed
- [22.04] service: nginx: expected active, found inactive
- [26.04] timed out: container was not ready after 600s

### Warnings

- [24.04] cloud-init: Deprecated cloud-config provided: apt_update
"""


def test_all_pass_table() -> None:
    assert render_table(all_pass()) == ALL_PASS_TABLE


def test_mixed_table_lists_failures_and_warnings_below_the_matrix() -> None:
    assert render_table(mixed()) == MIXED_TABLE


def test_mixed_markdown() -> None:
    assert render_markdown(mixed()) == MIXED_MARKDOWN


def test_all_pass_markdown_has_no_failure_section() -> None:
    markdown = render_markdown(all_pass())
    assert "| package: nginx | ✅ PASS (1.18.0) | ✅ PASS (1.24.0) | ✅ PASS (1.28.3) |" in markdown
    assert "###" not in markdown


def test_color_wraps_only_the_status_word_and_keeps_alignment() -> None:
    colored = render_table(mixed(), color=True)
    assert "\033[31mFAIL\033[0m" in colored
    assert "\033[32mPASS\033[0m (1.24.0)" in colored
    assert "\033[33mWARN\033[0m" in colored
    assert "\033[2mSKIP\033[0m" in colored
    stripped = colored
    for code in ("\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"):
        stripped = stripped.replace(code, "")
    assert stripped == MIXED_TABLE


def test_no_color_codes_by_default() -> None:
    assert "\033[" not in render_table(mixed())


def test_check_missing_for_one_release_shows_skip() -> None:
    result = run(
        passing("22.04", "jammy", "1.18.0"),
        release("24.04", "noble", P, CheckResult("schema", P)),
    )
    lines = render_table(result).splitlines()
    assert lines[5] == "package: nginx   PASS (1.18.0)   SKIP"
    assert "| package: nginx | ✅ PASS (1.18.0) | ⏭️ SKIP |" in render_markdown(result)


def test_rows_keep_first_seen_order() -> None:
    lines = render_table(all_pass()).splitlines()[3:7]
    assert [line.split("  ")[0] for line in lines] == [
        "schema",
        "cloud-init",
        "package: nginx",
        "service: nginx",
    ]


def test_markdown_escapes_pipes() -> None:
    result = run(release("24.04", "noble", F, CheckResult("command: a | b", F, "x | y")))
    markdown = render_markdown(result)
    assert "| command: a \\| b | ❌ FAIL |" in markdown
    assert "- [24.04] command: a \\| b: x \\| y" in markdown


def test_json_round_trips_with_every_field() -> None:
    data = json.loads(render_json(mixed()))
    assert data["tool"] == "seedcheck"
    assert data["version"] == "0.1.0"
    assert data["exit_code"] == EXIT_INFRA
    assert data["summary"] == {
        "releases": 3,
        "releases_passed": 1,
        "checks": 12,
        "failed": 2,
        "warnings": 1,
        "errors": 1,
        "skipped": 4,
    }
    jammy = data["releases"][0]
    assert jammy["release"] == "22.04"
    assert jammy["status"] == "FAIL"
    assert jammy["duration_s"] == 42.5
    assert jammy["cloud_init_version"] == "26.1-0ubuntu1"
    assert jammy["checks"][2] == {
        "name": "package: nginx",
        "status": "FAIL",
        "detail": "not installed",
        "value": "",
    }
    assert data["releases"][2]["error"] == "timed out: container was not ready after 600s"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([P, P], EXIT_OK),
        ([P, W], EXIT_OK),
        ([P, F], EXIT_FAILED),
        ([F, E], EXIT_INFRA),
        ([P, S], EXIT_OK),
    ],
)
def test_exit_code_from_check_statuses(statuses: list[Status], expected: int) -> None:
    checks = [CheckResult(f"c{i}", status) for i, status in enumerate(statuses)]
    assert exit_code(run(release("24.04", "noble", P, *checks))) == expected


def test_errored_release_gives_exit_code_3_even_with_failures_elsewhere() -> None:
    assert exit_code(mixed()) == EXIT_INFRA


def test_empty_run_exits_zero() -> None:
    assert exit_code(run()) == EXIT_OK
