"""Running one read over every unit ``--id`` names.

A JK bus carries up to sixteen boards -- a four-position DIP switch decides
which -- and a bank of four batteries is an ordinary installation rather than
an exotic one.  The vendor's application answers that with a device selector
and one window at a time; ``--id 1,2,3`` and ``--id all`` answer it by
running the read you asked for over each of them and printing one section
each.

The combining is :mod:`devicectl.cli.fanout`.  What is here is what a JK bus
adds: the DIP switch's range, and the units this invocation ended up
addressing.
"""

from __future__ import annotations

import argparse

from devicectl.cli.fanout import fan_out as _fan_out, parse_range

from jkctl.device import Device

# Every address a four-position DIP switch can be set to.
ALL_IDS = range(16)


def parse_ids(text: str | int) -> list[int] | None:
    """Parse ``--id``: one address, a comma list, a range, or ``all``.

    ``None`` means "every address on the bus", which cannot be resolved until
    a port is open, so it is left for the caller to sweep for.
    """
    return parse_range(text, ALL_IDS, what="id")


def devices_of(args: argparse.Namespace) -> list[Device]:
    """Return the units this invocation addresses, in the order it named them."""
    return list(getattr(args, "devices", None) or [])


def fan_out(device: Device, args: argparse.Namespace, one) -> int:
    """Run ``one(device)`` over every addressed unit and combine the results.

    With one unit this is exactly what the handler did before: no heading, no
    wrapper, the same bytes on standard output.  With several, each section
    gets a heading, and ``--json`` comes back as one object keyed by address.
    """
    return _fan_out(
        devices_of(args) or [device],
        one,
        key=lambda dev: str(dev.slave),
        heading=lambda dev: f"BMS {dev.slave}",
        as_json=bool(getattr(args, "json", False)),
    )


__all__ = ["ALL_IDS", "devices_of", "fan_out", "parse_ids"]
