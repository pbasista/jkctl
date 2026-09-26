"""Diagnostics: characterise a bus, run a fake BMS, and check a unit over.

``probe`` is what to run when nothing answers, or when a board behaves unlike
the one this tool was measured against.  ``simulate`` serves the other half of
a socat pty pair so every command can be exercised with no hardware at all.
``doctor`` is composition over what the other commands already read.
"""

from __future__ import annotations

import argparse
import json
import sys

from devicectl.cli.command import Command, Need
from devicectl.cli.output import print_json, print_table

from jkctl import doctor as D, probe as PR, simulator
from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.cli.fanout import fan_out
from jkctl.cli.target import resolve_target
from jkctl.config import load_config
from jkctl.device import Device
from jkctl.modbus import Bus


def cmd_probe(bus: Bus, args: argparse.Namespace) -> int:
    """Characterise the bus and write a report someone else can read."""
    target = args.target
    # probe opens its own ports per baud rate, so hand back the one main() opened.
    bus.close()
    log = PR.Log(args.out + ".log" if args.out else None)
    trace = (lambda d, b: log("   %s %s" % (d, b.hex(" ")))) if args.trace else None
    report = PR.run(
        target.port,
        bauds=[target.baud] if args.baud else None,
        timeout=target.timeout,
        retries=target.retries,
        trace=trace,
        log=log,
        probe_writes=args.probe_writes,
    )
    if args.out:
        with open(args.out + ".json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        log.save()
        print(f"\nwrote {args.out}.json and {args.out}.log")
    return EXIT_OK if report.get("transport") == "modbus" else EXIT_ERROR


def cmd_simulate(device: Device | None, args: argparse.Namespace) -> int:
    """Serve a fake BMS on a serial port until interrupted."""
    config = load_config(args.config)
    target = resolve_target(args, config)
    sim = simulator.Sim(
        target.port, target.baud, target.slave, target.addr_offset, args.save_image
    )
    try:
        sim.run()
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
    return EXIT_OK


def cmd_doctor(device: Device, args: argparse.Namespace) -> int:
    """Read every addressed unit over and report anything that looks wrong."""
    return fan_out(device, args, lambda dev: _doctor_one(dev, args))


def _doctor_one(device: Device, args: argparse.Namespace) -> int:
    """Check one unit."""
    report = D.check(device)
    findings = report.sorted()
    if args.json:
        print_json(
            {
                "findings": [
                    {
                        "severity": f.severity,
                        "area": f.area,
                        "detail": f.detail,
                        "fix": f.fix,
                    }
                    for f in findings
                ],
                "unavailable": report.unavailable,
            }
        )
    else:
        if findings:
            print_table(
                ["SEVERITY", "AREA", "DETAIL", "FIX"],
                [[f.severity, f.area, f.detail, f.fix or ""] for f in findings],
            )
        else:
            print("Nothing to report; the unit reads as healthy.")
        for line in report.unavailable:
            print(f"note: could not check -- {line}", file=sys.stderr)
    return EXIT_OK if report.ok else EXIT_ERROR


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    sp = sub.add_parser(
        "probe", help="characterise the bus and dump every table", parents=[common]
    )
    sp.add_argument(
        "--out",
        default="probe-result",
        help="write <OUT>.json and <OUT>.log (default probe-result)",
    )
    sp.add_argument(
        "--probe-writes",
        action="store_true",
        help="also measure the write quantity cap; this WRITES, though "
        "only ever the bytes it has just read back",
    )

    sp = sub.add_parser(
        "simulate", help="serve a fake BMS on a serial port", parents=[common]
    )
    sp.add_argument("--save-image", help="write a received firmware image here")

    sp = sub.add_parser(
        "doctor", help="read a unit over and report what looks wrong", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")


COMMANDS: dict[str, Command] = {
    "probe": Command(cmd_probe, Need.LINK),
    "simulate": Command(cmd_simulate, Need.NOTHING),
    "doctor": Command(cmd_doctor),
}
