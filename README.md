# Seedcheck

[![CI](https://github.com/oum255/seedcheck/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/oum255/seedcheck/actions/workflows/ci.yml)

Seedcheck is a command-line tool that answers one question: *does this
cloud-init configuration work on every Ubuntu release I care about?* Given a
cloud-init user-data file and a list of expectations, it validates the file,
launches one LXD container per Ubuntu release (22.04, 24.04, 26.04), waits for
cloud-init to finish, runs checks inside each container (packages, services,
files, users, commands and cloud-init's own error report), and prints a
pass/fail matrix per release. Work in progress.

## Requirements

Seedcheck runs on Ubuntu 22.04 or newer and needs LXD. Ubuntu ships LXD as
a snap, so the Debian package cannot depend on it; install it first:

```bash
sudo snap install lxd
sudo lxd init --auto
sudo usermod -aG lxd "$USER"   # then log out and back in
```

`scripts/setup-lxd.sh` does the same. If LXD is missing or unusable,
`seedcheck run` stops with exit code 3 and says which step is missing.

## Tests

Measured on 2026-10-16 (macOS with Python 3.13, and again on Ubuntu 22.04
with Python 3.10):

- **287 unit tests**, run without LXD in under a second, with **99.75%
  coverage** (statements and branches; CI fails below 90%).
- **5 integration tests** against a real LXD, which run every example on
  Ubuntu 22.04, 24.04 and 26.04 and check that no container is left behind
  (about 8 minutes).

```bash
pip install -e ".[dev]"
pytest --cov=seedcheck            # unit tests and coverage
pytest -m integration             # needs LXD
```
