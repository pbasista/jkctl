"""What the pack is doing right now: the summary, the cells, and the alarms.

Every reading here is cheap enough to repeat, which is why all three sit
comfortably in a watch loop, and why ``log`` can sample them into a file.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from datetime import datetime
from typing import Any

from devicectl.cli.command import Command
from devicectl.cli.output import (
    CLOCK_FORMAT,
    error,
    print_json,
    print_rows,
    print_sections,
    print_table,
    write_out,
)

from jkctl import runtime as R
from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.cli.fanout import fan_out
from jkctl.device import Device
from jkctl.registers import RUNTIME

# `status --watch` with no number.  The pack changes slowly and a whole-table
# read is 40-odd Modbus transactions, so a second would be pointless traffic.
DEFAULT_WATCH_INTERVAL_S = 2.0

# How many cell voltages to put on one line.
CELLS_PER_ROW = 8

# The columns `jkctl log` samples, in order.  Kept short on purpose: a log is
# for plotting, and the fields that move are the ones worth carrying.
LOG_FIELDS = (
    "batVol",
    "batCurrent",
    "batWatt",
    "socRelativeStateOfCharge",
    "socCapabilityRemain",
    "cellVolAve",
    "maxVoltDelta",
    "equCurrent",
    "tempMos",
    "batTemp1",
    "batTemp2",
)


def cmd_status(device: Device, args: argparse.Namespace) -> int:
    """Show the pack summary, optionally redrawing it until Ctrl+C."""
    return _watch(device, args, _print_status)


def cmd_cells(device: Device, args: argparse.Namespace) -> int:
    """Show the per-cell voltages and balance-wire resistances."""
    return _watch(device, args, _print_cells)


def cmd_alarms(device: Device, args: argparse.Namespace) -> int:
    """Show every protection and warning bit the BMS currently raises."""
    return fan_out(device, args, lambda dev: _print_alarms(dev, args))


def _print_alarms(device: Device, args: argparse.Namespace) -> int:
    """Print one unit's raised alarm bits."""
    reading = R.read(device)
    raised = reading.alarms
    if args.json:
        print_json(
            [
                {"register": a.register, "bit": a.bit, "name": a.name, "state": a.state}
                for a in raised
            ]
        )
        return EXIT_OK
    if not raised:
        print("No alarms raised.")
        return EXIT_OK
    print_table(
        ["REGISTER", "BIT", "ALARM", "STATE"],
        [[a.register, str(a.bit), a.name, a.state] for a in raised],
    )
    # An alarm is the answer to the question, not a failure of the command.
    return EXIT_OK


def cmd_log(device: Device, args: argparse.Namespace) -> int:
    """Sample the runtime table into a CSV or JSON file until Ctrl+C or --count."""
    catalog = device.catalog
    columns = [c for c in LOG_FIELDS if catalog.get(c, RUNTIME) is not None]
    rows: list[dict[str, Any]] = []
    taken = 0
    try:
        while args.count is None or taken < args.count:
            reading = R.read(device)
            row = {"time": datetime.now().strftime(CLOCK_FORMAT)}
            row.update({key: reading.get(key) for key in columns})
            rows.append(row)
            taken += 1
            if args.follow_output:
                print(_csv([row], columns, header=taken == 1), end="")
            elif sys.stderr.isatty():
                print(f"\r  sample {taken}", end="", file=sys.stderr, flush=True)
            if args.count is not None and taken >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    if sys.stderr.isatty() and not args.follow_output:
        print(file=sys.stderr)
    if args.follow_output:
        return EXIT_OK
    payload = (
        _json_rows(rows) if args.format == "json" else _csv(rows, columns, header=True)
    )
    return _write_out(payload, args, device)


# --- rendering ------------------------------------------------------------------------


def _watch(device: Device, args: argparse.Namespace, render) -> int:
    """Run ``render`` over every addressed unit once, or on a ``--watch`` loop."""

    def once() -> int:
        return fan_out(device, args, lambda dev: render(dev, args))

    if not args.watch:
        return once()
    live = sys.stdout.isatty()
    while True:
        if live:
            # Home the cursor and clear below, so the reading stays in one
            # place instead of scrolling past.
            sys.stdout.write("\033[H\033[J")
        rc = once()
        if rc != EXIT_OK:
            return rc
        print(
            f"\n  updated {datetime.now().strftime(CLOCK_FORMAT)}, every "
            f"{args.watch:g}s -- press Ctrl+C to stop"
        )
        time.sleep(args.watch)


def _print_status(device: Device, args: argparse.Namespace) -> int:
    """Print one pack summary."""
    reading = R.read(device)
    if not reading.fields:
        error(f"no unit answered at address {device.slave}")
        return EXIT_ERROR
    if args.json:
        doc = {
            key: reading.get(key)
            for key, _ in R.SUMMARY_FIELDS
            if key in reading.fields
        }
        doc["alarms"] = [a.name for a in reading.alarms]
        print_json(doc)
        return EXIT_OK
    print_sections(f"BMS {device.slave}", reading.summary())
    alarms = reading.alarms
    if alarms:
        print("\n  alarms:")
        for alarm in alarms:
            print(f"    {alarm.name}: {alarm.state}")
    return EXIT_OK


def _print_cells(device: Device, args: argparse.Namespace) -> int:
    """Print the per-cell table."""
    reading = R.read(device)
    volts = reading.cell_voltages
    if not volts:
        error("the unit reported no cell voltages")
        return EXIT_ERROR
    res = reading.cell_resistances
    if args.json:
        print_json(
            {
                "cells": [
                    {
                        "cell": i + 1,
                        "voltage_v": v,
                        "wire_resistance_ohm": res[i] if i < len(res) else None,
                    }
                    for i, v in enumerate(volts)
                ],
                "average_v": reading.get("cellVolAve"),
                "delta_v": reading.get("maxVoltDelta"),
                "highest_cell": _cell_number(reading.get("celMaxVol")),
                "lowest_cell": _cell_number(reading.get("celMinVol")),
                "balance_current_a": reading.get("equCurrent"),
            }
        )
        return EXIT_OK
    print(f"BMS {device.slave} -- {len(volts)} cells\n")
    print("  voltages (V):")
    for i in range(0, len(volts), CELLS_PER_ROW):
        chunk = volts[i : i + CELLS_PER_ROW]
        print(
            f"    {i + 1:2d}-{i + len(chunk):2d}  "
            + " ".join(f"{v:.3f}" for v in chunk)
        )
    if res:
        print("\n  wire resistance (Ω):")
        for i in range(0, len(res), CELLS_PER_ROW):
            chunk = res[i : i + CELLS_PER_ROW]
            print(
                f"    {i + 1:2d}-{i + len(chunk):2d}  "
                + " ".join(f"{v:.3f}" for v in chunk)
            )
    rows = dict(reading.summary()).get("Cells", [])
    if rows:
        print()
        print_rows("", rows)
    return EXIT_OK


def _cell_number(index: Any) -> int | None:
    """Count a cell index from the runtime table from one, as the rest does."""
    return None if index is None else int(index) + 1


def _csv(rows: list[dict[str, Any]], columns: list[str], *, header: bool) -> str:
    """Render sampled rows as CSV, one column per sampled register."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    if header:
        writer.writerow(["time", *columns])
    for row in rows:
        writer.writerow([row.get("time"), *(row.get(c) for c in columns)])
    return buf.getvalue()


def _json_rows(rows: list[dict[str, Any]]) -> str:
    """Render sampled rows as JSON."""
    import json

    return json.dumps(rows, indent=2) + "\n"


def _write_out(payload: str, args: argparse.Namespace, device: Device) -> int:
    """Write a log to the named file, to a device-named default, or to stdout.

    On a terminal with no ``--file``, a name is invented from the unit's serial
    number: someone watching a battery wants a file afterwards.  Piped, it goes
    to stdout, so ``jkctl log --count 10 | …`` works.
    """
    stem = device.identity.get("deviceSN") or f"bms{device.slave}"
    return write_out(
        payload,
        args.file,
        default_name=f"{stem}-log.{args.format}",
        yes=args.yes,
    )


def _add_watch(sp: argparse.ArgumentParser) -> None:
    """Add the shared --watch option."""
    sp.add_argument(
        "--watch",
        nargs="?",
        type=float,
        const=DEFAULT_WATCH_INTERVAL_S,
        metavar="S",
        help=f"keep re-reading and redrawing every S seconds until "
        f"Ctrl+C (default {DEFAULT_WATCH_INTERVAL_S:g})",
    )


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    sp = sub.add_parser(
        "status", help="show what the pack is doing right now", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a report")
    _add_watch(sp)

    sp = sub.add_parser(
        "cells", help="show per-cell voltages and wire resistances", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a report")
    _add_watch(sp)

    sp = sub.add_parser(
        "alarms", help="show the protection and warning bits raised", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = sub.add_parser(
        "log", help="sample the runtime data into a file", parents=[common]
    )
    sp.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_WATCH_INTERVAL_S,
        metavar="S",
        help="seconds between samples (default 2)",
    )
    sp.add_argument("--count", type=int, help="stop after this many samples")
    sp.add_argument("--file", help="write here ('-' for stdout)")
    sp.add_argument(
        "--format",
        choices=("csv", "json"),
        default="csv",
        help="output format (default csv)",
    )
    sp.add_argument(
        "--follow-output",
        action="store_true",
        help="stream each sample as it is taken instead of writing at the end",
    )
    sp.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="overwrite an existing file without asking",
    )


COMMANDS: dict[str, Command] = {
    "status": Command(cmd_status),
    "cells": Command(cmd_cells),
    "alarms": Command(cmd_alarms),
    "log": Command(cmd_log),
}
