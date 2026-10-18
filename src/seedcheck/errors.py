"""Seedcheck's exceptions.

Everything we raise on purpose inherits from SeedcheckError, so the CLI
can catch it in one place and pick the right exit code.
"""


class SeedcheckError(Exception):
    """Base class for Seedcheck errors."""


class ConfigError(SeedcheckError):
    """Bad seedcheck.yaml or user-data (exit code 2)."""


class LxdError(SeedcheckError):
    """LXD is missing, unreachable, or an lxc command failed (exit code 3)."""


class TimeoutError_(SeedcheckError):
    """The container never got ready, or cloud-init took too long.

    The underscore is there so it doesn't shadow Python's TimeoutError.
    """
