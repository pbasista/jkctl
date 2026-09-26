"""The base class every error this program raises on purpose derives from.

A command fails for one of two reasons.  Either the bus, the BMS or the input
was not what it needed -- a unit that does not answer, a setting outside its
allowed range, a firmware image for another model -- which is ordinary and
gets one clear line on stderr.  Or this program has a bug, which deserves a
traceback.

:class:`JkError` is the first kind.  Every module raises its own subclass so a
caller that cares can still tell a refused register apart from a bad firmware
file, but :func:`jkctl.cli.main` catches this one class, prints the message and
exits -- it does not have to know the list.

It derives in turn from :class:`devicectl.errors.DeviceError`, which is what
the shared CLI and web layers catch: they need "an expected failure" and
nothing more specific.

Errors that are specifically about a *value* also derive from
:class:`ValueError`, because that is what they are and callers already catch
them that way.
"""

from __future__ import annotations

from devicectl.errors import DeviceError


class JkError(DeviceError):
    """An expected failure, reportable to the user as a single line."""


__all__ = ["JkError"]
