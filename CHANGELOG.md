# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-16

First release.

### Added

- `seedcheck run CONFIG`: launches a cloud-init user-data file in one LXD
  container per Ubuntu release (22.04, 24.04 and 26.04 by default), waits
  for cloud-init, runs the configured checks in each container, prints a
  pass/fail matrix, and always deletes its containers. Options:
  `--release`, `--markdown`, `--json`, `--artifacts-dir`, `--timeout`,
  `--keep` and `--verbose`.
- `seedcheck validate CONFIG`: strict static validation of `seedcheck.yaml`
  and of the user-data (`#cloud-config` header, YAML shape, and the host's
  `cloud-init schema` when cloud-init is installed), with errors that name
  the exact key. Does not need LXD.
- `seedcheck releases` and `seedcheck --version`.
- Checks run inside each container: the release's own `cloud-init schema
  --system`, cloud-init's error report (recoverable errors give WARN),
  installed packages with their versions, service states, files (existence,
  mode, owner, content), users and groups, and arbitrary shell commands.
- Reports as a terminal table (colored on a terminal, honouring
  `NO_COLOR`), Markdown for GitHub job summaries, and JSON.
- Exit codes for CI: 0 passed, 1 a check failed, 2 invalid input, 3
  infrastructure error, 130 interrupted.
- Examples: `nginx`, `apt-source` (a Launchpad PPA added with cloud-init's
  `apt:` module) and `broken` (fails on purpose).
- Debian package (`seedcheck_0.1.0_all.deb`) with a man page, built with
  pybuild, lintian-clean, installable on 22.04, 24.04 and 26.04.
- GitHub Actions CI that lints, runs the unit tests on Python 3.10, 3.12
  and 3.14, builds the `.deb`, installs it in a fresh container of each
  release, and runs the full matrix on every push; and a release workflow
  that publishes the `.deb` when a `v*` tag is pushed.

### Fixed

Found and fixed during development, before this first release:

- Pressing Ctrl+C while a container was being launched could leave the
  container behind: LXD keeps creating it server-side and refuses to delete
  a "busy" instance. Deletion now waits for that operation to end.
  (Found by testing interruption against a real LXD; covered by
  `test_delete_waits_for_an_interrupted_launch_to_finish`.)
- The Debian package installed the examples as a stray top-level
  `examples` module in `/usr/lib/python3/dist-packages`, because pybuild
  installs whatever the unit tests leave in its build directory. They are
  now removed after the tests and shipped in
  `/usr/share/doc/seedcheck/examples/` instead, and `scripts/build-deb.sh`
  fails if anything but `seedcheck` lands in `dist-packages`. (Found by
  reading `dpkg -c`; lintian did not report it.)

[Unreleased]: https://github.com/oum255/seedcheck/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/oum255/seedcheck/releases/tag/v0.1.0
