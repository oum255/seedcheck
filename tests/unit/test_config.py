from __future__ import annotations

from pathlib import Path

import pytest

from seedcheck.config import (
    DEFAULT_TIMEOUT,
    CommandCheck,
    FileCheck,
    PackageCheck,
    ServiceCheck,
    UserCheck,
    load_config,
)
from seedcheck.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]


def write_config(tmp_path: Path, text: str) -> Path:
    """Write seedcheck.yaml (and an empty-ish user-data.yaml next to it) into tmp_path."""
    (tmp_path / "user-data.yaml").write_text("#cloud-config\npackages: [curl]\n")
    path = tmp_path / "seedcheck.yaml"
    path.write_text(text)
    return path


def config_error(tmp_path: Path, text: str) -> str:
    with pytest.raises(ConfigError) as exc:
        load_config(write_config(tmp_path, text))
    return str(exc.value)


def test_nginx_example_loads() -> None:
    config = load_config(REPO_ROOT / "examples/nginx/seedcheck.yaml")
    assert config.user_data == REPO_ROOT / "examples/nginx/user-data.yaml"
    assert [r.version for r in config.releases] == ["22.04", "24.04", "26.04"]
    assert config.timeout == 600
    assert config.checks.packages == (PackageCheck("nginx"), PackageCheck("curl"))
    assert config.checks.services == (ServiceCheck("nginx", "active"),)
    assert config.checks.files == (
        FileCheck("/etc/motd", contains="Provisioned by cloud-init", mode="0644", owner="root"),
    )
    assert config.checks.users == (UserCheck("deploy", ("sudo",)),)
    assert config.checks.commands == (
        CommandCheck("nginx answers on port 80", "curl -fsS http://localhost", 0, "nginx"),
    )
    assert config.checks.count() == 6


def test_minimal_config_uses_defaults(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, "user_data: user-data.yaml\n"))
    assert [r.version for r in config.releases] == ["22.04", "24.04", "26.04"]
    assert config.timeout == DEFAULT_TIMEOUT
    assert config.checks.count() == 0


@pytest.mark.parametrize("checks", ["checks:\n", "checks: {}\n", "checks:\n  packages:\n"])
def test_empty_checks_section_is_allowed(tmp_path: Path, checks: str) -> None:
    config = load_config(write_config(tmp_path, "user_data: user-data.yaml\n" + checks))
    assert config.checks.count() == 0


def test_user_data_is_resolved_relative_to_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "user_data: user-data.yaml\n")
    monkeypatch.chdir("/")
    assert load_config(path).user_data == (tmp_path / "user-data.yaml").resolve()


def test_releases_can_be_given_by_codename_and_keep_their_order(tmp_path: Path) -> None:
    text = "user_data: user-data.yaml\nreleases: [resolute, '22.04']\n"
    config = load_config(write_config(tmp_path, text))
    assert [r.version for r in config.releases] == ["26.04", "22.04"]


def test_package_and_service_short_and_long_forms(tmp_path: Path) -> None:
    text = """\
user_data: user-data.yaml
checks:
  packages: [nginx, {name: curl}]
  services: [ssh, {name: nginx}, {name: apt-daily.timer, state: inactive}]
"""
    checks = load_config(write_config(tmp_path, text)).checks
    assert checks.packages == (PackageCheck("nginx"), PackageCheck("curl"))
    assert checks.services == (
        ServiceCheck("ssh"),
        ServiceCheck("nginx"),
        ServiceCheck("apt-daily.timer", "inactive"),
    )


def test_optional_check_fields_default_sensibly(tmp_path: Path) -> None:
    text = """\
user_data: user-data.yaml
checks:
  files: [{path: /etc/hosts}]
  users: [{name: root}]
  commands: [{name: true, run: "true"}]
"""
    text = text.replace("name: true", "name: always true")
    checks = load_config(write_config(tmp_path, text)).checks
    assert checks.files == (FileCheck("/etc/hosts"),)
    assert checks.users == (UserCheck("root"),)
    assert checks.commands == (CommandCheck("always true", "true", 0, None),)


@pytest.mark.parametrize("mode", ["0644", "644", "4755", "0600"])
def test_valid_octal_modes_are_accepted(tmp_path: Path, mode: str) -> None:
    text = f'user_data: user-data.yaml\nchecks:\n  files: [{{path: /x, mode: "{mode}"}}]\n'
    assert load_config(write_config(tmp_path, text)).checks.files[0].mode == mode


# Each case: (YAML body after "user_data: user-data.yaml", expected text in the error).
INVALID_CONFIGS = {
    "unknown top-level key with suggestion": (
        "timout: 10\n",
        "timout: unknown key (allowed: user_data, releases, timeout, checks); "
        "did you mean 'timeout'?",
    ),
    "unknown top-level key without suggestion": ("zzz: 1\n", "zzz: unknown key"),
    "unknown check kind": ("checks:\n  package: [nginx]\n", "checks.package: unknown key"),
    "unknown key inside a check": (
        "checks:\n  files: [{path: /x, perms: '0644'}]\n",
        "checks.files[0].perms: unknown key",
    ),
    "checks not a mapping": ("checks: [nginx]\n", "checks: must be a mapping"),
    "releases empty": ("releases: []\n", "releases: must list at least one release"),
    "releases not a list": ("releases: '22.04'\n", "releases: must be a list, got a string"),
    "release unquoted number": (
        "releases: [22.04]\n",
        'releases[0]: must be a quoted string: write "22.04"',
    ),
    "release not a string": ("releases: [true]\n", "releases[0]: must be a string"),
    "release unsupported": (
        "releases: ['20.04']\n",
        "releases[0]: unknown or unsupported release '20.04'; supported: 22.04 (jammy)",
    ),
    "release duplicated across forms": (
        "releases: ['22.04', jammy]\n",
        "releases[1]: release 22.04 (jammy) is listed twice",
    ),
    "timeout zero": ("timeout: 0\n", "timeout: must be a positive whole number"),
    "timeout negative": ("timeout: -5\n", "timeout: must be a positive whole number"),
    "timeout string": ("timeout: '600'\n", "timeout: must be a positive whole number"),
    "timeout float": ("timeout: 1.5\n", "timeout: must be a positive whole number"),
    "timeout boolean": ("timeout: true\n", "timeout: must be a positive whole number"),
    "packages not a list": ("checks:\n  packages: nginx\n", "checks.packages: must be a list"),
    "package not a string": (
        "checks:\n  packages: [42]\n",
        "checks.packages[0]: must be a mapping",
    ),
    "package empty": ("checks:\n  packages: ['']\n", "checks.packages[0]: must not be empty"),
    "package mapping without name": (
        "checks:\n  packages: [{version: 1}]\n",
        "checks.packages[0].version: unknown key",
    ),
    "service unknown state": (
        "checks:\n  services: [{name: nginx, state: running}]\n",
        "checks.services[0].state: unknown state 'running'; expected one of active",
    ),
    "service state not a string": (
        "checks:\n  services: [{name: nginx, state: 1}]\n",
        "checks.services[0].state: must be a string",
    ),
    "service missing name": (
        "checks:\n  services: [{state: active}]\n",
        "checks.services[0].name: required key is missing",
    ),
    "file given as a string": (
        "checks:\n  files: [/etc/motd]\n",
        "checks.files[0]: must be a mapping",
    ),
    "file missing path": ("checks:\n  files: [{owner: root}]\n", "checks.files[0].path: required"),
    "file relative path": (
        "checks:\n  files: [{path: etc/motd}]\n",
        "checks.files[0].path: must be an absolute path",
    ),
    "file mode unquoted": (
        "checks:\n  files: [{path: /x, mode: 0644}]\n",
        'checks.files[0].mode: must be a quoted octal string like "0644"; '
        "unquoted, YAML read it as the number 420",
    ),
    "file mode list": (
        "checks:\n  files: [{path: /x, mode: [6, 4, 4]}]\n",
        'checks.files[0].mode: must be a quoted octal string like "0644", got a list',
    ),
    "file mode not octal": (
        "checks:\n  files: [{path: /x, mode: '0988'}]\n",
        "checks.files[0].mode: must be an octal string",
    ),
    "file mode symbolic": (
        "checks:\n  files: [{path: /x, mode: rw-r--r--}]\n",
        "checks.files[0].mode: must be an octal string",
    ),
    "file contains not a string": (
        "checks:\n  files: [{path: /x, contains: 5}]\n",
        "checks.files[0].contains: must be a string",
    ),
    "file owner not a string": (
        "checks:\n  files: [{path: /x, owner: 0}]\n",
        "checks.files[0].owner: must be a string",
    ),
    "user missing name": (
        "checks:\n  users: [{groups: [sudo]}]\n",
        "checks.users[0].name: required",
    ),
    "user groups not a list": (
        "checks:\n  users: [{name: deploy, groups: sudo}]\n",
        "checks.users[0].groups: must be a list",
    ),
    "user group not a string": (
        "checks:\n  users: [{name: deploy, groups: [27]}]\n",
        "checks.users[0].groups[0]: must be a string",
    ),
    "command missing run": (
        "checks:\n  commands: [{name: x}]\n",
        "checks.commands[0].run: required",
    ),
    "command missing name": (
        "checks:\n  commands: [{run: 'true'}]\n",
        "checks.commands[0].name: required",
    ),
    "command exit code string": (
        "checks:\n  commands: [{name: x, run: 'true', expect_exit: '0'}]\n",
        "checks.commands[0].expect_exit: must be a whole number from 0 to 255",
    ),
    "command exit code out of range": (
        "checks:\n  commands: [{name: x, run: 'true', expect_exit: 256}]\n",
        "checks.commands[0].expect_exit: must be a whole number from 0 to 255",
    ),
    "command exit code boolean": (
        "checks:\n  commands: [{name: x, run: 'true', expect_exit: false}]\n",
        "checks.commands[0].expect_exit: must be a whole number",
    ),
    "command stdout expectation not a string": (
        "checks:\n  commands: [{name: x, run: 'true', expect_stdout_contains: 1}]\n",
        "checks.commands[0].expect_stdout_contains: must be a string",
    ),
}


@pytest.mark.parametrize(
    ("body", "expected"), list(INVALID_CONFIGS.values()), ids=list(INVALID_CONFIGS)
)
def test_invalid_config_is_rejected_naming_the_key(
    tmp_path: Path, body: str, expected: str
) -> None:
    message = config_error(tmp_path, "user_data: user-data.yaml\n" + body)
    assert message.startswith(f"{tmp_path / 'seedcheck.yaml'}: ")
    assert expected in message


def test_missing_user_data_key_is_rejected(tmp_path: Path) -> None:
    assert "user_data: required key is missing" in config_error(tmp_path, "timeout: 5\n")


def test_user_data_must_be_a_string(tmp_path: Path) -> None:
    assert "user_data: must be a string, got a list" in config_error(tmp_path, "user_data: []\n")


def test_missing_user_data_file_shows_the_resolved_path(tmp_path: Path) -> None:
    message = config_error(tmp_path, "user_data: nope/missing.yaml\n")
    assert f"user_data: file not found: {(tmp_path / 'nope/missing.yaml').resolve()}" in message


def test_user_data_directory_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "somedir").mkdir()
    assert "user_data: not a regular file" in config_error(tmp_path, "user_data: somedir\n")


def test_top_level_must_be_a_mapping(tmp_path: Path) -> None:
    assert "top level: must be a mapping" in config_error(tmp_path, "- user_data\n")


def test_empty_config_file_is_rejected(tmp_path: Path) -> None:
    assert config_error(tmp_path, "").endswith("seedcheck.yaml: file is empty")


def test_missing_config_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"missing\.yaml: file not found"):
        load_config(tmp_path / "missing.yaml")


def test_unreadable_config_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read file"):
        load_config(tmp_path)  # a directory cannot be read as a file


def test_config_that_is_not_utf8_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "seedcheck.yaml"
    path.write_bytes(b"user_data: \xff\xfe\n")
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config(path)


def test_invalid_yaml_reports_the_line_number(tmp_path: Path) -> None:
    message = config_error(tmp_path, "user_data: user-data.yaml\nchecks:\n  packages: [nginx\n")
    assert "seedcheck.yaml:4: invalid YAML" in message
