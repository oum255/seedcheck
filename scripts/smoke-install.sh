#!/usr/bin/env bash
# Install a seedcheck .deb with apt in a fresh LXD container of one Ubuntu
# release, run it, and delete the container. Proves the package installs and
# works on that release.
#
# Usage: scripts/smoke-install.sh RELEASE DEB
#   e.g. scripts/smoke-install.sh 22.04 dist/seedcheck_0.1.0_all.deb
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 RELEASE DEB   (e.g. $0 22.04 dist/seedcheck_0.1.0_all.deb)" >&2
  exit 2
fi
release="$1"
deb="$2"
if [[ ! -f "$deb" ]]; then
  echo "error: no such file: $deb" >&2
  exit 2
fi

expected_version="$(dpkg-deb --field "$deb" Version)"
name="seedcheck-smoke-${release//./}-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"

# Always delete the container, whether the test passes, fails or is interrupted.
cleanup() {
  lxc delete --force "$name" </dev/null >/dev/null 2>&1 || true
}
trap cleanup EXIT

# lxc reads instance configuration from stdin when it is not a terminal, so
# every lxc call gets an explicit empty stdin.
in_container() {
  lxc exec "$name" --cwd /root -- "$@" </dev/null
}

echo "==> [$release] launching a fresh ubuntu:$release container ($name)"
lxc launch "ubuntu:$release" "$name" </dev/null

for _ in $(seq 120); do
  in_container true 2>/dev/null && break
  sleep 1
done
# Wait for first boot to finish so cloud-init's own apt run does not hold the lock.
in_container cloud-init status --wait >/dev/null || true

echo "==> [$release] installing $(basename "$deb") with apt"
lxc file push "$deb" "$name/root/" </dev/null
in_container env DEBIAN_FRONTEND=noninteractive apt-get update -q
in_container env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "./$(basename "$deb")"

echo "==> [$release] running seedcheck"
in_container python3 --version
actual_version="$(in_container seedcheck --version)"
echo "$actual_version"
if [[ "$actual_version" != "seedcheck $expected_version" ]]; then
  echo "error: expected 'seedcheck $expected_version', got '$actual_version'" >&2
  exit 1
fi
in_container seedcheck releases
# The examples ship in the package itself; validating one proves they were installed.
in_container seedcheck validate /usr/share/doc/seedcheck/examples/nginx/seedcheck.yaml

echo "==> [$release] smoke test passed"
