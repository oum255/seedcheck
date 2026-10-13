"""Result types shared by the runner, the checks and the reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """Outcome of a single check, or of a whole release."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CheckResult:
    """The result of one check inside one container.

    ``detail`` is a short human-readable reason (for example
    "expected mode 0644, found 0600"). ``value`` is an optional short value
    shown next to the status in the matrix, such as an installed package
    version.
    """

    name: str
    status: Status
    detail: str = ""
    value: str = ""


@dataclass
class ReleaseResult:
    """Everything Seedcheck learned about one Ubuntu release."""

    release: str
    codename: str
    status: Status
    duration_s: float = 0.0
    cloud_init_version: str = ""
    error: str = ""
    checks: list[CheckResult] = field(default_factory=list)


@dataclass
class RunResult:
    """The results of a whole ``seedcheck run`` across all releases."""

    tool_version: str
    user_data: str
    started_at: str
    releases: list[ReleaseResult] = field(default_factory=list)
