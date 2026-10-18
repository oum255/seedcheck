#!/usr/bin/env bash
# Builds the .deb into dist/, runs lintian on it and shows what's inside.
# Needs Ubuntu 24.04+ (no pybuild-plugin-pyproject on 22.04) and the
# build deps from debian/control.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

version="$(dpkg-parsechangelog --show-field Version)"
deb="seedcheck_${version}_all.deb"

echo "==> Building ${deb}..."
# -b: binary only. -us -uc: don't sign.
dpkg-buildpackage -us -uc -b

# dpkg-buildpackage puts its output in the parent directory.
mkdir -p dist
mv "../${deb}" ../seedcheck_"${version}"_*.buildinfo ../seedcheck_"${version}"_*.changes dist/

echo "==> lintian"
lintian --info --display-info --fail-on error,warning "dist/${deb}"
echo "lintian: no errors or warnings"

# Only seedcheck should end up in dist-packages. Leftovers from the tests
# would get installed as stray modules, and lintian doesn't notice that.
stray="$(dpkg -c "dist/${deb}" | awk '{print $6}' \
  | grep -E '^\./usr/lib/python3/dist-packages/[^/]+/$' \
  | grep -vE '/dist-packages/seedcheck(-[^/]+\.dist-info)?/$' || true)"
if [[ -n "$stray" ]]; then
  echo "error: unexpected top-level entries in dist-packages:" >&2
  echo "$stray" >&2
  exit 1
fi

echo "==> Contents (dpkg -c)"
dpkg -c "dist/${deb}"

echo "==> Control information (dpkg -I)"
dpkg -I "dist/${deb}"

echo "==> Built dist/${deb}"
