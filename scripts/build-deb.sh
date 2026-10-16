#!/usr/bin/env bash
# Build the seedcheck .deb into dist/, check it with lintian, and show what it
# contains. Run on Ubuntu 24.04 or newer (pybuild-plugin-pyproject is not
# available on 22.04) with the build dependencies listed in debian/control.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

version="$(dpkg-parsechangelog --show-field Version)"
deb="seedcheck_${version}_all.deb"

echo "==> Building ${deb}..."
# -b: binary package only; -us -uc: do not sign anything.
dpkg-buildpackage -us -uc -b

# dpkg-buildpackage writes its results next to the source tree, not inside it.
mkdir -p dist
mv "../${deb}" ../seedcheck_"${version}"_*.buildinfo ../seedcheck_"${version}"_*.changes dist/

echo "==> lintian"
lintian --info --display-info --fail-on error,warning "dist/${deb}"
echo "lintian: no errors or warnings"

# Only seedcheck itself may land in dist-packages. Anything else (for example
# test data left in pybuild's build directory) would be installed as a stray
# top-level module. lintian does not catch this, so check it here.
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
