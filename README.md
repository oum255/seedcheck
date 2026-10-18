# Seedcheck

[![CI](https://github.com/oum255/seedcheck/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/oum255/seedcheck/actions/workflows/ci.yml)

*[Version française](README.fr.md)*

Seedcheck runs your cloud-init config on several Ubuntu releases at once, in
LXD containers, and tells you where it works and where it doesn't.

```text
$ seedcheck run examples/nginx/seedcheck.yaml
Seedcheck 0.1.0   user-data: examples/nginx/user-data.yaml

Check                               22.04                        24.04                       26.04
schema                              PASS                         PASS                        PASS
cloud-init                          PASS                         PASS                        PASS
package: nginx                      PASS (1.18.0-6ubuntu14.21)   PASS (1.24.0-2ubuntu7.18)   PASS (1.28.3-2ubuntu1.11)
package: curl                       PASS (7.81.0-1ubuntu1.29)    PASS (8.5.0-2ubuntu10.15)   PASS (8.18.0-1ubuntu2.7)
service: nginx                      PASS                         PASS                        PASS
file: /etc/motd                     PASS                         PASS                        PASS
user: deploy                        PASS                         PASS                        PASS
command: nginx answers on port 80   PASS                         PASS                        PASS

Result: 3 of 3 releases passed (24 checks, 0 failed, 0 warnings)
```

When something's broken, you see where and why (`examples/broken`):

```text
Check                                  22.04                       24.04                       26.04
package: this-package-does-not-exist   FAIL                        FAIL                        FAIL
file: /etc/seedcheck-demo.conf         FAIL                        FAIL                        FAIL
command: python3 is 3.12               FAIL                        PASS                        FAIL

Failures:
  [22.04] package: this-package-does-not-exist: not installed
  [22.04] file: /etc/seedcheck-demo.conf: expected mode 0644, found 0600
  [22.04] command: python3 is 3.12: stdout does not contain 'Python 3.12'; output: Python 3.10.12
  ...
```

Both of these are real runs. CI posts the same table on every push.

## Why I built it

A cloud-init file that works on one Ubuntu release can break on the next
one. Package versions change, a service becomes socket-activated, apt moves
to a new sources format, cloud-init returns a different exit code for the
same warning. I ran into all of these between 22.04, 24.04 and 26.04 while
building this (the list is in
[docs/design.md](docs/design.md#differences-between-releases)).

Testing that by hand means starting a VM per release and poking around. I
wanted one command that does it for me and gives a clear yes or no per
release.

## What you need

- Ubuntu 22.04, 24.04 or 26.04: on bare metal, in a VM, or in WSL2 with
  systemd.
- LXD 5.0 or newer. I've tested 5.0.10 (what Ubuntu 22.04 images come
  with) and 5.21.8 LTS. It's a snap, so the `.deb` can't pull it in for
  you:

  ```bash
  sudo snap install lxd
  sudo lxd init --auto
  sudo usermod -aG lxd "$USER"   # then log out and back in
  ```

  Or just run [`scripts/setup-lxd.sh`](scripts/setup-lxd.sh). If LXD isn't
  ready, `seedcheck run` stops with exit code 3 and tells you what's
  missing.
- Network access from the containers, to get images and packages.

## Install

From the `.deb` on the [releases page](https://github.com/oum255/seedcheck/releases/latest):

```bash
wget https://github.com/oum255/seedcheck/releases/download/v0.1.0/seedcheck_0.1.0_all.deb
sudo apt install ./seedcheck_0.1.0_all.deb
seedcheck --version
```

It's plain Python, so one package works on 22.04, 24.04 and 26.04, amd64
and arm64. The only extra dependency is `python3-yaml`. The examples end up
in `/usr/share/doc/seedcheck/examples/`, and there's a man page
(`man seedcheck`).

From source (Python 3.10+; on Ubuntu, `venv` comes as a separate package):

```bash
sudo apt install git python3-venv
git clone https://github.com/oum255/seedcheck.git
cd seedcheck
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

## Try it

Once it's installed, from a clone of this repo (that's where the examples
are):

```bash
cd seedcheck

# Check the config without launching anything. No LXD needed.
seedcheck validate examples/nginx/seedcheck.yaml

# Try one release first, it takes about a minute.
seedcheck run examples/nginx/seedcheck.yaml --release 24.04

# Then all three, with reports.
seedcheck run examples/nginx/seedcheck.yaml --markdown report.md --json report.json
echo "exit code: $?"
```

The [nginx user-data](examples/nginx/user-data.yaml) installs nginx and
curl, adds a `deploy` user, writes `/etc/motd` and starts nginx, and
[`seedcheck.yaml`](examples/nginx/seedcheck.yaml) checks each of those.
There are two other examples: [`apt-source`](examples/apt-source/) adds a
Launchpad PPA with cloud-init's `apt:` module and checks git really came
from it, and [`broken`](examples/broken/) fails on purpose.

## Writing a `seedcheck.yaml`

```yaml
user_data: user-data.yaml        # required, relative to this file
releases: ["22.04", "24.04", "26.04"]  # optional, number or codename (this is the default)
timeout: 600                     # optional, seconds to wait per release

checks:                          # optional, schema and cloud-init are always checked
  packages:                      # must be installed; the version shows in the table
    - nginx
    - name: curl                 # same thing as "- curl"
  services:                      # must be in this state (systemctl is-active)
    - nginx                      # active by default
    - name: nginx.service        # the .service part is optional
      state: active
  files:                         # must exist and be a regular file
    - path: /etc/motd
      contains: "Provisioned by cloud-init"  # optional, plain text, can be several lines
      mode: "0644"               # optional, in quotes
      owner: root                # optional
  users:                         # must exist
    - name: deploy
      groups: [sudo]             # optional, must be in each one
  commands:                      # runs with sh -c in the container
    - name: nginx answers on port 80
      run: curl -fsS http://localhost
      expect_exit: 0             # optional, 0 by default
      expect_stdout_contains: nginx  # optional
```

Validation is strict on purpose, so mistakes show up before anything
starts. A typo like `package:` gets you `checks.package: unknown key ...
did you mean 'packages'?`, and every error points at the exact key (like
`checks.files[0].mode`). Put release numbers and file modes in quotes:
YAML reads an unquoted `0644` as 420 and `24.10` as 24.1. Service states
are whatever `systemctl is-active` can print: `active`, `inactive`,
`failed`, `activating`, `deactivating`, `reloading`, `refreshing`,
`maintenance`.

The user-data has to start with `#cloud-config`, otherwise cloud-init just
ignores it without saying anything. Other formats (`#!/bin/bash`,
`#include`, MIME multi-part...) are accepted, but schema validation only
works on `#cloud-config`.

## Commands

```text
seedcheck run CONFIG          Validate, launch, check and report.
    --release REL             Only this release (22.04 or jammy). Repeat it for more. Overrides the config.
    --markdown PATH           Also write the table as Markdown (works in a GitHub job summary).
    --json PATH               Also write everything as JSON.
    --artifacts-dir DIR       Save each container's cloud-init logs in DIR/RELEASE/.
    --timeout SECONDS         Override the timeout from the config.
    --keep                    Don't delete the containers (for debugging).
    -v, --verbose             Show every lxc command and its output.

seedcheck validate CONFIG     Only validate. No LXD needed.
seedcheck releases            List supported releases, codenames and images.
seedcheck --version           Print the version.
```

Progress goes to stderr and the table to stdout, so you can redirect the
table. Colors only show up in a terminal, and never if `NO_COLOR` is set.

| Release | Codename | LXD image | Python |
|---|---|---|---|
| 22.04 | jammy | `ubuntu:22.04` | 3.10 |
| 24.04 | noble | `ubuntu:24.04` | 3.12 |
| 26.04 | resolute | `ubuntu:26.04` | 3.14 |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every check on every release is PASS or WARN. |
| 1 | At least one check FAILed. |
| 2 | Bad arguments, `seedcheck.yaml` or user-data. Caught before anything is launched. |
| 3 | Infrastructure problem: LXD missing or unreachable, or a release hit an ERROR (image not found, timeout). Wins over 1, since the results are incomplete. |
| 130 | You pressed Ctrl+C. The containers still get deleted. |

A cell can be PASS, FAIL, WARN (passed, but cloud-init reported something
minor like a deprecated key), SKIP (didn't run because the release failed
earlier) or ERROR.

## How it works

1. **Validate** `seedcheck.yaml` and the user-data first. If cloud-init is
   installed on your machine, its `schema` command runs too.
2. **Launch** one LXD container per release from the official `ubuntu:`
   images, with the user-data passed through the `cloud-init.user-data`
   key.
3. **Wait** for cloud-init to finish in each one (`cloud-init status
   --wait`), up to the timeout.
4. **Check** inside each container: that release's own `cloud-init schema
   --system`, cloud-init's error report (`cloud-init status --long --format
   json`), then packages (`dpkg-query`), services (`systemctl is-active`),
   files (`stat`, `grep -F`), users (`id -nG`) and commands (`sh -c`).
5. **Report** the table in the terminal, plus Markdown and JSON if you ask.
6. **Clean up.** Every container gets deleted, whether things passed,
   failed, timed out or you hit Ctrl+C, and the exit code tells your CI what
   happened.

Releases run one after the other, and one failing doesn't stop the rest.
The design and the reasons behind it are in [docs/design.md](docs/design.md).

## In GitHub Actions

```yaml
jobs:
  cloud-init:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
      - uses: canonical/setup-lxd@8c6a87bfb56aa48f3fb9b830baa18562d8bfd4ee # v1
        with:
          channel: 5.21/stable
      - name: Install Seedcheck
        run: |
          wget -q https://github.com/oum255/seedcheck/releases/download/v0.1.0/seedcheck_0.1.0_all.deb
          sudo apt-get install -y ./seedcheck_0.1.0_all.deb
      - name: Test the cloud-init config
        run: seedcheck run seedcheck.yaml --markdown report.md --artifacts-dir logs
      - name: Show the table in the job summary
        if: always()
        run: cat report.md >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: cloud-init-logs
          path: logs/
```

`canonical/setup-lxd` gives the runner access to LXD and opens up Docker's
firewall for the LXD bridge (without that, containers have no network).
Seedcheck's own [CI](.github/workflows/ci.yml) does exactly this on every
push.

## Limitations

- **Containers, not VMs.** They share the host's kernel, so kernel
  modules, kernel settings, the bootloader and some low-level systemd
  stuff aren't tested. LXD can run VMs too, that could be a future option.
- **Ubuntu only**, and only the three releases above.
- **One release at a time**, about a minute each.
- **You test your machine's architecture** (amd64 or arm64). If a
  third-party apt repo has no packages for it, the check fails even if
  your config is fine.
- **The quick schema check on your machine uses your own cloud-init**,
  which may not match a release's version. The per-release `schema` row is
  the one to trust.
- The user-data is passed as a single command-line argument, and Linux caps
  that at 128 KiB.

## Development

```bash
sudo apt install git python3-venv shellcheck
git clone https://github.com/oum255/seedcheck.git && cd seedcheck
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check . && mypy src   # lint, format, types
pytest --cov=seedcheck                              # unit tests, no LXD needed
pytest -m integration                               # real containers, about 8 minutes
scripts/build-deb.sh                                # build the .deb (Ubuntu 24.04+)
scripts/smoke-install.sh 22.04 dist/seedcheck_0.1.0_all.deb
```

Numbers from real runs (macOS with Python 3.13, Ubuntu 22.04 with Python
3.10, and CI with 3.10, 3.12 and 3.14):

- **287 unit tests** that run without LXD in under a second, with **99.75%
  coverage** of lines and branches (CI fails under 90%). They use a fake
  LXD client, so they run anywhere.
- **5 integration tests** on a real LXD that run every example on 22.04,
  24.04 and 26.04 and check no container is left behind.

Want to add a check type or a release? See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Copyright © 2026 Oumayma Haddour. All rights reserved. The code is public so
you can read it, but you can't reuse it without my permission. See
[LICENSE](LICENSE).
