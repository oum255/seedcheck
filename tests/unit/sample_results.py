"""Hand-built RunResults shared by the report and CLI tests."""

from __future__ import annotations

from seedcheck.models import CheckResult, ReleaseResult, RunResult, Status

P, F, W, S, E = Status.PASS, Status.FAIL, Status.WARN, Status.SKIP, Status.ERROR


def run(*releases: ReleaseResult) -> RunResult:
    return RunResult(
        "0.1.0", "examples/nginx/user-data.yaml", "2026-10-13T18:00:00+00:00", list(releases)
    )


def release(
    version: str, codename: str, status: Status, *checks: CheckResult, error: str = ""
) -> ReleaseResult:
    return ReleaseResult(version, codename, status, 42.5, "26.1-0ubuntu1", error, list(checks))


def passing(version: str, codename: str, nginx: str) -> ReleaseResult:
    return release(
        version,
        codename,
        P,
        CheckResult("schema", P),
        CheckResult("cloud-init", P),
        CheckResult("package: nginx", P, value=nginx),
        CheckResult("service: nginx", P),
    )


def all_pass() -> RunResult:
    return run(
        passing("22.04", "jammy", "1.18.0"),
        passing("24.04", "noble", "1.24.0"),
        passing("26.04", "resolute", "1.28.3"),
    )


def mixed() -> RunResult:
    """22.04 fails a check, 24.04 only warns, 26.04 never started."""
    return run(
        release(
            "22.04",
            "jammy",
            F,
            CheckResult("schema", P),
            CheckResult("cloud-init", P),
            CheckResult("package: nginx", F, "not installed"),
            CheckResult("service: nginx", F, "expected active, found inactive"),
        ),
        release(
            "24.04",
            "noble",
            W,
            CheckResult("schema", P),
            CheckResult("cloud-init", W, "Deprecated cloud-config provided: apt_update"),
            CheckResult("package: nginx", P, value="1.24.0"),
            CheckResult("service: nginx", P),
        ),
        release(
            "26.04",
            "resolute",
            E,
            CheckResult("schema", S, "not run: timed out"),
            CheckResult("cloud-init", S, "not run: timed out"),
            CheckResult("package: nginx", S, "not run: timed out"),
            CheckResult("service: nginx", S, "not run: timed out"),
            error="timed out: container was not ready after 600s",
        ),
    )
