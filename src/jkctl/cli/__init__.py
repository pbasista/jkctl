"""The command-line interface.

The package is one module per concern, and one module per group of commands:

* :mod:`jkctl.cli.main` -- parse, open what the command needs, run it;
* :mod:`jkctl.cli.parser` -- the root parser and the shared options;
* :mod:`jkctl.cli.commands` -- the command groups, and the table that maps a
  typed word to the function that runs it;
* :mod:`jkctl.cli.target` -- which port, and which unit on it;
* :mod:`jkctl.cli.output` -- tables, shared formats, the one prompt;
* :mod:`jkctl.cli.report` -- progress on a terminal;
* :mod:`jkctl.cli.exits` -- the process exit codes.

What a subcommand *is* -- :class:`~devicectl.cli.command.Command` and the
:class:`~devicectl.cli.command.Need` it declares -- comes from
``devicectl-core``, along with the three exit codes that mean the same thing
in every program of this shape.

To add a command: write the handler in the right module of
:mod:`jkctl.cli.commands`, describe it in that module's ``add_parsers``, and
name it in that module's ``COMMANDS``.

Messages on stderr open with ``error:`` when the command failed, ``warning:``
when it carried on regardless, and ``note:`` for an advisory -- the same two
words argparse prints, so a script can grep for one prefix instead of several.
A declined prompt says ``Aborted.``, and a command that simply found nothing
says so in a sentence; neither is a malfunction.
"""

from jkctl.cli.exits import (
    EXIT_ABORTED,
    EXIT_ERROR,
    EXIT_INCOMPATIBLE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_UPDATE_FAILED,
)
from jkctl.cli.main import main
from jkctl.cli.parser import (
    DEFAULT_ACTIONS,
    build_parser,
    insert_default_action,
    insert_default_command,
)

__all__ = [
    "DEFAULT_ACTIONS",
    "EXIT_ABORTED",
    "EXIT_ERROR",
    "EXIT_INCOMPATIBLE",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_UPDATE_FAILED",
    "build_parser",
    "insert_default_action",
    "insert_default_command",
    "main",
]
