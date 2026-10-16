# Design notes

This file tracks how Seedcheck works, why it is built the way it is, and
facts about the environment that were verified during development rather
than assumed. It is updated as each phase of the implementation roadmap
is completed.

## Development environment

Built on an Ubuntu 24.04 LTS LXD host running as a multipass VM (2 vCPU,
3GB RAM, 25GB disk) on an Apple Silicon Mac, since LXD requires a Linux
kernel. `ubuntu:` here always refers to the official Ubuntu simplestreams
image remote (`https://cloud-images.ubuntu.com/releases/`), LXD's default.

## Appendix facts verified (roadmap section "Annexe")

Verified during Phase 0, 2026-10-12/13:

| Fact | Result |
|---|---|
| "seedcheck" name unused by an active project | Confirmed: no `seedcheck` package on PyPI (404), no closely-related active GitHub project (a few small, unrelated repos share the name — Minecraft seed checkers, a crypto wallet seed-phrase tool — none in the Ubuntu/cloud-init space). `github.com/oum255/seedcheck` was free and has been created. |
| `ubuntu:26.04` available on the default remote, codename `resolute` | Confirmed. `lxc image info ubuntu:26.04` reports `release: resolute`, with aliases `26.04`, `r`, `resolute`, `lts`, `default`. |
| Python version shipped per release | Confirmed by running `python3 --version` inside a freshly launched container of each release: 22.04 → Python 3.10.12, 24.04 → Python 3.12.3, 26.04 → **Python 3.14.4** (the roadmap's assumption was correct). |
| cloud-init version shipped per release | 22.04 → `26.1-0ubuntu1~22.04.1`, 24.04 → `26.1-0ubuntu1~24.04.1`, 26.04 → `26.1-0ubuntu3~26.04.1`. |
| `cloud-init status --wait` works on all three releases | Confirmed: all three reached `status: done` after a plain launch with no user-data. |

Verified during Phase 1, 2026-10-13:

| Fact | Result |
|---|---|
| The package installs and its checks pass on the oldest supported Python | Confirmed inside a fresh `ubuntu:22.04` container (Python 3.10.12): `pip install -e ".[dev]"`, `seedcheck --version`, `ruff check .`, `mypy src` and `pytest` all succeed. |

Verified during Phase 2, 2026-10-13, against the host's cloud-init
`26.1-0ubuntu1~24.04.1`:

| Fact | Result |
|---|---|
| `cloud-init schema --config-file PATH` works without root | Yes, but when not run as root it prints `WARNING` log lines (it cannot read `/var/lib/cloud/instance/obj.pkl`) before its verdict. It exits 0 and prints `Valid schema PATH` on success, and exits 1 on failure. |
| Format of host schema errors | All schema errors arrive on one line, `Error: Cloud config schema errors: KEY: MESSAGE, KEY: MESSAGE`, followed by `Error: Invalid schema: user-data`. `userdata.py` keeps only the first kind of line, so log noise never reaches the user. The messages themselves contain commas, so they are not split further. |
| User-data headers cloud-init accepts | cloud-init's own error for a missing header lists `#!`, `## template: jinja`, `#cloud-boothook`, `#cloud-config`, `#cloud-config-archive`, `#cloud-config-jsonp`, `#include`, `#include-once` and `#part-handler`; `userdata.py` recognizes the same set (plus MIME multi-part). |

Verified during Phase 3, 2026-10-13, with LXD 5.21.8 LTS, by launching
each release twice: once with a deprecated key (`apt_update: true`, a
recoverable error) and once with a failing `runcmd` (a real error):

| Fact | Result |
|---|---|
| LXD accepts the `cloud-init.user-data` instance key | Yes, on all three releases; no fallback to `user.user-data` needed. |
| `cloud-init status --wait`, `--long`, `--format json` exist everywhere | Yes, on all three releases. |
| `cloud-init status` exit codes differ between releases | **Confirmed.** For the same deprecated key, 22.04 exits **0** with `status: done` and empty `recoverable_errors`, while 24.04 and 26.04 exit **2** with `extended_status: degraded done` and the deprecation under `recoverable_errors`. All three run cloud-init 26.1, so this is a per-release (SRU) behaviour, not a version difference. A real error exits 1 with `status: error` everywhere. The runner therefore ignores the exit code of `cloud-init status --wait` and the checks read the JSON instead. |
| JSON shape of `cloud-init status --long --format json` | Differs: 22.04 adds `_schema_version` and a `schemas` copy of every field; 24.04 and 26.04 do not. `errors` is a list of strings; `recoverable_errors` is a mapping of level (`WARNING`, `DEPRECATED`, ...) to a list of messages, and 26.04 can repeat the same message. |
| `cloud-init schema --system` exists and works as root | Yes, on all three (`lxc exec` runs as root). Exit 0 for valid user-data, also when it only has deprecations; exit 1 with `Error: Cloud config schema errors: ...` otherwise. |
| `lxc` messages Seedcheck relies on | A missing container gives `Error: ... Instance not found` (exit 1); `lxc exec` passes the command's exit code through unchanged; a missing image gives `Error: Failed instance creation: ... couldn't be found`. |

## `lxc launch` reads YAML from stdin when stdin is not a terminal

Observed during Phase 1: running `lxc launch` from a script fed to bash
on stdin made `lxc` consume the rest of the script as instance
configuration and fail with `yaml: line 13: mapping values are not
allowed in this context`. `lxc launch` (like `lxc init`) reads a YAML
config from stdin whenever stdin is not a terminal. `LxdClient._run()`
(phase 3) must therefore always give `lxc` an explicit stdin, either the
intended input or `subprocess.DEVNULL`, never inherit Seedcheck's own.

## Operational quirk: occasional `lxc launch` / `lxc init` hang on this dev VM

Observed during Phase 0's manual proof-of-life: `lxc launch`/`lxc init`
would occasionally hang indefinitely — the client process stayed alive
with zero CPU time, no operation ever appeared in `lxc operation list`,
and no network connection was ever opened — while at other times (even
for the same release, same cached image) it completed in under 10
seconds. This was not a slow download; `lxc image list ubuntu:` and raw
`curl` downloads were always fast.

Two things were changed when troubleshooting, and the hang has not
reproduced since, though the fix is not confirmed as the root cause
given the intermittent nature:
1. The VM had a non-functional IPv6 address (assigned via router
   advertisement, but `curl -6` to any external host failed). IPv6 was
   disabled (`sysctl net.ipv6.conf.all.disable_ipv6=1`).
2. The LXD snap daemon was restarted (`sudo snap restart lxd`).

This looks specific to this multipass/vmnet development VM's networking,
not to LXD or Ubuntu in general, and may not affect the GitHub Actions
runners used in CI (phase 8). Documented here because if it recurs
while running the real test matrix, the symptom (a release that never
progresses, with no error) should be treated as this known quirk, not as
a bug in Seedcheck's own timeout handling. `runner.py`'s per-release
timeout (phase 3) already bounds this: a release that never becomes
ready is reported as `ERROR` after the configured timeout, rather than
hanging Seedcheck itself.

## Static validation (`seedcheck validate`)

Validation runs in two layers before any container is launched, so a
mistake costs milliseconds instead of a three-release run:

1. `config.py` checks `seedcheck.yaml` itself. Unknown keys are refused
   (with a "did you mean" suggestion, since a typo such as `package:`
   would otherwise silently disable a whole category of checks), and
   every error names the exact key path, e.g. `checks.files[0].mode`.
   Release numbers and file modes must be quoted strings: YAML 1.1 reads
   an unquoted `0644` as the octal number 420, and an unquoted `24.10`
   as the float 24.1.
2. `userdata.py` checks the user-data file: readable UTF-8, the
   `#cloud-config` header (without it cloud-init silently ignores the
   file), and a YAML mapping body. If cloud-init is installed on the
   host, its `schema` command is run too. That host check is
   best-effort: the host's cloud-init may be older or newer than the
   one in each release, which is why every release also gets its own
   in-container `schema` check (phase 4).

## How container cleanup is guaranteed

Leaking containers would slowly fill the disk of whoever runs Seedcheck,
so cleanup is enforced at three levels:

1. `run_release()` deletes its container in a `finally` block, so success,
   failure, timeout and unexpected exceptions all end with a delete.
   It deletes even when the launch itself failed, since a failed launch
   can leave a stopped container behind.
2. `run_all()` keeps a list of containers that may still exist (a name
   is added *before* `lxc launch`, removed after a successful delete). On
   Ctrl+C it deletes whatever is left, then re-raises so the CLI exits 130.
   With `--keep` it leaves them and prints their names instead.
3. `LxdClient.delete()` treats "Instance not found" as success, so
   deleting twice is harmless, and retries while LXD reports the
   container as busy.

The third point comes from a bug found by testing Ctrl+C against a real
LXD: interrupting `lxc launch` kills the client, but the LXD server keeps
creating the container and refuses to delete it ("Instance is busy
running a create operation"), so the container leaked. `delete()` now
retries once a second, for up to five minutes (an interrupted launch may
still be downloading an image). A regression test covers it.

A delete that still fails is reported as a warning naming the exact
`lxc delete --force` command to run, rather than raised, so one stuck
container never hides the results of a run.

## Why `lxc` is wrapped in `LxdClient`

All calls to `lxc` go through `LxdClient._run()`. That one method always
passes an argument list (never a shell string), always sets stdin,
always applies a timeout, and turns "lxc not installed" and timeouts into
Seedcheck's own errors. The runner and the checks depend only on the small
`Lxd` protocol (`launch`, `wait_ready`, `exec`, `pull_file`, `delete`),
which lets the unit tests use `FakeLxdClient` and run in milliseconds,
without LXD, on any machine, including CI's Python matrix.

The user-data is passed as a single `cloud-init.user-data=TEXT`
argument. Linux limits a single argument to 128 KiB (`MAX_ARG_STRLEN`),
which is far above realistic cloud-config files.

## Checks (`checks.py`)

Each check runs one or two commands through `lxc exec` and turns their
output into a `CheckResult(name, status, detail, value)`. Values from
`seedcheck.yaml` are separate arguments, never part of a shell string;
only the `command` check runs `sh -c`, because running the user's shell
command is its whole purpose.

| Check | Command | Notes |
|---|---|---|
| schema | `cloud-init schema --system` | Exit 0 is PASS (deprecations still pass). The error lines are indented in this mode, unlike `--config-file`. |
| cloud-init | `cloud-init status --long --format json` | Exit code ignored. FAIL if `status` is `error` or `errors` is non-empty, or `status` is anything but `done`; WARN if only `recoverable_errors`; missing fields count as empty; duplicate messages are counted once. |
| package | `dpkg-query -W -f=${Status}\t${Version}\n NAME` | No shell, so `${Status}` reaches dpkg-query literally. One line per architecture; PASS if any is `install ok installed`, and the version is shown in the matrix. |
| service | `systemctl is-active NAME` | Only stdout matters (it exits non-zero for every state but active). |
| file | `stat -L -c %F\|%a\|%U PATH`, then `grep -q -F -- TEXT PATH` | `-L` follows symlinks. `%F` is `regular empty file` for an empty file. Modes are compared as octal numbers. Multi-line `contains` text is compared against `cat` output, since grep matches one line at a time. |
| user | `id -nG NAME` | |
| command | `sh -c RUN` | Exit code and stdout compared; a command that times out is a FAIL, not an ERROR. |

An exception inside one check (for example the container stopped
responding) becomes an ERROR cell for that check only. When a release
never reaches its checks (launch failure, timeout), every planned check
is reported as SKIP with the reason, so the matrix keeps its shape.

`detail` is collapsed to one line and cut at 200 characters, so a noisy
command cannot flood the report.

## Differences between releases

Observed while building Seedcheck (October 2026), on arm64:

| | 22.04 jammy | 24.04 noble | 26.04 resolute |
|---|---|---|---|
| Python | 3.10.12 | 3.12.3 | 3.14.4 |
| cloud-init | 26.1-0ubuntu1~22.04.1 | 26.1-0ubuntu1~24.04.1 | 26.1-0ubuntu3~26.04.1 |
| `cloud-init status` exit for a deprecated key | 0, `done`, no recoverable errors | 2, `degraded done` | 2, `degraded done` |
| `cloud-init status` JSON | extra `_schema_version` and `schemas` copy | | can repeat recoverable messages |
| nginx | 1.18.0-6ubuntu14.21 | 1.24.0-2ubuntu7.18 | 1.28.3-2ubuntu1.11 |
| curl | 7.81.0-1ubuntu1.29 | 8.5.0-2ubuntu10.15 | 8.18.0-1ubuntu2.7 |
| Ubuntu apt sources | `/etc/apt/sources.list` | deb822 `/etc/apt/sources.list.d/ubuntu.sources` | deb822 `ubuntu.sources` |
| `systemctl is-active ssh` right after boot | `active` | `inactive` (socket-activated via `ssh.socket` until first connection) | `active` |

The ssh row is a good example of why a service check should state the
state it expects per use case rather than assume `active`.

Sources added with cloud-init's `apt: sources:` were written as one-line
`.list` files in `/etc/apt/sources.list.d/` on all three releases (for
example `git-core.list`), even where Ubuntu's own sources use deb822.
The `apt-source` example still searches both locations rather than
assume either.

### Choosing the apt-source example

The first candidate, Grafana k6's repository (`dl.k6.io/deb stable`),
installs fine on amd64 but publishes no arm64 packages, so it failed on
the arm64 development VM even though the key and the source were
configured correctly. The example now uses the git-core Launchpad PPA,
which builds for amd64 and arm64 and for all three releases. Its source
line uses `$RELEASE`, which cloud-init replaces with each release's
codename, and its signing key fingerprint
(`F911AB184317630C59970973E363C90F8F1B6217`) was read from the
Launchpad API and is fetched from keyserver.ubuntu.com.

## Statuses, exit codes and reports

Each check is PASS, FAIL, WARN (passed with a caveat: cloud-init
recoverable errors), SKIP (never evaluated because the release failed
earlier) or ERROR (an infrastructure problem). A release takes the worst
status of its checks, or ERROR if it never got through them.

| Exit code | When |
|---|---|
| 0 | every check of every release is PASS or WARN |
| 1 | at least one check is FAIL, and nothing is ERROR |
| 2 | bad usage, invalid `seedcheck.yaml` or invalid user-data, found before any container starts |
| 3 | LXD is unusable, or anything ended in ERROR |
| 130 | interrupted with Ctrl+C (containers are still deleted) |

When a run has both a FAIL and an ERROR, the exit code is 3, not 1. An
ERROR means the results are incomplete, so CI should report "the test
infrastructure broke" rather than "the configuration is wrong". The
reports still show every FAIL.

`report.py` holds only pure functions of a `RunResult`, so the table,
Markdown and JSON are tested against exact expected strings. Rows keep
the order checks were first seen (schema, cloud-init, then the order of
`seedcheck.yaml`); a check missing for one release shows as SKIP. Color
is used only when stdout is a terminal and `NO_COLOR` is unset or empty
(https://no-color.org); it wraps the status word after padding, so the
columns stay aligned. Progress lines go to stderr, so stdout carries only
the table and can be redirected or piped. The Markdown report uses
status icons and escapes `|`, so it renders correctly in a GitHub job
summary.

## Edge cases and where they are tested

Every edge case in the roadmap has a defined behaviour and an end-to-end
test in `tests/unit/test_edge_cases.py`, which drives `seedcheck run`
the way a user would. The module tests cover the same rules in more
detail.

| Situation | Behaviour | Test |
|---|---|---|
| Config missing, unreadable or invalid YAML | exit 2, path and line number | `test_missing_config_file_exits_2_naming_the_path`, `test_invalid_yaml_config_exits_2_with_the_line_number` |
| Unknown key in `seedcheck.yaml` | exit 2 naming the key, with a suggestion | `test_unknown_key_typo_exits_2_naming_the_key` |
| `user_data` path does not exist | exit 2 with the resolved path | `test_missing_user_data_file_exits_2_showing_the_resolved_path` |
| No `#cloud-config` header | exit 2, explains cloud-init would ignore it | `test_missing_cloud_config_header_is_rejected_with_an_explanation` |
| Valid YAML, invalid schema | exit 2 from the host check; `schema` row FAIL per release | `test_schema_error_found_by_host_cloud_init_exits_2`, `test_schema_error_found_inside_a_release_fails_its_schema_row` |
| Unsupported or duplicate release | exit 2 listing supported releases | `test_unsupported_or_duplicate_release_exits_2_listing_supported_ones` |
| `lxc` missing, LXD not initialized, permission denied | exit 3 with advice for each | `test_unusable_lxd_exits_3_with_advice_for_each_cause` |
| Image cannot be downloaded | that release ERROR, others run, exit 3 | `test_image_that_cannot_be_downloaded_errors_only_that_release` |
| Container never ready / cloud-init timeout | ERROR, checks SKIP, logs kept, container deleted | `test_timeout_errors_the_release_skips_its_checks_keeps_logs_and_deletes_it` |
| cloud-init finishes with errors | `cloud-init` FAIL with the first error, other checks still run | `test_cloud_init_errors_fail_its_row_but_other_checks_still_run` |
| Only recoverable errors | `cloud-init` WARN, exit 0 | `test_recoverable_errors_only_warn_and_the_run_still_exits_0` |
| Older `cloud-init status` JSON | missing fields count as empty | `test_older_cloud_init_status_json_with_missing_fields_does_not_crash` |
| Package on one release only | FAIL in that column only | `test_package_missing_on_one_release_fails_only_that_column` |
| Same package, different versions | all PASS, each cell shows its version | `test_same_package_with_different_versions_passes_and_shows_each_version` |
| No network in the container | `cloud-init` FAIL with apt's failure | `test_no_network_in_the_container_shows_cloud_init_apt_failure` |
| File check on a missing path or a directory | FAIL "not found" / "not a regular file" | `test_file_check_on_a_missing_path_or_a_directory_fails` |
| Command with huge output | detail truncated | `test_command_with_huge_output_has_its_detail_truncated` |
| Ctrl+C | every container deleted, exit 130 | `test_ctrl_c_deletes_every_container_and_exits_130` (also tested by hand against a real LXD, see the cleanup section) |
| Unexpected exception in one release | container deleted, ERROR, others continue | `test_unexpected_exception_errors_one_release_and_the_others_continue` |
| Two runs at once | no name collision (random suffix) | `test_two_runs_at_the_same_time_never_reuse_a_container_name` |
| Output redirected, or `NO_COLOR` set | no color codes | `test_redirected_output_or_no_color_has_no_color_codes` |
| Empty `checks` section | only `schema` and `cloud-init` rows | `test_empty_checks_section_shows_only_schema_and_cloud_init` |

One thing writing these tests surfaced: `seedcheck.lxd` and
`seedcheck.userdata` both import the standard `shutil` and `subprocess`
modules, and in Python those are one shared object. Monkeypatching
`lxd.shutil.which` therefore also changes what `userdata` sees, so the
tests answer only for the specific command they fake.

## Debian packaging

Seedcheck is a native Debian package (`3.0 (native)`, version `0.1.0`
with no Debian revision), built with `dh` and pybuild's pyproject plugin,
which builds a wheel from `pyproject.toml` and unpacks it into the
package. It is `Architecture: all`: pure Python, so one `.deb` serves
amd64 and arm64. It is built on 24.04 and installs on all three releases
because it lands in `/usr/lib/python3/dist-packages`, which every
supported Python 3 searches, and dh_python3 turns its requirements into
`Depends: python3-yaml, python3:any`.

| File | Purpose |
|---|---|
| `debian/control` | Source and binary package metadata, build dependencies, runtime dependencies. `Recommends: snapd`, since LXD is a snap and cannot be a `Depends`. |
| `debian/rules` | The build recipe: `dh` with pybuild. Runs the unit tests during the build, excluding the integration tests (no LXD there). |
| `debian/changelog` | Package version history; its version must match `__version__` and the git tag. |
| `debian/copyright` | Machine-readable (DEP-5) license. Debian calls the MIT license `Expat`, since "MIT" names several different texts. |
| `debian/source/format` | `3.0 (native)`: the package is developed in this repository, so there is no separate upstream tarball. |
| `debian/seedcheck.1`, `debian/seedcheck.manpages` | The man page, so `man seedcheck` works. |
| `debian/seedcheck.examples` | Installs `examples/` to `/usr/share/doc/seedcheck/examples/`. |

`PYBUILD_NAME` is deliberately not set: it is meant for library packages
named `python3-*`, and setting it here would install files into a
`debian/python3-seedcheck/` directory that no package uses.

Verified during Phase 7, 2026-10-16:

| Fact | Result |
|---|---|
| `pybuild-plugin-pyproject` is available on the build machine | Yes on 24.04 (`6.20240401`). |
| pybuild runs the unit tests and honours `PYBUILD_TEST_ARGS` | Yes: the build log shows `python3.12 -m pytest -m "not integration"` and `287 passed, 5 deselected`. |
| A package built on 24.04 installs and runs on 22.04, 24.04 and 26.04 | Yes: `scripts/smoke-install.sh` passes on all three, with Python 3.10.12, 3.12.3 and 3.14.4. |
| lintian | No errors or warnings, and no overrides. The only warning on the first build, `no-manual-page`, was fixed by writing a man page rather than overridden. |

**A packaging bug lintian did not catch.** The unit tests read
`examples/`, which pybuild does not copy into its build directory, so
`debian/rules` copies it there before the tests. But pybuild installs
whatever is left in the build directory, so the first package shipped a
stray top-level `examples` module in `/usr/lib/python3/dist-packages`,
which could clash with any other package doing the same. It was found by
reading `dpkg -c`. `debian/rules` now removes the copy after the tests;
the examples ship in `/usr/share/doc/seedcheck/examples/` instead; and
`scripts/build-deb.sh` fails if anything but `seedcheck` lands in
`dist-packages`. That guard was checked by reintroducing the bug in a
scratch copy and watching the build fail.

On 26.04, `apt-get install` prints `Solving dependencies...`: 26.04 ships
apt 3 with its new solver. It made no difference to installing the
package.
