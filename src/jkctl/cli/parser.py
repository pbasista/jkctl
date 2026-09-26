"""Building the command-line parser out of the command groups.

The root parser knows only the program's own options and the list of groups;
each group in :mod:`jkctl.cli.commands` describes its own subcommands.  Adding
a command means editing one file, not scrolling to the right place in a long
one.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from devicectl.cli.parser import (
    default_actions,
    insert_default_action as _insert_default_action,
    insert_default_command as _insert_default_command,
)

from jkctl import modbus as M
from jkctl.cli.commands import COMMANDS, GROUPS
from jkctl.config import default_config_path

# What a command does when its ACTION is left out, read off the command table
# rather than listed again here: an action qualifies only if it needs no
# further input and only reads, and `preset` and `calibrate` have none on
# purpose, because every one of their actions changes the battery.
DEFAULT_ACTIONS = default_actions(COMMANDS)


def _version_text() -> str:
    """Return the --version string."""
    from jkctl import __version__

    return f"jkctl {__version__}"


def common_options() -> argparse.ArgumentParser:
    """Build the parent parser carrying the options every command shares.

    Passed as ``parents=[common]`` to each subparser, so twenty-odd commands
    describe how to reach a BMS in one place rather than twenty.
    ``add_help=False`` because each child adds its own ``-h``.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--device", help="device name from jk.toml")
    # These default to None, not to their real defaults, so that
    # resolve_target can tell "not given" from "given the usual value".
    p.add_argument(
        "--port", default=None, help=f"serial port (default {M.DEFAULT_PORT})"
    )
    p.add_argument(
        "--baud", type=int, default=None, help=f"baud rate (default {M.DEFAULT_BAUD})"
    )
    p.add_argument(
        "--id",
        default=None,
        metavar="ID",
        help="BMS address = its DIP-switch id (default 1).  The read commands "
        "also take a list (1,2,5), a range (1-4) or 'all'",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=f"reply timeout in seconds, raise it on a slow host "
        f"(default {M.QUERY_TIMEOUT})",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=None,
        help="retries on a lost or garbled reply (default 2)",
    )
    p.add_argument(
        "--addr-offset",
        type=lambda s: int(s, 0),
        default=None,
        help=f"frameAddrOffset (default 0x{M.FRAME_ADDR_OFFSET:04X})",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"settings file (default: {default_config_path()})",
    )
    p.add_argument(
        "--trace",
        action="store_true",
        help="hex-dump every frame on the wire to stderr",
    )
    return p


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    common = common_options()
    p = argparse.ArgumentParser(
        prog="jkctl",
        description="Inspect and configure JK BMS units over RS485 Modbus, and "
        "flash their firmware (a reimplementation of the parts of JK BMS "
        "Monitor that matter on Linux).  Run with no command at all to serve "
        "the web interface, which is the usual way to use it; everything the "
        "page does is also one of the commands below.",
    )
    p.add_argument("--version", action="version", version=_version_text())
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    for group in GROUPS:
        group.add_parsers(sub, common)
    return p


def insert_default_command(argv: list[str]) -> list[str]:
    """Return ``argv`` with ``ui`` filled in when no command was typed.

    ``jkctl`` on its own serves the web interface, because that is how most
    people will use this program and a page of usage text is not what they
    came for.  Options meant for it are handed through, so ``jkctl --port
    /dev/ttyUSB1`` opens the page on that port rather than complaining that
    ``--port`` belongs to a subcommand.
    """
    return _insert_default_command(argv, "ui")


def insert_default_action(argv: list[str]) -> list[str]:
    """Return ``argv`` with a command's default ACTION filled in, if missing.

    ``jkctl settings`` becomes ``jkctl settings show``, and options meant for
    that default action are handed through to it (``jkctl settings --json``
    becomes ``jkctl settings show --json``), because the connection options
    live on the action parsers rather than the command itself.
    """
    return _insert_default_action(argv, DEFAULT_ACTIONS)


__all__ = ["DEFAULT_ACTIONS", "build_parser", "common_options", "insert_default_action"]
