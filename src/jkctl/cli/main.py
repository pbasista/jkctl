"""Parse the command line, open what the command needs, and run it.

The bus is opened here and closed here -- a command never has to remember to
close the port, and a command that needs no port never opens one.  Every
expected failure lands in :func:`devicectl.cli.main.run`, which prints one
line and returns an exit code, so a handler can raise the error that
describes what went wrong and return an exit code for everything else.
"""

from __future__ import annotations

import sys

from devicectl.cli.command import Need
from devicectl.cli.main import run

from jkctl.cli.commands import COMMANDS
from jkctl.cli.exits import EXIT_ERROR
from jkctl.cli.parser import (
    build_parser,
    insert_default_action,
    insert_default_command,
)
from jkctl.cli.target import device_target, open_bus, open_device


def _run(argv: list[str] | None) -> int:
    """Dispatch one invocation, letting every expected failure reach main()."""
    parser = build_parser()
    argv = insert_default_command(sys.argv[1:] if argv is None else list(argv))
    argv = insert_default_action(argv)
    args = parser.parse_args(argv)
    if args.command is None:  # pragma: no cover - insert_default_command fills it
        parser.print_help()
        return EXIT_ERROR

    command = COMMANDS[args.command]
    needs = command.need(getattr(args, "action", None))
    if needs is Need.NOTHING:
        return command.run(None, args)

    # Both remaining kinds resolve the same target; it is put on `args` so a
    # handler can name the port it is talking to without resolving it again.
    if needs is Need.LINK:
        opened = open_bus(args)
        if opened is None:
            return EXIT_ERROR
        bus, args.target = opened
        with bus:
            return command.run(bus, args)

    opened = open_device(args)
    if opened is None:
        return EXIT_ERROR
    bus, device = opened
    args.target = device_target(args)
    with bus:
        return command.run(device, args)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch to the right command, and return an exit code."""
    # A flash interrupted here leaves the BMS in its bootloader; the message
    # says so where it matters, not from the catch-all in `run`.
    return run(lambda: _run(argv))


__all__ = ["main"]
