#!/usr/bin/env bash
# Install and initialize LXD so Seedcheck has a container backend to launch
# Ubuntu releases into. Safe to re-run: skips steps that are already done.
set -euo pipefail

if ! command -v snap >/dev/null 2>&1; then
  echo "error: snapd is not available on this system; LXD is distributed as a snap" >&2
  exit 1
fi

if ! command -v lxd >/dev/null 2>&1 && ! snap list lxd >/dev/null 2>&1; then
  echo "==> Installing LXD (snap)..."
  sudo snap install lxd
else
  echo "==> LXD snap already installed, skipping install."
fi

echo "==> Initializing LXD with defaults (safe if already initialized)..."
if ! sudo lxd init --auto; then
  echo "note: 'lxd init --auto' failed or LXD was already initialized; continuing." >&2
fi

if ! groups "$USER" | grep -qw lxd; then
  echo "==> Adding $USER to the 'lxd' group..."
  sudo usermod -aG lxd "$USER"
  echo
  echo "You were just added to the 'lxd' group. This only takes effect on a"
  echo "new login session. Log out and back in (or run 'newgrp lxd' in this"
  echo "shell) before using the 'lxc' command without sudo."
else
  echo "==> $USER is already in the 'lxd' group."
fi

echo "==> Done. Verify with: lxc info"
