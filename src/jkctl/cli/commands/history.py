"""The board's own stored fault records, and the names for what they carry.

Two commands, and they are deliberately different in confidence.

``jkctl log-codes`` needs no hardware and is certain: it prints the map the
vendor's application uses to turn a stored record's code into a sentence,
extracted from the application itself.

``jkctl history`` needs hardware and is not: the records exist -- the runtime
table counts them, and the datasource describes their layout to the byte --
but which register window serves them over Modbus, if any, is an open
question.  The command reads the window the pattern of the four documented
frames suggests, decodes whatever comes back, and says plainly when a board
does not answer there rather than pretending the board has no history.
"""

from __future__ import annotations

import argparse

from devicectl.cli.command import Command, Need
from devicectl.cli.output import CLOCK_FORMAT, error, note, print_json, print_table

from jkctl import history as H, logcodes
from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.cli.fanout import fan_out
from jkctl.device import Device


def cmd_history(device: Device, args: argparse.Namespace) -> int:
    """Read each addressed unit's stored fault records."""
    return fan_out(device, args, lambda dev: _one(dev, args))


def _one(device: Device, args: argparse.Namespace) -> int:
    """Read one unit's records."""
    try:
        records = H.read(device, args.base)
    except H.HistoryError as exc:
        error(str(exc))
        return EXIT_ERROR
    if args.json:
        print_json(
            [
                {
                    "index": r.index,
                    "code": r.code,
                    "name": r.name,
                    "when": r.when.isoformat() if r.when else None,
                    "switches": r.closed,
                    "pack_v": r.pack_v,
                    "pack_a": r.pack_a,
                    "cell_max_v": r.cell_max_v,
                    "cell_min_v": r.cell_min_v,
                    "cell_max_no": r.max_cell_no,
                    "cell_min_no": r.min_cell_no,
                    "remaining_ah": r.remaining_ah,
                    "full_ah": r.full_ah,
                    "max_temp_c": r.max_temp_c,
                    "min_temp_c": r.min_temp_c,
                    "mos_temp_c": r.mos_temp_c,
                    "heat_a": r.heat_a,
                }
                for r in records
            ]
        )
        return EXIT_OK
    if not records:
        print("This unit answered, and holds no stored records.")
        return EXIT_OK
    print_table(
        ["WHEN", "EVENT", "PACK", "CURRENT", "CELLS", "TEMP", "CLOSED"],
        [
            [
                r.when.strftime(CLOCK_FORMAT) if r.when else "-",
                r.name,
                f"{r.pack_v:.2f} V",
                f"{r.pack_a:.1f} A",
                f"{r.cell_min_v:.3f}-{r.cell_max_v:.3f} V",
                f"{r.mos_temp_c} ℃",
                ", ".join(r.closed) or "-",
            ]
            for r in records
        ],
    )
    return EXIT_OK


def cmd_log_codes(device: Device | None, args: argparse.Namespace) -> int:
    """Print the code-to-name table a stored record's code indexes."""
    names = logcodes.all_names()
    if args.json:
        print_json({str(code): name for code, name in names.items()})
        return EXIT_OK
    print_table(["CODE", "EVENT"], [[str(code), name] for code, name in names.items()])
    note(
        "extracted from JK BMS Monitor 3.11.0; these are the vendor's own "
        "words for what its boards record"
    )
    return EXIT_OK


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    sp = sub.add_parser(
        "history",
        help="the board's own stored fault records (where a board maps them)",
        parents=[common],
    )
    sp.add_argument(
        "--base",
        type=lambda s: int(s, 0),
        default=None,
        metavar="OFF",
        help=(
            "offset from --addr-offset to read the records at "
            f"(default 0x{H.CANDIDATE_BASES[H.TABLE]:03X}, a candidate rather "
            "than a documented address)"
        ),
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = sub.add_parser(
        "log-codes", help="what each stored record's code means (no hardware)"
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")


COMMANDS: dict[str, Command] = {
    "history": Command(cmd_history),
    "log-codes": Command(cmd_log_codes, Need.NOTHING),
}
