"""Every register of every table, with what it currently reads.

``settings`` shows the configuration table, ``status`` and ``cells`` show the
handful of runtime fields worth a summary, and ``info`` shows the nameplate.
Between them they leave two-thirds of what the device answers for undisplayed:
the PWM duty cycles, the relay and pre-discharge states, the six
protection-release countdowns, the sensor-presence bits, the MCU id.

This command shows all of it -- name, description, value, unit, range,
factory default, access and the bytes it arrived as -- and takes a glob, so
looking for something you half-remember the name of is one line rather than a
trip through three commands and a register map.
"""

from __future__ import annotations

import argparse

from devicectl.cli.command import Command
from devicectl.cli.output import error, print_json, print_table, shorten

from jkctl import values as V
from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.cli.fanout import fan_out
from jkctl.device import Device
from jkctl.registers import TABLE_LABELS, TABLES, Register


def cmd_registers(device: Device, args: argparse.Namespace) -> int:
    """Print every register that matches, with its value, for each unit."""
    return fan_out(device, args, lambda dev: _one(dev, args))


def _read_values(
    device: Device, wanted: list[Register], want_raw: bool
) -> tuple[dict[str, object], dict[str, bytes]]:
    """Read every wanted register, one round trip per table it spans."""
    values: dict[str, object] = {}
    raw: dict[str, bytes] = {}
    for table in TABLES:
        group = [reg for reg in wanted if reg.table == table]
        if not group:
            continue
        values.update(device.read_fields(group))
        if want_raw:
            frame, _ = device.read_frame_bytes(group)
            for reg in group:
                if reg.key in values:
                    raw[reg.key] = frame[reg.field.off : reg.field.off + reg.size]
    return values, raw


def _registers_json(
    device: Device,
    wanted: list[Register],
    values: dict[str, object],
    raw: dict[str, bytes],
) -> None:
    """Print every register as a document, whether it answered or not."""
    print_json(
        [
            {
                "name": reg.key,
                "table": reg.table,
                "title": reg.title,
                "label": reg.label,
                "description": reg.description,
                "value": values.get(reg.key),
                "unit": reg.unit or None,
                "minimum": reg.minimum,
                "maximum": reg.maximum,
                "default": reg.default,
                "access": reg.access,
                "register": f"0x{reg.register(device.bus.addr_offset):04X}",
                "bytes": raw[reg.key].hex() if reg.key in raw else None,
                "answered": reg.key in values,
            }
            for reg in wanted
        ]
    )


def _registers_table(
    device: Device,
    wanted: list[Register],
    values: dict[str, object],
    raw: dict[str, bytes],
    want_raw: bool,
) -> None:
    """Print the registers as a table, with the wire bytes when asked."""
    headers = ["KEY", "TABLE", "NAME", "JK LABEL", "VALUE", "ACCESS"]
    if want_raw:
        headers.insert(5, "REG")
        headers.insert(6, "BYTES")
    rows = []
    for reg in wanted:
        row = [
            reg.key,
            TABLE_LABELS[reg.table],
            reg.title,
            reg.label,
            shorten(V.format_value(reg, values.get(reg.key)))
            if reg.key in values
            else "(not mapped)",
            reg.access,
        ]
        if want_raw:
            row.insert(5, f"0x{reg.register(device.bus.addr_offset):04X}")
            row.insert(6, raw.get(reg.key, b"").hex(" ") or "-")
        rows.append(row)
    print_table(headers, rows)


def _one(device: Device, args: argparse.Namespace) -> int:
    """Print one unit's registers."""
    wanted = _select(device, args)
    if not wanted:
        error(f"nothing matches {args.pattern!r} (the names are shown without one)")
        return EXIT_ERROR
    values, raw = _read_values(device, wanted, args.raw)
    if args.json:
        _registers_json(device, wanted, values, raw)
    else:
        _registers_table(device, wanted, values, raw, args.raw)
    return EXIT_OK


def _select(device: Device, args: argparse.Namespace) -> list[Register]:
    """Pick the registers this invocation is about, in table then wire order."""
    tables = [args.table] if args.table else list(TABLES)
    chosen = [reg for table in tables for reg in device.catalog.table(table)]
    if args.pattern:
        # By (table, name): a Register carries its datasource Field, which is
        # not hashable, and two tables can hold the same name.
        matching = {(reg.table, reg.key) for reg in device.catalog.match(args.pattern)}
        chosen = [reg for reg in chosen if (reg.table, reg.key) in matching]
    if args.writable:
        chosen = [reg for reg in chosen if reg.writable]
    return chosen


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    sp = sub.add_parser(
        "registers",
        help="every register of every table, with its value",
        parents=[common],
    )
    sp.add_argument(
        "pattern",
        nargs="?",
        metavar="PATTERN",
        help="a glob matched against the name and the description, e.g. 'temp*'",
    )
    sp.add_argument(
        "--table",
        choices=sorted(TABLES),
        help="only one table: 01 settings, 02 runtime, 03 device info",
    )
    sp.add_argument(
        "--writable", action="store_true", help="only the registers JK marks RW"
    )
    sp.add_argument(
        "--raw", action="store_true", help="also show the register and its bytes"
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")


COMMANDS: dict[str, Command] = {"registers": Command(cmd_registers)}
