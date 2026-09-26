"""Which bus a command opens, and which unit on it it talks to.

The answer can come from three places -- the command line, a device named in
``jk.toml``, or the file's own defaults -- and :func:`resolve_target` applies
them in that order.  :func:`open_bus` then opens the port, reporting on stderr
rather than raising, because every caller would only have printed the same
thing.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from devicectl.cli.output import error
from devicectl.cli.target import first_set

from jkctl import modbus as M
from jkctl.config import Config, DeviceConfig, load_config
from jkctl.device import Device
from jkctl.registers import Catalog

# The address every JK board leaves the factory on, and the one the vendor's
# own configuration file names.
DEFAULT_ID = M.DEFAULT_SLAVE
DEFAULT_RETRIES = 2


@dataclass(frozen=True)
class Target:
    """Everything needed to open the bus and address the units on it."""

    port: str
    baud: int
    slave: int
    timeout: float
    retries: int
    addr_offset: int
    slaves: tuple[int, ...] = ()
    """Every address the command line named.  Empty means "sweep the bus"."""


def resolve_target(args: argparse.Namespace, config: Config) -> Target:
    """Resolve the port and address for a command.

    Precedence: command line > ``[devices.<name>]`` table > top-level config
    defaults > the factory values.
    """
    dev: DeviceConfig | None = None
    if args.device:
        dev = config.device(args.device)
        if dev is None:
            raise ValueError(
                f"no device named {args.device!r} in "
                f"{config.path or 'the configuration'}"
            )
    wanted = _wanted_ids(args, dev, config)
    return Target(
        port=first_set(
            args.port, dev.port if dev else None, config.port, default=M.DEFAULT_PORT
        ),
        baud=first_set(
            args.baud, dev.baud if dev else None, config.baud, default=M.DEFAULT_BAUD
        ),
        slave=wanted[0] if wanted else DEFAULT_ID,
        slaves=tuple(wanted),
        timeout=first_set(
            args.timeout,
            dev.timeout if dev else None,
            config.timeout,
            default=M.QUERY_TIMEOUT,
        ),
        retries=first_set(
            args.retries,
            dev.retries if dev else None,
            config.retries,
            default=DEFAULT_RETRIES,
        ),
        addr_offset=first_set(
            args.addr_offset,
            dev.addr_offset if dev else None,
            config.addr_offset,
            default=M.FRAME_ADDR_OFFSET,
        ),
    )


def _wanted_ids(
    args: argparse.Namespace, dev: DeviceConfig | None, config: Config
) -> list[int]:
    """Resolve ``--id`` into the list of addresses, or ``[]`` for "all of them".

    ``--id`` used to be one integer.  It now takes a list, a range or ``all``,
    because a bank of four batteries on one bus is an ordinary installation
    and reading it a battery at a time is four commands.  The configuration
    file keeps a single id per device, which is what a named device is.
    """
    from jkctl.cli.fanout import parse_ids

    if args.id is not None:
        parsed = parse_ids(args.id)
        return parsed if parsed is not None else []
    named = first_set(dev.id if dev else None, config.id, default=DEFAULT_ID)
    return [named]


def open_bus(args: argparse.Namespace) -> tuple[M.Bus, Target] | None:
    """Open the serial port a command needs (or report why not)."""
    try:
        target = resolve_target(args, load_config(args.config))
    except ValueError as exc:
        error(str(exc))
        return None
    log = _tracer() if args.trace else None
    try:
        bus = M.Bus(
            target.port,
            target.baud,
            timeout=target.timeout,
            addr_offset=target.addr_offset,
            log=log,
            retries=target.retries,
        )
    except OSError as exc:
        error(f"cannot open {target.port}: {exc}")
        return None
    if args.trace:
        bus.note = lambda said: print(f"  -- {said}", file=sys.stderr)
    return bus, target


def open_device(args: argparse.Namespace) -> tuple[M.Bus, Device] | None:
    """Open the bus and bind to the addressed unit(s), or report why not.

    ``args.devices`` carries every unit the command line named, in that order;
    the one returned is the first of them, which is the whole story for the
    commands that address one board.  ``--id all`` sweeps the bus here, once,
    so a fan-out read does not scan again per command.
    """
    opened = open_bus(args)
    if opened is None:
        return None
    bus, target = opened
    args.target = target
    catalog = Catalog()
    if target.slaves:
        devices = [Device(bus, slave, catalog) for slave in target.slaves]
    else:
        from jkctl.device import scan

        devices = [dev for _slave, dev in scan(bus, range(16), catalog)]
        if not devices:
            error(f"no unit answered on {target.port}")
            return None
        print(
            f"note: --id all found {len(devices)} unit(s): "
            + ", ".join(str(d.slave) for d in devices),
            file=sys.stderr,
        )
    args.devices = devices
    return bus, devices[0]


def device_target(args: argparse.Namespace) -> "Target":
    """Return the target :func:`open_device` resolved for this invocation."""
    return args.target


def _tracer():
    """Return a --trace logger that hex-dumps every frame to stderr.

    A read that came back empty is printed too, and said in words: a
    timeout leaves nothing to hex-dump, and a TX with no RX under it looks
    exactly like the end of the trace rather than like the failure it is.
    """
    return lambda direction, data: print(
        f"  {direction} {data.hex(' ') if data else '(no reply)'}", file=sys.stderr
    )


__all__ = [
    "DEFAULT_ID",
    "device_target",
    "DEFAULT_RETRIES",
    "Target",
    "first_set",
    "open_bus",
    "open_device",
    "resolve_target",
]
