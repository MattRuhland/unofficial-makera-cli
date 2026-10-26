"""Unofficial command line interface for the Makera Z1 desktop CNC machine."""

__version__ = "0.1.1"


class MakeraError(Exception):
    """A failure the CLI reports as one line and exit status 1."""
