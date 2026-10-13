"""Allow running Seedcheck as ``python -m seedcheck``."""

import sys

from seedcheck.cli import main

sys.exit(main())
