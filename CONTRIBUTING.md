# Contributing to Seedcheck

Thanks for helping. This page covers the development setup, the code style,
the two most common changes (a new check type, a new Ubuntu release) and
the commit message format.

## Setup

You need Python 3.10 or newer. Unit tests run anywhere, including macOS.
Integration tests, package builds and real runs need an Ubuntu machine or
VM with LXD (see `scripts/setup-lxd.sh`).

```bash
git clone https://github.com/oum255/seedcheck.git && cd seedcheck
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Before sending a change, run what CI runs:

```bash
ruff check . && ruff format --check . && mypy src
shellcheck scripts/*.sh
pytest --cov=seedcheck          # must stay at 90% or more
pytest -m integration           # if you touched lxd.py, runner.py or checks.py
```

## Code style

- Python 3.10 compatible: Ubuntu 22.04 ships 3.10, and the package must
  install there. No `tomllib`, `typing.Self`, `ExceptionGroup`, `StrEnum`
  or other 3.11+ features; ruff (`target-version = "py310"`) and mypy
  (`python_version = "3.10"`) catch most of them.
- `ruff format` decides formatting; lines are at most 100 characters.
- `mypy --strict` on `src/`: every function is fully typed.
- The only run-time dependency is PyYAML. Use the standard library for
  everything else, so the Debian package stays simple.
- Never call a subprocess through a shell on the host: `subprocess.run`
  always gets an argument list. Inside containers, values from
  `seedcheck.yaml` are separate arguments to `lxc exec`, never pasted into
  a command string (the `command` check, which runs the user's own shell
  command, is the one exception).
- All `lxc` calls go through `LxdClient._run()`.
- Tests come with the code, in the same change. Name tests after the
  behaviour they check, e.g. `test_missing_cloud_config_header_is_rejected`.
  A bug fix comes with a test that fails before the fix, and a line in
  `CHANGELOG.md`.
- Everything in the repository is written in English.

## Adding a check type

Say you want a `ports` check that a TCP port is listening.

1. **Config** (`src/seedcheck/config.py`): add a frozen dataclass
   (`PortCheck(port: int)`), a field on `Checks`, the key to `_CHECK_KINDS`,
   and a parser function that validates every field and raises `_Invalid`
   naming the key path. Unknown keys must stay an error.
2. **Check** (`src/seedcheck/checks.py`): add `port_name(spec)` and
   `check_port(client, container, spec) -> CheckResult`. Run one command
   with `client.exec(container, [...], CHECK_TIMEOUT)`, passing values as
   separate arguments, and return PASS or FAIL with a short `detail` that
   says what was expected and what was found. Add it to both `run_checks()`
   and `check_names()`, in the same position.
3. **Tests**: in `tests/unit/test_config.py`, a valid case and one case per
   validation rule; in `tests/unit/test_checks.py`, a pass case, a fail
   case and a malformed-output case, using `FakeLxdClient` with output
   captured from real containers of every release.
4. **Docs**: the `seedcheck.yaml` reference in `README.md`, the checks
   table in `docs/design.md`, and an entry under `[Unreleased]` in
   `CHANGELOG.md`.
5. Run the command by hand in a container of each supported release first.
   Output formats and exit codes differ between releases more often than
   you would expect (see "Differences between releases" in
   `docs/design.md`).

## Adding an Ubuntu release

1. Check the image exists: `lxc image info ubuntu:YY.MM`, and note its
   codename and its Python version (`lxc exec ... -- python3 --version`).
2. Add one `Release(...)` line to `SUPPORTED` in
   `src/seedcheck/releases.py`. It becomes part of the default release
   list.
3. If its Python is new, add it to the `unit` matrix in
   `.github/workflows/ci.yml`; add the release to the `install-test`
   matrix too.
4. Update the tests that list releases (`tests/unit/test_releases.py`,
   the releases output in `tests/unit/test_cli.py`), the release tables in
   `README.md` and `debian/seedcheck.1`, and `docs/design.md`.
5. Run `pytest -m integration` and `scripts/smoke-install.sh` on the new
   release, and record what differs from the others in `docs/design.md`.

## Commit messages

Small commits, one logical change each, in the
[Conventional Commits](https://www.conventionalcommits.org/) style:

```text
type(scope): what the change does, in the imperative

Optional body: why, and anything a reviewer should know.
```

Types used here: `feat`, `fix`, `test`, `docs`, `build` (packaging),
`ci`, `refactor`. The scope is the module or area, for example
`feat(checks): add a ports check` or `fix(lxd): retry deleting a busy
container`.

## Releasing

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new version
   heading.
2. Set the same version in `src/seedcheck/__init__.py` and in
   `debian/changelog` (`dch -v X.Y.Z` or by hand).
3. Tag and push: `git tag vX.Y.Z && git push --tags`. The release workflow
   checks that the three versions agree, builds the `.deb` and publishes it
   as a GitHub release, with that version's `CHANGELOG.md` section as the
   notes.
