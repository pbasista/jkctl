"""The exit codes jkctl returns.

A script can tell the three general outcomes apart: 0 means it did what was
asked, 130 is the shell's own convention for Ctrl+C (128 + SIGINT), and 1 is
everything else -- no unit at that address, no such register, a value the BMS
refused.  There is deliberately no code per failure kind; the message on
stderr says which.

Those three, and the two firmware weights that mean the same thing in every
program of this shape, come from :mod:`devicectl.cli.exits`; they are
re-exported here so a caller has one place to look rather than two.  The
firmware flash adds one more of its own.  It is the one command likely to be
run unattended across a rack of batteries, where "this image is for another
model" and "the transfer stopped halfway" call for very different reactions.
"""

from __future__ import annotations

from devicectl.cli.exits import (
    EXIT_ERROR,
    EXIT_INCOMPATIBLE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_UPDATE_FAILED,
)

# --- jkctl firmware ------------------------------------------------------------------------

EXIT_ABORTED = 5  # a confirmation was declined, so nothing was done

__all__ = [
    "EXIT_ABORTED",
    "EXIT_ERROR",
    "EXIT_INCOMPATIBLE",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_UPDATE_FAILED",
]
