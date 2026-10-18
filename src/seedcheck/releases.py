"""Supported Ubuntu releases and how to look them up."""

from __future__ import annotations

from dataclasses import dataclass

from seedcheck.errors import ConfigError


@dataclass(frozen=True)
class Release:
    """A supported release and the LXD image we launch for it."""

    version: str
    codename: str
    image: str
    python: str


SUPPORTED: tuple[Release, ...] = (
    Release("22.04", "jammy", "ubuntu:22.04", "3.10"),
    Release("24.04", "noble", "ubuntu:24.04", "3.12"),
    Release("26.04", "resolute", "ubuntu:26.04", "3.14"),
)

DEFAULT_RELEASES: tuple[str, ...] = tuple(release.version for release in SUPPORTED)


def supported_summary() -> str:
    """Supported releases as text, e.g. "22.04 (jammy), 24.04 (noble)"."""
    return ", ".join(f"{release.version} ({release.codename})" for release in SUPPORTED)


def resolve(name: str) -> Release:
    """Find a release by number ("22.04") or codename ("jammy")."""
    wanted = name.strip().lower()
    for release in SUPPORTED:
        if wanted in (release.version, release.codename):
            return release
    raise ConfigError(f"unknown or unsupported release {name!r}; supported: {supported_summary()}")
