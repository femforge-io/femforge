"""The failure every ``femforge`` subcommand raises, shared so a command module needs no import back into the entry point."""

from __future__ import annotations


__all__ = ["CliError"]


class CliError(Exception):
    """A user-facing failure: ``main`` prints the message and exits 1."""
