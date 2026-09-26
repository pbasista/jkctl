"""Finding a unit on the bus, reading its nameplate, and its address."""

from __future__ import annotations

import argparse
import sys

from devicectl.cli.command import Command, Need
from devicectl.cli.output import (
    aborted,
    confirm,
    error,
    note,
    print_json,
    print_rows,
    print_table,
)

from jkctl import identity as I
from jkctl.cli.exits import EXIT_ABORTED, EXIT_ERROR, EXIT_OK
from jkctl.cli.fanout import fan_out
from jkctl.device import Device, scan
from jkctl.modbus import Bus
from jkctl.registers import SETTINGS, Catalog

# A JK board's address is a four-position DIP switch, so 0..15 is every
# address one can be set to.
MAX_DIP_ADDRESS = 15


def cmd_scan(bus: Bus, args: argparse.Namespace) -> int:
    """Sweep the bus for units and print what answers."""
    target = args.target
    print(
        f"scanning {target.port} at {target.baud} baud, addresses 0-{args.scan_range}\n"
    )
    rows = []
    catalog = Catalog()
    for slave, dev in scan(bus, range(args.scan_range + 1), catalog):
        ident = I.read(dev)
        rows.append(
            [
                str(slave),
                ident.model,
                ident.software_version,
                ident.hardware_version,
                ident.serial_number,
            ]
        )
    if not rows:
        print(
            "No units answered.  Check A/B polarity, the port, the baud rate\n"
            "and the DIP-switch address, then try:  jkctl probe"
        )
        return EXIT_ERROR
    print_table(["ID", "MODEL", "SOFTWARE", "HARDWARE", "SERIAL"], rows)
    print(f"\n{len(rows)} unit(s) found.")
    return EXIT_OK


def cmd_info(device: Device, args: argparse.Namespace) -> int:
    """Print each addressed unit's nameplate."""
    return fan_out(device, args, lambda dev: _info_one(dev, args))


def _info_one(device: Device, args: argparse.Namespace) -> int:
    """Print one unit's model, versions, serial number and port protocols."""
    ident = I.read(device)
    if not ident.model:
        error(f"no unit answered at address {device.slave}")
        return EXIT_ERROR
    if args.json:
        print_json(
            {
                "model": ident.model,
                "hardware_version": ident.hardware_version,
                "software_version": ident.software_version,
                "serial_number": ident.serial_number,
                "first_power_on": ident.first_power_on,
                "max_cells": ident.max_cells,
                "power_on_times": ident.power_on_times,
                "total_run_time_s": ident.total_run_time_s,
                "protocol_version": ident.protocol_version,
                "ports": [
                    {"port": label, "number": number, "protocol": name}
                    for label, number, name in ident.ports
                ],
            }
        )
        return EXIT_OK
    print_rows(
        f"BMS {device.slave}",
        [
            ("model", ident.model),
            ("hardware version", ident.hardware_version),
            ("software version", ident.software_version),
            ("serial number", ident.serial_number),
            ("first power-on", ident.first_power_on),
            ("max cells", _or_dash(ident.max_cells)),
            ("power-on count", _or_dash(ident.power_on_times)),
            ("total run time", ident.total_run_time),
            ("protocol version", _or_dash(ident.protocol_version)),
        ],
    )
    if ident.ports:
        print()
        print_table(
            ["PORT", "NO.", "PROTOCOL"],
            [[label, str(number), name] for label, number, name in ident.ports],
        )
    return EXIT_OK


def cmd_address(device: Device, args: argparse.Namespace) -> int:
    """Show or change the address the unit answers on."""
    return {"show": _address_show, "set": _address_set}[args.action](device, args)


def _address_show(device: Device, args: argparse.Namespace) -> int:
    """Print the stored address beside the one that actually answered."""
    stored = device.read("devAddr", SETTINGS)
    if args.json:
        print_json({"answering": device.slave, "devAddr": _int(stored)})
        return EXIT_OK
    print_rows(
        f"BMS {device.slave}",
        [
            ("answering at", str(device.slave)),
            ("devAddr holds", _or_dash(_int(stored))),
        ],
    )
    if stored is not None and _int(stored) != device.slave:
        note(
            "the two disagree: the DIP switches decide which address answers, "
            "and devAddr is what the board has stored"
        )
    return EXIT_OK


def _address_set(device: Device, args: argparse.Namespace) -> int:
    """Write devAddr, after saying what it will cost.

    This is the one setting whose write moves the thing being written: after
    it lands, ``--id`` has to name the new address.  It is a plain register in
    the settings table, so ``settings set devAddr=`` reaches it too -- but a
    setting that changes who you are talking to deserves to be asked about
    rather than to arrive in the middle of a list of forty.
    """
    if not 0 <= args.address <= MAX_DIP_ADDRESS:
        error(f"an address is 0..{MAX_DIP_ADDRESS} (a four-position DIP switch)")
        return EXIT_ERROR
    if not args.yes:
        print(
            f"About to set devAddr on BMS {device.slave} to {args.address}.\n"
            "Afterwards this unit answers there, so `--id "
            f"{args.address}` is how to reach it -- and the DIP switches on "
            "the board are what actually select the address on most models, "
            "so check them before relying on this.",
            file=sys.stderr,
        )
        if not confirm("Change the address?"):
            aborted()
            return EXIT_ABORTED
    device.write("devAddr", args.address, SETTINGS)
    print(f"devAddr is now {args.address}.")
    return EXIT_OK


def _int(value) -> int | None:
    """Read a decoded register as an int, or None if the board did not map it."""
    return None if value is None else int(value)


def _or_dash(value) -> str:
    """Render a field this board does not map as a dash, not as "None"."""
    return "-" if value is None else str(value)


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    sp = sub.add_parser("scan", help="find the BMS units on the bus", parents=[common])
    sp.add_argument(
        "--scan-range",
        type=int,
        default=MAX_DIP_ADDRESS,
        help=f"highest address to probe (default {MAX_DIP_ADDRESS})",
    )

    sp = sub.add_parser(
        "info", help="show a unit's model, versions and serial", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a report")

    p = sub.add_parser("address", help="the address this unit answers on")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    sp = actions.add_parser(
        "show", help="the stored address and the answering one", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a report")
    sp = actions.add_parser("set", help="change the stored address", parents=[common])
    sp.add_argument("address", type=int, metavar="ID")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask")


COMMANDS: dict[str, Command] = {
    "scan": Command(cmd_scan, Need.LINK),
    "info": Command(cmd_info),
    "address": Command(cmd_address, default_action="show"),
}
