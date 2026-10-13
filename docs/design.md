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

Still to verify in later phases: `cloud-init.user-data` config key acceptance
(phase 3), `cloud-init status --long --format json` field shape across
releases (phase 3/4), `cloud-init schema --system` (phase 4).

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
