"""Reading the configuration, and changing it.

A change is a plan first: the registers that differ, old value and new, shown
before anything is written.  ``--dry-run`` stops there and additionally prints
the Modbus frames that would have gone out, which is the thing to look at
before the first write to a battery that matters.
"""

from __future__ import annotations

import argparse

from devicectl.cli.command import Command
from devicectl.cli.output import (
    aborted,
    confirm,
    error,
    print_json,
    print_table,
    read_in,
    shorten,
    write_out,
)

from jkctl import settings as S, values as V
from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.device import Device
from jkctl.modbus import crc16
from jkctl.registers import SETTINGS


def cmd_settings(device: Device, args: argparse.Namespace) -> int:
    """Dispatch one ``jkctl settings`` action."""
    return {
        "show": _show,
        "get": _get,
        "set": _set,
        "list": _list,
        "export": _export,
        "import": _import,
    }[args.action](device, args)


def _show(device: Device, args: argparse.Namespace) -> int:
    """Print every configuration register this unit answers for."""
    values = S.read(device)
    if not values:
        error(f"no unit answered at address {device.slave}")
        return EXIT_ERROR
    if args.json:
        print_json(values)
        return EXIT_OK
    rows = []
    for reg in device.catalog.table(SETTINGS):
        if reg.key not in values:
            continue
        rows.append(
            [
                reg.key,
                reg.title,
                reg.label,
                shorten(V.format_value(reg, values[reg.key])),
                reg.access,
            ]
        )
    print_table(["KEY", "NAME", "JK LABEL", "VALUE", "ACCESS"], rows)
    return EXIT_OK


def _get(device: Device, args: argparse.Namespace) -> int:
    """Print the named settings, one per line."""
    rows = []
    for key in args.keys:
        try:
            reg, index = S.resolve_key(device.catalog, key)
        except S.SettingsError as exc:
            error(str(exc))
            return EXIT_ERROR
        value = device.read_register(reg)
        if index is None:
            rows.append([reg.key, reg.title, V.format_value(reg, value)])
        else:
            element = value[index] if isinstance(value, (list, tuple)) else None
            rows.append(
                [f"{reg.key}[{index}]", reg.title, V.format_element(reg, element)]
            )
    if args.json:
        print_json({key: value for key, _, value in rows})
        return EXIT_OK
    print_table(["KEY", "NAME", "VALUE"], rows)
    return EXIT_OK


def _set(device: Device, args: argparse.Namespace) -> int:
    """Write the named settings, after showing what will change."""
    wanted = S.parse_assignments(device.catalog, args.assignments)
    changes = S.plan(device, wanted)
    if not changes:
        print("Nothing to change; the unit already matches.")
        return EXIT_OK
    print_plan(device, changes, args.dry_run)
    if args.dry_run:
        print("\nDry run; nothing written.")
        return EXIT_OK
    S.apply(device, changes)
    print(f"\nWrote {len(changes)} setting(s).")
    return EXIT_OK


def _list(device: Device, args: argparse.Namespace) -> int:
    """Print the catalog of configuration registers, with ranges and units."""
    rows = []
    for reg in device.catalog.table(SETTINGS):
        if args.pattern and reg not in device.catalog.match(args.pattern):
            continue
        span = "-"
        if reg.minimum is not None or reg.maximum is not None:
            span = (
                f"{V.format_value_bare(reg, reg.minimum)}"
                f"..{V.format_value_bare(reg, reg.maximum)}"
            )
        rows.append(
            [
                reg.key,
                reg.title,
                reg.label,
                reg.unit or "-",
                span,
                V.format_value_bare(reg, reg.default)
                if reg.default is not None
                else "-",
                reg.access,
            ]
        )
    if args.json:
        print_json(
            [
                {
                    "name": r[0],
                    "title": r[1],
                    "label": r[2],
                    "description": device.catalog.find(r[0], SETTINGS).description,
                    "unit": r[3],
                    "range": r[4],
                    "default": r[5],
                    "access": r[6],
                }
                for r in rows
            ]
        )
        return EXIT_OK
    print_table(["KEY", "NAME", "JK LABEL", "UNIT", "RANGE", "DEFAULT", "ACCESS"], rows)
    return EXIT_OK


def _export(device: Device, args: argparse.Namespace) -> int:
    """Write the writable settings to a file, or to stdout when piped."""
    stem = device.identity.get("deviceSN") or f"bms{device.slave}"
    return write_out(
        S.export(device),
        args.file,
        default_name=f"{stem}-settings.json",
        yes=args.yes,
    )


def _import(device: Device, args: argparse.Namespace) -> int:
    """Apply an exported settings file, showing the differences first."""
    text = read_in(args.file)
    wanted = S.load(device.catalog, text)
    changes = S.plan(device, wanted)
    if not changes:
        print("Nothing to change; the unit already matches the file.")
        return EXIT_OK
    print_plan(device, changes, args.dry_run)
    if args.dry_run:
        print("\nDry run; nothing written.")
        return EXIT_OK
    if not args.yes and not confirm(
        f"\nApply {len(changes)} change(s) to BMS {device.slave}?"
    ):
        aborted()
        return EXIT_ERROR
    S.apply(device, changes)
    print(f"\nWrote {len(changes)} setting(s).")
    return EXIT_OK


def print_plan(device: Device, changes: list[S.Change], with_frames: bool) -> int:
    """Print what will change, and under --dry-run the frames that would say so."""
    print(f"{len(changes)} setting(s) to change:\n")
    for change in changes:
        print(f"  {change.describe()}")
        if with_frames:
            print(f"      {_frame_for(device, change)}")
    return EXIT_OK


def _frame_for(device: Device, change: S.Change) -> str:
    """Build the exact FC16 request a change would send, as hex.

    This is what makes ``--dry-run`` worth having: the tool has never written
    to a live BMS, so being able to read the bytes before they go out is the
    difference between a considered first write and a hopeful one.
    """
    reg = change.register
    data = V.encode(reg, change.new)
    address = reg.register(device.bus.addr_offset)
    body = (
        bytes([device.slave, 0x10])
        + address.to_bytes(2, "big")
        + (len(data) // 2).to_bytes(2, "big")
        + bytes([len(data)])
        + data
    )
    return (body + crc16(body).to_bytes(2, "little")).hex(" ")


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    p = sub.add_parser("settings", help="read and change the configuration")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)

    sp = actions.add_parser(
        "show", help="print every configuration register", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = actions.add_parser("get", help="print the named settings", parents=[common])
    sp.add_argument("keys", nargs="+", metavar="NAME")
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = actions.add_parser("set", help="write the named settings", parents=[common])
    sp.add_argument("assignments", nargs="+", metavar="NAME=VALUE")
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="show the changes and the frames, and write nothing",
    )

    sp = actions.add_parser(
        "list", help="list the settings, with ranges and units", parents=[common]
    )
    sp.add_argument("pattern", nargs="?", help="only settings matching this glob")
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = actions.add_parser(
        "export", help="write the settings to a file", parents=[common]
    )
    sp.add_argument("file", nargs="?", help="write here ('-' for stdout)")
    sp.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="overwrite an existing file without asking",
    )

    sp = actions.add_parser("import", help="apply a settings file", parents=[common])
    sp.add_argument("file", help="read here ('-' for stdin)")
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="show the changes and the frames, and write nothing",
    )
    sp.add_argument(
        "-y", "--yes", action="store_true", help="do not ask before writing"
    )


COMMANDS: dict[str, Command] = {
    "settings": Command(cmd_settings, default_action="show"),
}
