"""Load seedcheck.yaml and validate it into immutable dataclasses.

Every rule violation raises a ConfigError that names the file, the key (for
example ``checks.files[0].mode``) and the problem, so a typo is reported
before any container is launched.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from seedcheck.errors import ConfigError
from seedcheck.releases import DEFAULT_RELEASES, Release, resolve

DEFAULT_TIMEOUT = 600

# Every state `systemctl is-active` can print.
SERVICE_STATES = (
    "active",
    "inactive",
    "failed",
    "activating",
    "deactivating",
    "reloading",
    "refreshing",
    "maintenance",
)

_OCTAL_MODE = re.compile(r"[0-7]{3,4}")

_TOP_LEVEL_KEYS = ("user_data", "releases", "timeout", "checks")
_CHECK_KINDS = ("packages", "services", "files", "users", "commands")


@dataclass(frozen=True)
class PackageCheck:
    name: str


@dataclass(frozen=True)
class ServiceCheck:
    name: str
    state: str = "active"


@dataclass(frozen=True)
class FileCheck:
    path: str
    contains: str | None = None
    mode: str | None = None
    owner: str | None = None


@dataclass(frozen=True)
class UserCheck:
    name: str
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandCheck:
    name: str
    run: str
    expect_exit: int = 0
    expect_stdout_contains: str | None = None


@dataclass(frozen=True)
class Checks:
    packages: tuple[PackageCheck, ...] = ()
    services: tuple[ServiceCheck, ...] = ()
    files: tuple[FileCheck, ...] = ()
    users: tuple[UserCheck, ...] = ()
    commands: tuple[CommandCheck, ...] = ()

    def count(self) -> int:
        """Number of user-defined checks (schema and cloud-init not included)."""
        return sum(len(getattr(self, kind)) for kind in _CHECK_KINDS)


@dataclass(frozen=True)
class Config:
    path: Path
    user_data: Path
    releases: tuple[Release, ...]
    timeout: int
    checks: Checks


class _Invalid(Exception):
    """A rule violation at a given key; load_config adds the file name."""

    def __init__(self, where: str, problem: str) -> None:
        super().__init__(f"{where}: {problem}")


def load_config(path: Path) -> Config:
    """Read and validate a seedcheck.yaml file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except UnicodeDecodeError:
        raise ConfigError(f"{path}: file is not valid UTF-8") from None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read file: {exc.strerror}") from None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"{path}:{_yaml_error_location(exc)} invalid YAML: {_yaml_problem(exc)}"
        ) from None

    if data is None:
        raise ConfigError(f"{path}: file is empty")
    try:
        return _parse(data, path)
    except _Invalid as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _yaml_error_location(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    return f"{mark.line + 1}:" if mark is not None else ""


def _yaml_problem(exc: yaml.YAMLError) -> str:
    return getattr(exc, "problem", None) or str(exc)


def _parse(data: Any, path: Path) -> Config:
    mapping = _mapping(data, "top level")
    _check_keys(mapping, _TOP_LEVEL_KEYS, "")

    if "user_data" not in mapping:
        raise _Invalid("user_data", "required key is missing")
    user_data = _user_data_path(mapping["user_data"], path)

    releases = _releases(mapping.get("releases", list(DEFAULT_RELEASES)))
    timeout = _timeout(mapping.get("timeout", DEFAULT_TIMEOUT))

    checks_data = mapping.get("checks")
    checks = Checks() if checks_data is None else _checks(checks_data)
    return Config(path, user_data, releases, timeout, checks)


def _user_data_path(value: Any, config_path: Path) -> Path:
    relative = _string(value, "user_data")
    resolved = (config_path.parent / relative).resolve()
    if not resolved.exists():
        raise _Invalid("user_data", f"file not found: {resolved}")
    if not resolved.is_file():
        raise _Invalid("user_data", f"not a regular file: {resolved}")
    return resolved


def _releases(value: Any) -> tuple[Release, ...]:
    items = _list(value, "releases")
    if not items:
        raise _Invalid("releases", "must list at least one release")
    releases: list[Release] = []
    for index, item in enumerate(items):
        where = f"releases[{index}]"
        if isinstance(item, float):
            raise _Invalid(
                where, f'must be a quoted string: write "{item}", YAML reads {item} as a number'
            )
        name = _string(item, where)
        try:
            release = resolve(name)
        except ConfigError as exc:
            raise _Invalid(where, str(exc)) from None
        if release in releases:
            raise _Invalid(where, f"release {release.version} ({release.codename}) is listed twice")
        releases.append(release)
    return tuple(releases)


def _timeout(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _Invalid("timeout", f"must be a positive whole number of seconds, got {value!r}")
    return value


def _checks(value: Any) -> Checks:
    mapping = _mapping(value, "checks")
    _check_keys(mapping, _CHECK_KINDS, "checks")
    return Checks(
        packages=tuple(_package(item, where) for where, item in _entries(mapping, "packages")),
        services=tuple(_service(item, where) for where, item in _entries(mapping, "services")),
        files=tuple(_file(item, where) for where, item in _entries(mapping, "files")),
        users=tuple(_user(item, where) for where, item in _entries(mapping, "users")),
        commands=tuple(_command(item, where) for where, item in _entries(mapping, "commands")),
    )


def _entries(checks: dict[str, Any], kind: str) -> list[tuple[str, Any]]:
    """Return (key path, entry) pairs for one kind of check; missing or null means none."""
    value = checks.get(kind)
    if value is None:
        return []
    items = _list(value, f"checks.{kind}")
    return [(f"checks.{kind}[{index}]", item) for index, item in enumerate(items)]


def _package(item: Any, where: str) -> PackageCheck:
    if isinstance(item, str):
        return PackageCheck(_string(item, where))
    fields = _fields(item, where, required=("name",), optional=())
    return PackageCheck(_string(fields["name"], f"{where}.name"))


def _service(item: Any, where: str) -> ServiceCheck:
    if isinstance(item, str):
        return ServiceCheck(_string(item, where))
    fields = _fields(item, where, required=("name",), optional=("state",))
    name = _string(fields["name"], f"{where}.name")
    state = _string(fields.get("state", "active"), f"{where}.state")
    if state not in SERVICE_STATES:
        raise _Invalid(
            f"{where}.state",
            f"unknown state {state!r}; expected one of {', '.join(SERVICE_STATES)}",
        )
    return ServiceCheck(name, state)


def _file(item: Any, where: str) -> FileCheck:
    fields = _fields(item, where, required=("path",), optional=("contains", "mode", "owner"))
    path = _string(fields["path"], f"{where}.path")
    if not path.startswith("/"):
        raise _Invalid(f"{where}.path", f"must be an absolute path, got {path!r}")
    raw_mode = fields.get("mode")
    if isinstance(raw_mode, int) and not isinstance(raw_mode, bool):
        # YAML 1.1 reads an unquoted 0644 as octal, so it arrives here as 420.
        raise _Invalid(
            f"{where}.mode",
            f'must be a quoted octal string like "0644"; unquoted, YAML read it as '
            f"the number {raw_mode}",
        )
    mode = _optional_string(raw_mode, f"{where}.mode", hint='a quoted octal string like "0644"')
    if mode is not None and not _OCTAL_MODE.fullmatch(mode):
        raise _Invalid(f"{where}.mode", f'must be an octal string like "0644", got {mode!r}')
    return FileCheck(
        path=path,
        contains=_optional_string(fields.get("contains"), f"{where}.contains"),
        mode=mode,
        owner=_optional_string(fields.get("owner"), f"{where}.owner"),
    )


def _user(item: Any, where: str) -> UserCheck:
    fields = _fields(item, where, required=("name",), optional=("groups",))
    name = _string(fields["name"], f"{where}.name")
    groups = _list(fields.get("groups", []), f"{where}.groups")
    return UserCheck(
        name, tuple(_string(group, f"{where}.groups[{i}]") for i, group in enumerate(groups))
    )


def _command(item: Any, where: str) -> CommandCheck:
    fields = _fields(
        item,
        where,
        required=("name", "run"),
        optional=("expect_exit", "expect_stdout_contains"),
    )
    expect_exit = fields.get("expect_exit", 0)
    if (
        isinstance(expect_exit, bool)
        or not isinstance(expect_exit, int)
        or not 0 <= expect_exit <= 255
    ):
        raise _Invalid(
            f"{where}.expect_exit", f"must be a whole number from 0 to 255, got {expect_exit!r}"
        )
    return CommandCheck(
        name=_string(fields["name"], f"{where}.name"),
        run=_string(fields["run"], f"{where}.run"),
        expect_exit=expect_exit,
        expect_stdout_contains=_optional_string(
            fields.get("expect_stdout_contains"), f"{where}.expect_stdout_contains"
        ),
    )


# Generic helpers. Each one raises _Invalid naming `where` on a type mismatch.


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Invalid(where, f"must be a mapping of keys to values, got {_type_name(value)}")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise _Invalid(where, f"must be a list, got {_type_name(value)}")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise _Invalid(where, f"must be a string, got {_type_name(value)}")
    if not value.strip():
        raise _Invalid(where, "must not be empty")
    return value


def _optional_string(value: Any, where: str, hint: str = "a string") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid(where, f"must be {hint}, got {_type_name(value)} {value!r}")
    return value


def _fields(
    item: Any, where: str, required: tuple[str, ...], optional: tuple[str, ...]
) -> dict[str, Any]:
    fields = _mapping(item, where)
    _check_keys(fields, required + optional, where)
    for key in required:
        if key not in fields:
            raise _Invalid(f"{where}.{key}", "required key is missing")
    return fields


def _check_keys(mapping: dict[str, Any], allowed: tuple[str, ...], where: str) -> None:
    for key in mapping:
        if key in allowed:
            continue
        key_path = f"{where}.{key}" if where else str(key)
        problem = f"unknown key (allowed: {', '.join(allowed)})"
        close = difflib.get_close_matches(str(key), allowed, n=1)
        if close:
            problem += f"; did you mean {close[0]!r}?"
        raise _Invalid(key_path, problem)


def _type_name(value: Any) -> str:
    if value is None:
        return "nothing (null)"
    return {
        bool: "a boolean",
        int: "a number",
        float: "a number",
        str: "a string",
        list: "a list",
        dict: "a mapping",
    }.get(type(value), type(value).__name__)
