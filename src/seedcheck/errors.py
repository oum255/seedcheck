"""Exception types raised by Seedcheck.

Every error Seedcheck raises on purpose derives from SeedcheckError, so the
CLI can catch them in one place and map each kind to an exit code.
"""


class SeedcheckError(Exception):
    """Base class for all errors raised by Seedcheck."""


class ConfigError(SeedcheckError):
    """seedcheck.yaml or the user-data file is invalid (exit code 2)."""


class LxdError(SeedcheckError):
    """LXD is missing, unreachable, or an lxc command failed (exit code 3)."""


class TimeoutError_(SeedcheckError):
    """A container did not become ready, or cloud-init did not finish, in time.

    The trailing underscore avoids shadowing the built-in TimeoutError.
    """
