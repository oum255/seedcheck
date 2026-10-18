"""Result types shared by the runner, checks and reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """Result of a check, or of a whole release."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CheckResult:
    """Result of one check in one container.

    ``detail`` is a short reason, e.g. "expected mode 0644, found 0600".
    ``value`` is shown next to the status in the table, e.g. a package version.
    """

    name: str
    status: Status
    detail: str = ""
    value: str = ""


@dataclass
class ReleaseResult:
    """What we found on one Ubuntu release."""

    release: str
    codename: str
    status: Status
    duration_s: float = 0.0
    cloud_init_version: str = ""
    error: str = ""
    checks: list[CheckResult] = field(default_factory=list)


@dataclass
class RunResult:
    """Results of a whole ``seedcheck run``."""

    tool_version: str
    user_data: str
    started_at: str
    releases: list[ReleaseResult] = field(default_factory=list)
