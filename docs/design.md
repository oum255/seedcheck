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

Still to verify in later phases: `cloud-init.user-data` config key acceptance
(phase 3), `cloud-init status --long --format json` field shape across
releases (phase 3/4), `cloud-init schema --system` (phase 4).

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
