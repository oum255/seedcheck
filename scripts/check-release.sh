#!/usr/bin/env bash
# Check that a release tag, the Python package version and the Debian
# changelog all name the same version, and write that version's section of
# CHANGELOG.md to NOTES_FILE (used as the GitHub release notes).
#
# Usage: scripts/check-release.sh TAG NOTES_FILE
#   e.g. scripts/check-release.sh v0.1.0 release-notes.md
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 TAG NOTES_FILE   (e.g. $0 v0.1.0 release-notes.md)" >&2
  exit 2
fi
tag="$1"
notes="$2"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ "$tag" != v* ]]; then
  echo "error: release tags look like v0.1.0, got '$tag'" >&2
  exit 1
fi
tag_version="${tag#v}"
python_version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' src/seedcheck/__init__.py)"
debian_version="$(dpkg-parsechangelog --show-field Version)"

echo "tag:                   $tag_version"
echo "__version__:           $python_version"
echo "debian/changelog:      $debian_version"
if ! [[ "$tag_version" == "$python_version" && "$python_version" == "$debian_version" ]]; then
  echo "error: the tag, src/seedcheck/__init__.py and debian/changelog must agree" >&2
  exit 1
fi

# The section runs from "## [VERSION]" to the next "## [" heading, or to the
# link definitions ("[0.1.0]: https://...") at the end of the file.
awk -v heading="## [${tag_version}]" '
  index($0, heading) == 1 { found = 1; next }
  found && (/^## \[/ || /^\[[^]]+\]: /) { exit }
  found { print }
' CHANGELOG.md >"$notes"
if ! grep -q '[^[:space:]]' "$notes"; then
  echo "error: CHANGELOG.md has no section for ${tag_version}" >&2
  exit 1
fi
echo "release notes for ${tag_version} written to ${notes}"
