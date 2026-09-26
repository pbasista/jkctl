"""``jkctl ui`` -- serve the web interface, and what ``jkctl`` does by itself.

The only command that opens no bus of its own: the server makes one when a
browser asks for something that needs it, and gives it back when nobody is
looking.  That matters more here than it would over a network -- a held
``/dev/ttyUSB0`` is a locked door, and a terminal running ``jkctl status``
cannot get past it.

Running with no port chosen is deliberately not an error.  The page opens on
a picker listing the serial ports this machine actually has, with what the
system says each one is, and scans whichever is chosen: a first run should not
need a configuration file to exist.

``--simulate`` needs no port at all: it puts a bank of simulated boards on an
in-memory bus and serves the page against them, so the whole interface can be
seen, and screenshot, on a machine with no battery anywhere near it.
"""

from __future__ import annotations

import argparse
import os

from devicectl.cli.command import Command, Need
from devicectl.cli.output import error

from jkctl.cli.exits import EXIT_ERROR
from jkctl.cli.target import resolve_target
from jkctl.config import load_config
from jkctl.device import Device


def _chosen_port(args: argparse.Namespace, config) -> bool:
    """Whether a port was actually named, rather than merely defaulted to.

    ``resolve_target`` always produces a port, because every other command
    needs one; here the difference between "the user said /dev/ttyUSB1" and
    "nobody said anything and the default is /dev/ttyUSB0" decides whether the
    page opens on a dashboard or on a picker.  A default that happens to
    exist on this machine is taken as chosen -- but only when it is the only
    adapter there is.  A Raspberry Pi with three USB adapters plugged in has
    three ports the battery could be on, and silently taking the first of them
    is exactly the guess the picker exists to avoid.
    """
    if args.port or args.device or config.port:
        return True
    from jkctl import modbus as M

    if not os.path.exists(M.DEFAULT_PORT):
        return False
    return _adapter_count() <= 1


def _adapter_count() -> int:
    """How many ports on this machine look like a serial adapter.

    Counts what the picker would call ``likely``, so the two agree on what
    counts as a candidate: an onboard ``/dev/ttyS0`` no more makes the choice
    ambiguous here than it does there.  If the ports cannot be enumerated at
    all the answer is 1, which keeps the old "the default exists, use it"
    behaviour rather than opening a picker that would list nothing.
    """
    from jkctl.web.api import list_serial_ports

    try:
        ports = list_serial_ports()
    except Exception:  # noqa: BLE001  # pragma: no cover - a broken pyserial backend
        return 1
    if not ports:
        return 1
    return sum(1 for port in ports if port["likely"])


def _simulated(count: int) -> tuple:
    """Return the target and bus factory for a bank of simulated boards.

    The boards live in this process on an in-memory bus, so nothing is opened
    and nothing is held.  The factory is called again whenever the worker
    reconnects, and hands back the same bank each time: a simulated pack that
    forgot its settings every time the page idled out would be a poor thing to
    demonstrate with.
    """
    from jkctl import modbus as M, simulator
    from jkctl.web.session import Target

    slaves = tuple(range(1, count + 1))
    link = simulator.bank(slaves)
    target = Target(port="simulated", slaves=slaves, label=f"{count} simulated")
    return target, lambda _t: M.Bus(link=link, timeout=0.2, retries=0)


def cmd_ui(device: Device | None, args: argparse.Namespace) -> int:
    """Serve the web UI (no bus session: the server opens its own)."""
    from devicectl.web.server import parse_listen

    from jkctl.web import DEFAULT_HOST, DEFAULT_PORT, serve
    from jkctl.web.session import Target

    config = load_config(args.config)
    try:
        host, port = parse_listen(
            args.listen, default_host=DEFAULT_HOST, default_port=DEFAULT_PORT
        )
    except ValueError:
        error(f"cannot read --listen '{args.listen}'")
        return EXIT_ERROR

    # Resolved whether or not a port was named, because the rest of it is
    # needed either way: a port chosen in the page later is opened with the
    # configuration file's baud, timeout, retries and frame offset, the same
    # ones `--port` would have used.
    try:
        resolved = resolve_target(args, config)
    except ValueError as exc:
        error(str(exc))
        return EXIT_ERROR
    defaults = Target(
        port=resolved.port,
        baud=resolved.baud,
        timeout=resolved.timeout,
        retries=resolved.retries,
        addr_offset=resolved.addr_offset,
    )

    open_bus = None
    note = ""
    target = None
    if args.simulate:
        target, open_bus = _simulated(args.simulate)
        note = (
            f"simulated: {args.simulate} fake board(s) in this process, and no "
            "serial port open -- nothing here is a battery"
        )
    elif _chosen_port(args, config):
        target = Target(
            port=resolved.port,
            baud=resolved.baud,
            timeout=resolved.timeout,
            retries=resolved.retries,
            addr_offset=resolved.addr_offset,
            slaves=resolved.slaves,
            label=args.device or "",
        )

    return serve(
        target=target,
        defaults=defaults,
        host=host,
        port=port,
        token=None if args.token is None else args.token,
        read_only=args.read_only,
        open_browser=not args.no_browser,
        poll_interval=args.interval,
        idle_timeout=args.idle_timeout,
        allow_hosts=tuple(args.allow_host or ()),
        firmware_dir=args.firmware_dir or "",
        debug=args.debug,
        open_bus=open_bus,
        note=note,
    )


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    from jkctl.web import DEFAULT_HOST, DEFAULT_PORT

    sp = sub.add_parser(
        "ui",
        help="serve the web interface (this is what jkctl does with no arguments)",
        parents=[common],
    )
    sp.add_argument(
        "--listen",
        default=f"{DEFAULT_HOST}:{DEFAULT_PORT}",
        metavar="HOST[:PORT]",
        help=f"where to listen (default {DEFAULT_HOST}:{DEFAULT_PORT})",
    )
    sp.add_argument(
        "--no-browser", action="store_true", help="do not open or raise a browser tab"
    )
    sp.add_argument(
        "--read-only",
        action="store_true",
        help="refuse every write server-side, so a page can be shared safely",
    )
    sp.add_argument(
        "--token",
        default=None,
        metavar="TOKEN",
        help="pin the access token (one is generated off loopback)",
    )
    sp.add_argument(
        "--no-token",
        dest="token",
        action="store_const",
        const="",
        help="serve without a token, on a network you trust",
    )
    sp.add_argument(
        "--allow-host",
        action="append",
        metavar="NAME",
        help="also answer to this hostname (repeatable)",
    )
    sp.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="S",
        help="seconds between live refreshes (default 3)",
    )
    sp.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        metavar="S",
        help="seconds before an unused port is given back (default 45)",
    )
    sp.add_argument(
        "--firmware-dir",
        default=None,
        metavar="DIR",
        help="a directory of .jkbms files to offer in the firmware library",
    )
    sp.add_argument(
        "--simulate",
        type=int,
        nargs="?",
        const=3,
        default=0,
        metavar="N",
        help="serve against N simulated boards instead of a serial port "
        "(default 3), for a demonstration on a machine with no hardware",
    )
    sp.add_argument("--debug", action="store_true", help="log every request")


COMMANDS: dict[str, Command] = {"ui": Command(cmd_ui, Need.NOTHING)}
