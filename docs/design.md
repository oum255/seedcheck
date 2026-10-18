# Design notes

How Seedcheck works, why it's built this way, and the things I found out
along the way by actually running it, not by assuming.

- [How the code is split](#how-the-code-is-split)
- [Why every `lxc` call goes through one class](#why-every-lxc-call-goes-through-one-class)
- [Making sure containers always get deleted](#making-sure-containers-always-get-deleted)
- [Validation](#validation)
- [Checks](#checks)
- [Statuses, exit codes and reports](#statuses-exit-codes-and-reports)
- [Differences between releases](#differences-between-releases)
- [What cloud-init and LXD actually do](#what-cloud-init-and-lxd-actually-do)
- [Edge cases and their tests](#edge-cases-and-their-tests)
- [Debian package](#debian-package)
- [CI and releases](#ci-and-releases)
- [Testing on a fresh machine](#testing-on-a-fresh-machine)
- [My dev setup](#my-dev-setup)

## How the code is split

Config goes in, containers run in the middle, reports come out. Each
module does one thing.

| Module | What it does |
|---|---|
| `cli.py` | Parses the arguments, runs the steps in order (config, user-data, LXD check, run, report) and turns each kind of failure into an exit code. It's the only place that prints the table. |
| `config.py` | Reads `seedcheck.yaml` into frozen dataclasses and rejects anything wrong with a `ConfigError` that names the key. |
| `userdata.py` | Checks the user-data file: encoding, `#cloud-config` header, YAML shape, and the host's `cloud-init schema` if there is one. Returns a list of problems instead of raising, so you see all of them at once. |
| `releases.py` | The list of supported releases. Adding one is one line here. |
| `lxd.py` | `LxdClient`, the only code that runs `lxc`, and the `Lxd` protocol the rest of the code uses. |
| `runner.py` | Runs one release from start to finish (launch, wait, check, get logs, delete), then all of them. Turns infra failures into ERROR and makes sure cleanup happens. |
| `checks.py` | One function per check type, plus `run_checks()` to run them in order. |
| `report.py` | Pure functions: `RunResult` in, table / Markdown / JSON / exit code out. |
| `models.py` | The result types: `Status`, `CheckResult`, `ReleaseResult`, `RunResult`. |
| `errors.py` | `SeedcheckError` and its subclasses, so the CLI maps errors to exit codes in one place. |

The only runtime dependency is PyYAML. Everything else (arguments, the
table, JSON, subprocesses) is standard library, so the Debian package only
needs `python3-yaml`.

## Why every `lxc` call goes through one class

All `lxc` calls go through `LxdClient._run()`. That one method always uses
an argument list (never a shell string), always sets stdin, always has a
timeout, and turns "lxc isn't installed" and timeouts into Seedcheck
errors.

The runner and the checks only know about a small `Lxd` protocol
(`launch`, `wait_ready`, `exec`, `pull_file`, `delete`). That's what lets
the unit tests use a `FakeLxdClient`: they run in milliseconds, with no
LXD, on any machine, including the CI Python matrix.

The stdin part matters more than it looks. I found out the hard way that
`lxc launch` reads a YAML config from stdin whenever stdin isn't a
terminal: I ran a script through `bash` on stdin and `lxc` swallowed the
rest of the script, then failed with `yaml: line 13: mapping values are
not allowed in this context`. So `_run()` always passes either the input
it means to send or `/dev/null`.

The user-data goes in as a single `cloud-init.user-data=TEXT` argument.
Linux caps one argument at 128 KiB, which is way more than a real
cloud-config needs.

## Making sure containers always get deleted

Leftover containers would slowly fill up the disk, so cleanup happens at
three levels:

1. `run_release()` deletes its container in a `finally` block, so success,
   failure, timeout and crashes all end with a delete. It deletes even if
   the launch failed, because a failed launch can leave a stopped
   container around.
2. `run_all()` keeps a list of containers that might still exist. A name
   goes in *before* `lxc launch` and comes out after a successful delete.
   On Ctrl+C it deletes whatever's left and re-raises, so the CLI exits
   with 130. With `--keep` it leaves them and prints their names.
3. `LxdClient.delete()` treats "Instance not found" as fine, so deleting
   twice doesn't hurt, and it retries while LXD says the container is
   busy.

That last one comes from a real bug. I tested Ctrl+C against a real LXD
and a container was left behind: killing `lxc launch` stops the client,
but the LXD server keeps creating the container and refuses to delete it
("Instance is busy running a create operation"). Now `delete()` retries
every second for up to five minutes (an interrupted launch might still be
downloading an image), and there's a regression test for it.

If a delete still fails, Seedcheck prints a warning with the exact
`lxc delete --force` command to run instead of crashing, so one stuck
container doesn't hide the results.

## Validation

There are two layers, both before anything is launched, so a mistake
costs milliseconds instead of a three-release run:

1. `config.py` checks `seedcheck.yaml`. Unknown keys are an error with a
   "did you mean" hint, because a typo like `package:` would otherwise
   quietly turn off a whole set of checks. Every error names the exact key,
   like `checks.files[0].mode`. Release numbers and file modes have to be
   quoted: YAML 1.1 reads an unquoted `0644` as the octal number 420 and
   `24.10` as 24.1.
2. `userdata.py` checks the user-data: valid UTF-8, the `#cloud-config`
   header (without it, cloud-init ignores the file silently), and a YAML
   mapping. If cloud-init is installed on the host, `cloud-init schema
   --config-file` runs too. That's only a quick first pass: the host's
   cloud-init can be older or newer than a release's, so each release also
   gets its own `schema` check inside the container.

Run as a normal user, `cloud-init schema` prints `WARNING` log lines (it
can't read `/var/lib/cloud/instance/obj.pkl`) before its answer, and all
the errors come on one line: `Error: Cloud config schema errors: KEY:
MESSAGE, KEY: MESSAGE`. Seedcheck keeps only those lines. The messages
have commas in them, so I don't split them further.

The list of other user-data formats Seedcheck accepts (`#!`,
`#include`, `#cloud-boothook`...) is the same list cloud-init itself
prints when the header is missing, plus MIME multi-part.

## Checks

Each check runs one or two commands with `lxc exec` and turns the output
into a `CheckResult(name, status, detail, value)`. Values from
`seedcheck.yaml` are separate arguments, never part of a shell string.
Only the `command` check uses `sh -c`, since running the user's shell
command is the whole point.

| Check | Command | Notes |
|---|---|---|
| schema | `cloud-init schema --system` | Exit 0 is PASS, deprecations included. In this mode the error lines are indented. |
| cloud-init | `cloud-init status --long --format json` | Exit code ignored (see below). FAIL if `status` is `error`, `errors` isn't empty, or `status` isn't `done`. WARN if there are only `recoverable_errors`. Missing fields count as empty, and repeated messages count once. |
| package | `dpkg-query -W -f=${Status}\t${Version}\n NAME` | No shell, so dpkg-query gets `${Status}` as is. One line per architecture; PASS if any is `install ok installed`, and the version shows in the table. |
| service | `systemctl is-active NAME` | Only the output matters, the exit code is non-zero for anything but active. |
| file | `stat -L -c %F\|%a\|%U PATH`, then `grep -q -F -- TEXT PATH` | `-L` follows symlinks. An empty file shows up as `regular empty file`. Modes are compared as octal numbers. Multi-line `contains` is compared on the whole file, since grep works line by line. |
| user | `id -nG NAME` | |
| command | `sh -c RUN` | Compares exit code and stdout. A command that times out is a FAIL, not an ERROR: it's the command that's slow, not the infrastructure. |

If one check hits an infra problem (say the container stops answering),
only that cell is ERROR. If a release never gets to its checks (launch
failed, timeout), every check shows as SKIP with the reason, so the table
keeps its shape.

`detail` is squashed to one line and cut at 200 characters, so a noisy
command can't flood the report.

## Statuses, exit codes and reports

A check is PASS, FAIL, WARN (passed, but cloud-init reported something
recoverable), SKIP (never ran because the release failed earlier) or
ERROR (infrastructure). A release gets the worst status of its checks, or
ERROR if it never got through them.

| Exit code | When |
|---|---|
| 0 | every check on every release is PASS or WARN |
| 1 | at least one FAIL, and no ERROR |
| 2 | bad arguments, `seedcheck.yaml` or user-data, before anything starts |
| 3 | LXD unusable, or anything ended in ERROR |
| 130 | Ctrl+C (containers still deleted) |

If a run has both a FAIL and an ERROR, it exits with 3, not 1. An ERROR
means the results are incomplete, so CI should say "the test setup broke"
rather than "your config is wrong". The reports still show every FAIL.

`report.py` is all pure functions, so the table, Markdown and JSON are
tested against exact strings. Rows keep the order checks first appear in
(schema, cloud-init, then `seedcheck.yaml` order), and a check missing
for one release shows as SKIP. Color is only used when stdout is a
terminal and `NO_COLOR` is empty or unset (https://no-color.org). The
color codes are added after padding, otherwise they'd throw off the column
widths. Progress goes to stderr, so stdout is just the table. The
Markdown version uses icons and escapes `|` so it renders properly in a
GitHub job summary.

## Differences between releases

What I saw while building this (October 2026, on arm64):

| | 22.04 jammy | 24.04 noble | 26.04 resolute |
|---|---|---|---|
| Python | 3.10.12 | 3.12.3 | 3.14.4 |
| cloud-init | 26.1-0ubuntu1~22.04.1 | 26.1-0ubuntu1~24.04.1 | 26.1-0ubuntu3~26.04.1 |
| `cloud-init status` exit for a deprecated key | 0, `done`, no recoverable errors | 2, `degraded done` | 2, `degraded done` |
| `cloud-init status` JSON | extra `_schema_version` and a `schemas` copy | | can repeat recoverable messages |
| nginx | 1.18.0-6ubuntu14.21 | 1.24.0-2ubuntu7.18 | 1.28.3-2ubuntu1.11 |
| curl | 7.81.0-1ubuntu1.29 | 8.5.0-2ubuntu10.15 | 8.18.0-1ubuntu2.7 |
| Ubuntu apt sources | `/etc/apt/sources.list` | deb822 `/etc/apt/sources.list.d/ubuntu.sources` | deb822 `ubuntu.sources` |
| `systemctl is-active ssh` right after boot | `active` | `inactive` (socket-activated through `ssh.socket` until someone connects) | `active` |

The exit code row surprised me the most. All three run cloud-init 26.1,
but the same deprecated key gives exit 0 on 22.04 and exit 2 on the
others, so it's a per-release (SRU) choice, not a version thing. That's
why the runner ignores the exit code of `cloud-init status --wait` and the
cloud-init check reads the JSON instead.

The ssh row is a good reason to write the expected state of a service
explicitly instead of always assuming `active`.

Sources added with cloud-init's `apt: sources:` were written as one-line
`.list` files in `/etc/apt/sources.list.d/` on all three releases (like
`git-core.list`), even where Ubuntu's own sources are deb822. The
`apt-source` example still looks in both places.

On the amd64 CI runners, package versions were sometimes a bit newer than
on my arm64 VM a few days earlier (curl `7.81.0-1ubuntu1.30` vs `.29` on
22.04). That's why the table prints versions instead of expecting fixed
ones.

### Picking the apt-source example

My first try was Grafana's k6 repository (`dl.k6.io/deb stable`). It
works on amd64, but it has no arm64 packages, so it failed on my arm64 VM
even with the key and source set up correctly. I switched to the git-core
Launchpad PPA, which builds for amd64 and arm64 on all three releases. Its
source line uses `$RELEASE`, which cloud-init replaces with each release's
codename. I got its signing key fingerprint
(`F911AB184317630C59970973E363C90F8F1B6217`) from the Launchpad API, and
cloud-init fetches the key from keyserver.ubuntu.com.

## What cloud-init and LXD actually do

Things I checked by running them, with LXD 5.21.8 LTS, launching each
release once with a deprecated key and once with a failing `runcmd`:

| Question | Answer |
|---|---|
| Does LXD accept the `cloud-init.user-data` key? | Yes, on all three. No need for the older `user.user-data`. |
| Do `cloud-init status --wait`, `--long` and `--format json` exist everywhere? | Yes. |
| Does `cloud-init schema --system` work? | Yes on all three (`lxc exec` runs as root). Exit 0 for valid user-data, even with deprecations, and exit 1 with `Error: Cloud config schema errors: ...` otherwise. |
| What does the status JSON look like? | `errors` is a list of strings; `recoverable_errors` is a dict of level (`WARNING`, `DEPRECATED`, ...) to a list of messages. |
| What `lxc` messages does Seedcheck rely on? | A missing container gives `Error: ... Instance not found` (exit 1). `lxc exec` passes the command's exit code through. A missing image gives `Error: Failed instance creation: ... couldn't be found`. |
| Does the package work on Python 3.10? | Yes, tested in a fresh 22.04 container (3.10.12): install, ruff, mypy and pytest all pass. |

## Edge cases and their tests

Every edge case I could think of has a defined behaviour and an
end-to-end test in `tests/unit/test_edge_cases.py`, which runs
`seedcheck run` the way a user would. The module tests go into more
detail.

| Situation | What happens | Test |
|---|---|---|
| Config missing, unreadable or invalid YAML | exit 2, path and line number | `test_missing_config_file_exits_2_naming_the_path`, `test_invalid_yaml_config_exits_2_with_the_line_number` |
| Unknown key in `seedcheck.yaml` | exit 2 naming the key, with a suggestion | `test_unknown_key_typo_exits_2_naming_the_key` |
| `user_data` path doesn't exist | exit 2 with the full path | `test_missing_user_data_file_exits_2_showing_the_resolved_path` |
| No `#cloud-config` header | exit 2, says cloud-init would ignore it | `test_missing_cloud_config_header_is_rejected_with_an_explanation` |
| Valid YAML but invalid schema | exit 2 from the host check; `schema` row FAIL per release | `test_schema_error_found_by_host_cloud_init_exits_2`, `test_schema_error_found_inside_a_release_fails_its_schema_row` |
| Unsupported or duplicate release | exit 2 listing the supported ones | `test_unsupported_or_duplicate_release_exits_2_listing_supported_ones` |
| `lxc` missing, LXD not set up, permission denied | exit 3 with a hint for each | `test_unusable_lxd_exits_3_with_advice_for_each_cause` |
| Image can't be downloaded | that release ERROR, the others run, exit 3 | `test_image_that_cannot_be_downloaded_errors_only_that_release` |
| Container never ready / cloud-init timeout | ERROR, checks SKIP, logs kept, container deleted | `test_timeout_errors_the_release_skips_its_checks_keeps_logs_and_deletes_it` |
| cloud-init finishes with errors | `cloud-init` FAIL with the first error, other checks still run | `test_cloud_init_errors_fail_its_row_but_other_checks_still_run` |
| Only recoverable errors | `cloud-init` WARN, exit 0 | `test_recoverable_errors_only_warn_and_the_run_still_exits_0` |
| Older `cloud-init status` JSON | missing fields count as empty | `test_older_cloud_init_status_json_with_missing_fields_does_not_crash` |
| Package on one release only | FAIL in that column only | `test_package_missing_on_one_release_fails_only_that_column` |
| Same package, different versions | all PASS, each cell shows its version | `test_same_package_with_different_versions_passes_and_shows_each_version` |
| No network in the container | `cloud-init` FAIL with apt's error | `test_no_network_in_the_container_shows_cloud_init_apt_failure` |
| File check on a missing path or a directory | FAIL "not found" / "not a regular file" | `test_file_check_on_a_missing_path_or_a_directory_fails` |
| Command with a huge output | detail cut short | `test_command_with_huge_output_has_its_detail_truncated` |
| Ctrl+C | all containers deleted, exit 130 | `test_ctrl_c_deletes_every_container_and_exits_130` (also tried by hand on a real LXD, see the cleanup section) |
| Unexpected exception on one release | container deleted, ERROR, the others continue | `test_unexpected_exception_errors_one_release_and_the_others_continue` |
| Two runs at once | no name clash (random suffix) | `test_two_runs_at_the_same_time_never_reuse_a_container_name` |
| Output redirected, or `NO_COLOR` set | no color codes | `test_redirected_output_or_no_color_has_no_color_codes` |
| Empty `checks` section | only `schema` and `cloud-init` rows | `test_empty_checks_section_shows_only_schema_and_cloud_init` |

One thing that tripped me up while writing these: `seedcheck.lxd` and
`seedcheck.userdata` both import `shutil` and `subprocess`, and in Python
that's the same module object. Monkeypatching `lxd.shutil.which` also
changes what `userdata` sees, so the fakes only answer for the command
they're faking.

## Debian package

Seedcheck is a native Debian package (`3.0 (native)`, version `0.1.0`, no
Debian revision), built with `dh` and pybuild's pyproject plugin, which
builds a wheel from `pyproject.toml` and unpacks it into the package. It's
`Architecture: all` because it's pure Python, so one `.deb` covers amd64
and arm64. I build it on 24.04 and it installs on all three releases,
since it goes into `/usr/lib/python3/dist-packages`, which every Python 3
there looks in. dh_python3 turns the requirements into `Depends:
python3-yaml, python3:any`.

| File | What it's for |
|---|---|
| `debian/control` | Package metadata and dependencies. `Recommends: snapd`, because LXD is a snap and can't be a `Depends`. |
| `debian/rules` | The build recipe: `dh` with pybuild. Runs the unit tests during the build, minus the integration tests (no LXD there). |
| `debian/changelog` | Version history. Its version has to match `__version__` and the git tag. |
| `debian/copyright` | Machine-readable (DEP-5) copyright file. The code is all rights reserved, so the license is `proprietary`. |
| `debian/source/format` | `3.0 (native)`: the package lives in this repo, there's no separate upstream tarball. |
| `debian/seedcheck.1`, `debian/seedcheck.manpages` | The man page, so `man seedcheck` works. |
| `debian/seedcheck.examples` | Puts `examples/` in `/usr/share/doc/seedcheck/examples/`. |

I don't set `PYBUILD_NAME`: it's meant for library packages called
`python3-*`, and here it would install files into a
`debian/python3-seedcheck/` directory that no package uses.

`pybuild-plugin-pyproject` exists on 24.04 (`6.20240401`) but not on
22.04, so that's where the package gets built. The build log shows
pybuild running `python3.12 -m pytest -m "not integration"` (`287
passed, 5 deselected`), so `PYBUILD_TEST_ARGS` is respected. lintian has
nothing to say and there are no overrides. The only warning on the first
build was `no-manual-page`, and I wrote a man page rather than silencing
it.

**A bug lintian didn't catch.** The unit tests need `examples/`, which
pybuild doesn't copy into its build directory, so `debian/rules` copies
it in before the tests. But pybuild installs whatever is left in that
directory, so the first package had a stray `examples` module in
`/usr/lib/python3/dist-packages` that could clash with any other package
doing the same. I spotted it by reading `dpkg -c`. Now `debian/rules`
removes the copy after the tests, the examples go into
`/usr/share/doc/seedcheck/examples/` instead, and `scripts/build-deb.sh`
fails if anything other than `seedcheck` lands in `dist-packages`. I put
the bug back in a scratch copy to make sure that check really fails the
build.

On 26.04, `apt-get install` prints `Solving dependencies...`: that's
apt 3 and its new solver. It made no difference for installing the
package.

## CI and releases

`.github/workflows/ci.yml` runs on every push and pull request, on
`ubuntu-24.04` runners:

| Job | What it checks |
|---|---|
| `lint` | ruff (lint and format), mypy strict, shellcheck on every script. |
| `unit` | Unit tests with at least 90% coverage, on Python 3.10, 3.12 and 3.14 (the versions of the three releases). |
| `build-deb` | `scripts/build-deb.sh` builds a lintian-clean package and uploads the `.deb`. |
| `install-test` | For each release, installs the `.deb` with apt in a fresh LXD container and runs it (`scripts/smoke-install.sh`). |
| `matrix` | Installs the `.deb` on the runner, runs the nginx example on all three releases and adds the Markdown table to the job summary. The broken example has to exit 1. Reports and cloud-init logs are uploaded. |

`concurrency` cancels a run when a newer push comes in on the same
branch, and every job has `timeout-minutes`, so a stuck container can't
hold a runner for hours.

LXD comes from `canonical/setup-lxd`, pinned to a commit SHA (the `v1`
tag can move) and to the `5.21/stable` snap channel instead of its
default `latest/candidate`. Before using it I read its `action.yml`: it
gives the `adm` group (which the runner user is already in) access to the
LXD socket, so no `sudo` or re-login is needed, and it adds ACCEPT rules
for `lxdbr0` to Docker's `DOCKER-USER` chain. That second part matters:
Docker sets the `FORWARD` policy to DROP on GitHub runners, which would
otherwise cut containers off the network. The first CI run passed with
networked `apt` installs on all three releases, and `setup-python` had
3.10.22, 3.12.15 and 3.14.8.

`.github/workflows/release.yml` runs when a `v*` tag is pushed.
`scripts/check-release.sh` stops unless the tag, `__version__` and
`debian/changelog` all say the same version and `CHANGELOG.md` has a
section for it. That section becomes the release notes, and the workflow
builds the `.deb` and attaches it to a new GitHub release.

## Testing on a fresh machine

On 2026-10-17 I ran everything again from a fresh clone, following the
README word for word, on a brand-new multipass VM with Ubuntu 22.04 (the
oldest supported release, so Python 3.10 natively), 2 vCPUs and 3 GB of
RAM.

| Step | Result |
|---|---|
| `git clone`, `python3 -m venv`, `pip install -e ".[dev]"` | **Failed at first**: a fresh Ubuntu doesn't have `python3-venv`. The README installs it now. After that it worked with Python 3.10.12 and the system pip 22.0.2. |
| ruff, ruff format, mypy, shellcheck | Clean. |
| `pytest --cov=seedcheck` | 287 passed, 99.75% coverage. |
| `seedcheck validate` | 0 for the three examples, 2 for each broken file. |
| `seedcheck run` nginx / apt-source / broken | All PASS (exit 0) / all PASS (exit 0) / FAIL with reasons (exit 1). The first run took 11 minutes, mostly downloading images. |
| Ctrl+C during a run | Exit 130, no container left. |
| `pytest -m integration` | 5 passed in 7 min 40 s, no container left. |
| `scripts/build-deb.sh` (in a 24.04 container, since the plugin needs 24.04) | Built, 287 unit tests passed during the build, nothing stray in `dist-packages`. |
| `scripts/smoke-install.sh` on 22.04, 24.04, 26.04 | All passed. |

Two things I learned from it:

- **LXD 5.0 works.** Ubuntu 22.04 images come with the LXD 5.0 snap
  (5.0.10), older than the 5.21 I'd been using. Everything Seedcheck needs,
  including the `cloud-init.user-data` key, works with it.
- **lintian inside a container can complain for no reason.** In an arm64
  24.04 container inside that VM, lintian reported `groff-message ...
  troff: Segmentation fault` on the man page. It said the same thing about
  Ubuntu's own `dpkg` package in the same container, and running lintian's
  exact groff command by hand on the page worked fine. So it's the
  environment, not the man page. On full VMs (CI and my dev VM) the same
  `.deb` is lintian-clean.

## My dev setup

I work on an Apple Silicon Mac, so LXD runs in an Ubuntu 24.04 multipass
VM (2 vCPUs, 3 GB RAM, 25 GB disk), since it needs a Linux kernel.
`ubuntu:` always means LXD's default image remote,
`https://cloud-images.ubuntu.com/releases/`.

Every so often, `lxc launch` in that VM would hang forever: the process
sat at zero CPU, nothing showed up in `lxc operation list` and no network
connection was opened, while the next try with the same cached image
finished in under 10 seconds. It wasn't a slow download. Two things
changed while I was looking into it and it hasn't happened since,
though I can't say for sure which one fixed it:

1. The VM had a broken IPv6 address (from router advertisement, but
   `curl -6` to anything failed), so I turned IPv6 off
   (`sysctl net.ipv6.conf.all.disable_ipv6=1`).
2. I restarted the LXD snap (`sudo snap restart lxd`).

It looks specific to multipass networking on my Mac, not to LXD or
Ubuntu. If it ever comes back, a release that never moves forward without
any error is this, not a Seedcheck bug, and the per-release timeout still
stops it with an ERROR instead of hanging forever.
