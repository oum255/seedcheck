#!/usr/bin/env bash
# Makes sure the tag, __version__ and debian/changelog all say the same
# version, then writes that version's CHANGELOG.md section to NOTES_FILE
# (the GitHub release notes).
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

# Grab everything from "## [VERSION]" up to the next "## [" heading
# or the links at the bottom ("[0.1.0]: https://...").
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
