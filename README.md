# Seedcheck

Seedcheck tests a cloud-init configuration on several Ubuntu releases at once,
in LXD containers, and tells you where it works and where it breaks.

[![CI](https://github.com/oum255/seedcheck/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/oum255/seedcheck/actions/workflows/ci.yml)

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

And when something breaks, the matrix shows where and why
(`examples/broken`):

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

Both outputs come from real runs; the CI job summary shows the same matrix,
as Markdown, on every push.

## Why

A cloud-init file that works on one Ubuntu release can fail on another:
package names and versions change, services move to socket activation, apt
switches source formats, cloud-init reports the same problem with a
different exit code. Building Seedcheck turned up exactly these
differences between 22.04, 24.04 and 26.04 (see
[docs/design.md](docs/design.md#differences-between-releases)). Seedcheck
catches them before your users do: one command, one container per release,
one pass/fail matrix.

## Requirements

- Ubuntu 22.04, 24.04 or 26.04 (native, in a VM, or WSL2 with systemd).
- LXD. Ubuntu ships it as a snap, so the Debian package cannot depend on
  it; install it first:

  ```bash
  sudo snap install lxd
  sudo lxd init --auto
  sudo usermod -aG lxd "$USER"   # then log out and back in
  ```

  [`scripts/setup-lxd.sh`](scripts/setup-lxd.sh) does the same. If LXD is
  missing or unusable, `seedcheck run` stops with exit code 3 and says
  which step is missing.
- Network access from the containers (to download images and packages).

## Installation

From the `.deb` attached to the
[latest release](https://github.com/oum255/seedcheck/releases/latest):

```bash
wget https://github.com/oum255/seedcheck/releases/download/v0.1.0/seedcheck_0.1.0_all.deb
sudo apt install ./seedcheck_0.1.0_all.deb
seedcheck --version
```

The package is `Architecture: all` (pure Python) and installs on 22.04,
24.04 and 26.04, amd64 and arm64. Its only dependency besides Python is
`python3-yaml`. The examples are installed in
`/usr/share/doc/seedcheck/examples/`, and `man seedcheck` works.

From source (Python 3.10 or newer):

```bash
git clone https://github.com/oum255/seedcheck.git
cd seedcheck
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

## Quick start (five minutes)

```bash
git clone https://github.com/oum255/seedcheck.git && cd seedcheck

# 1. Check the configuration without launching anything (no LXD needed).
seedcheck validate examples/nginx/seedcheck.yaml

# 2. Try it on one release first (about a minute).
seedcheck run examples/nginx/seedcheck.yaml --release 24.04

# 3. Then all three, with reports for CI or for a pull request.
seedcheck run examples/nginx/seedcheck.yaml --markdown report.md --json report.json
echo "exit code: $?"
```

[`examples/nginx/user-data.yaml`](examples/nginx/user-data.yaml) installs
nginx and curl, creates a `deploy` user, writes `/etc/motd` and starts
nginx; [`seedcheck.yaml`](examples/nginx/seedcheck.yaml) checks each of
those. Two more examples:
[`apt-source`](examples/apt-source/) adds a Launchpad PPA with
cloud-init's `apt:` module and checks that git came from it, and
[`broken`](examples/broken/) fails on purpose.

## `seedcheck.yaml` reference

```yaml
user_data: user-data.yaml        # required; relative to this file
releases: ["22.04", "24.04", "26.04"]  # optional; version or codename; this is the default
timeout: 600                     # optional; seconds to wait for each release

checks:                          # optional; schema and cloud-init are always checked
  packages:                      # each must be installed (dpkg); its version is shown
    - nginx
    - name: curl                 # same as "- curl"
  services:                      # each must be in this state (systemctl is-active)
    - nginx                      # state defaults to active
    - name: nginx.service        # the .service suffix is optional
      state: active
  files:                         # path must exist and be a regular file
    - path: /etc/motd
      contains: "Provisioned by cloud-init"  # optional; fixed text, may span lines
      mode: "0644"               # optional; quoted octal
      owner: root                # optional
  users:                         # user must exist
    - name: deploy
      groups: [sudo]             # optional; must be in each
  commands:                      # run with sh -c inside the container
    - name: nginx answers on port 80
      run: curl -fsS http://localhost
      expect_exit: 0             # optional; default 0
      expect_stdout_contains: nginx  # optional
```

Validation is strict, so mistakes are caught before any container starts.
Unknown keys are rejected with a suggestion (`checks.package: unknown key
... did you mean 'packages'?`), and every error names the exact key, such
as `checks.files[0].mode`. Release numbers and file modes must be quoted:
YAML reads an unquoted `0644` as the number 420 and `24.10` as 24.1.
Service states are those `systemctl is-active` prints: `active`,
`inactive`, `failed`, `activating`, `deactivating`, `reloading`,
`refreshing`, `maintenance`.

The user-data file must start with `#cloud-config`; cloud-init silently
ignores a file without it. Other cloud-init formats (`#!/bin/bash`,
`#include`, MIME multi-part, ...) are accepted, with a note that schema
validation only applies to `#cloud-config`.

## Command-line reference

```text
seedcheck run CONFIG          Validate, launch, check and report.
    --release REL             Only test this release (22.04 or jammy); repeatable; overrides the config.
    --markdown PATH           Also write the matrix as Markdown (renders in a GitHub job summary).
    --json PATH               Also write the full results as JSON.
    --artifacts-dir DIR       Save each container's cloud-init logs in DIR/RELEASE/.
    --timeout SECONDS         Override the config's timeout.
    --keep                    Do not delete the containers (for debugging).
    -v, --verbose             Show every lxc command and its output.

seedcheck validate CONFIG     Static validation only. Does not need LXD.
seedcheck releases            List supported releases, codenames and LXD images.
seedcheck --version           Print the version.
```

Progress goes to stderr and the matrix to stdout, so the matrix can be
redirected. Color is used only on a terminal, and never when `NO_COLOR` is
set.

| Release | Codename | LXD image | Python |
|---|---|---|---|
| 22.04 | jammy | `ubuntu:22.04` | 3.10 |
| 24.04 | noble | `ubuntu:24.04` | 3.12 |
| 26.04 | resolute | `ubuntu:26.04` | 3.14 |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every check on every release is PASS or WARN. |
| 1 | At least one check is FAIL. |
| 2 | Invalid usage, `seedcheck.yaml` or user-data, found before anything is launched. |
| 3 | Infrastructure error: LXD missing or unreachable, or a release ended in ERROR (image not found, timeout). Takes precedence over 1, since the results are incomplete. |
| 130 | Interrupted with Ctrl+C. The containers are still deleted. |

Each cell is PASS, FAIL, WARN (passed with a caveat: cloud-init reported
only recoverable errors such as deprecations), SKIP (not evaluated because
the release failed earlier) or ERROR.

## How it works

1. **Validate** `seedcheck.yaml` and the user-data before launching
   anything, including `cloud-init schema` if cloud-init is installed on
   the host.
2. **Launch** one LXD container per release from the official
   `ubuntu:` images, passing the user-data through the
   `cloud-init.user-data` instance key.
3. **Wait** for cloud-init to finish in each container (`cloud-init
   status --wait`), within the timeout.
4. **Check** inside each container: the release's own `cloud-init schema
   --system`, cloud-init's error report (`cloud-init status --long
   --format json`), then packages (`dpkg-query`), services (`systemctl
   is-active`), files (`stat`, `grep -F`), users (`id -nG`) and commands
   (`sh -c`).
5. **Report** the matrix in the terminal, and as Markdown and JSON on
   request.
6. **Clean up**: every container is deleted, on success, failure, timeout
   or Ctrl+C, and the exit code tells CI what happened.

Releases run one after another; a failure on one never stops the others.
[docs/design.md](docs/design.md) explains the design and the decisions
behind it.

## Using Seedcheck in GitHub Actions

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
      - name: Test the cloud-init configuration
        run: seedcheck run seedcheck.yaml --markdown report.md --artifacts-dir logs
      - name: Show the matrix in the job summary
        if: always()
        run: cat report.md >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: cloud-init-logs
          path: logs/
```

`canonical/setup-lxd` grants the runner access to LXD and opens Docker's
firewall for the LXD bridge, which containers otherwise cannot get through.
Seedcheck's own [CI](.github/workflows/ci.yml) runs exactly this, on every
push.

## Limitations

- **Containers, not virtual machines.** Containers share the host's
  kernel, so kernel modules, kernel parameters, bootloader and some
  low-level systemd behaviour are not tested. LXD can run VMs too; that is
  a possible future option.
- **Ubuntu only**, and only the three releases above.
- **Releases run sequentially**, about a minute each.
- **The host architecture is the one tested** (amd64 or arm64). A
  third-party apt repository without packages for your architecture fails
  even if your configuration is right.
- **Host schema validation uses the host's cloud-init**, which may be
  older or newer than a release's; the per-release `schema` row is the
  authoritative check.
- The user-data is passed as a single command-line argument, which Linux
  limits to 128 KiB.

## Development

```bash
git clone https://github.com/oum255/seedcheck.git && cd seedcheck
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check . && mypy src   # lint, format, types
pytest --cov=seedcheck                              # unit tests, no LXD needed
pytest -m integration                               # real containers, about 8 minutes
scripts/build-deb.sh                                # build the .deb (Ubuntu 24.04+)
scripts/smoke-install.sh 22.04 dist/seedcheck_0.1.0_all.deb
```

Measured on 2026-10-16 (macOS with Python 3.13, then Ubuntu 22.04 with
Python 3.10, and in CI on Python 3.10, 3.12 and 3.14):

- **287 unit tests**, run without LXD in under a second, with **99.75%
  coverage** of statements and branches (CI fails below 90%). Unit tests
  use a fake LXD client, so they run anywhere.
- **5 integration tests** against a real LXD, which run every example on
  Ubuntu 22.04, 24.04 and 26.04 and check that no container is left
  behind.

See [CONTRIBUTING.md](CONTRIBUTING.md) to add a check type or a release.

## License

[MIT](LICENSE) © 2026 Oumayma Haddour
