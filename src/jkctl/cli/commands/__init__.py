"""The command groups, and the table that maps a typed word to a handler.

Each module here owns one group: the handlers, and the ``add_parsers`` that
describes them to argparse.  Adding a command means adding a function and two
lines to the module's own ``COMMANDS`` -- nothing outside this package changes.

``GROUPS`` is ordered, and that order is the order of ``jkctl --help``: find
the unit first, then read it, then change its configuration, then the acts
that reconfigure or restart the board itself, then the tools for when it will
not answer at all.
"""

from __future__ import annotations

from devicectl.cli.command import Command

from jkctl.cli.commands import (
    config,
    controls,
    devices,
    diag,
    firmware,
    history,
    protocols,
    registers,
    settings,
    status,
    ui,
)

GROUPS = (
    ui,
    devices,
    status,
    registers,
    history,
    settings,
    controls,
    protocols,
    firmware,
    diag,
    config,
)

COMMANDS: dict[str, Command] = {}
for _group in GROUPS:
    for _name, _command in _group.COMMANDS.items():
        assert _name not in COMMANDS, f"two groups claim the command {_name!r}"
        COMMANDS[_name] = _command

__all__ = ["COMMANDS", "GROUPS"]
