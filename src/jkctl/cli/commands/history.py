"""The board's own stored fault records, and the names for what they carry.

Two commands.

``jkctl log-codes`` needs no hardware and is certain: it prints the map the
vendor's application uses to turn a stored record's code into a sentence,
extracted from the application itself.

``jkctl history`` shows the records.  They are *not* served over Modbus -- the
firmware bounds a read to frames 01-03 and the vendor reads them over
Bluetooth instead -- so the way to get them through jkctl is a full flash
dump: ``jkctl firmware dump-flash`` on a patched board, then ``jkctl history
--from-flash-dump full.bin``, which decodes the record ring straight out of the
image and needs no hardware.  Without ``--from-flash-dump`` the command still
makes the over-the-wire attempt, for a board that might differ, and says
plainly why it found nothing when it does not.
"""

from __future__ import annotations

import argparse

from devicectl.cli.command import Command, Need
from devicectl.cli.output import CLOCK_FORMAT, error, note, print_json, print_table

from jkctl import history as H, logcodes
from jkctl.cli import target as T
from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.cli.fanout import fan_out
from jkctl.device import Device


def cmd_history(device: Device | None, args: argparse.Namespace) -> int:
    """Show stored fault records, from a flash dump or from the wire.

    Needs no hardware for ``--from-flash-dump``; opens the bus itself for the
    wire attempt (through the :mod:`~jkctl.cli.target` module so it opens the
    same one every other command does).
    """
    if args.from_flash_dump:
        return _from_dump(args)
    opened = T.open_device(args)
    if opened is None:
        return EXIT_ERROR
    bus, first = opened
    args.target = T.device_target(args)
    with bus:
        return fan_out(first, args, lambda dev: _one(dev, args))


def _from_dump(args: argparse.Namespace) -> int:
    """Decode the record ring out of a full flash image; no hardware."""
    try:
        with open(args.from_flash_dump, "rb") as fh:
            image = fh.read()
    except OSError as exc:
        error(str(exc))
        return EXIT_ERROR
    records = H.read_dump(image)
    if not records and not args.json:
        note(
            f"no stored records found in {args.from_flash_dump} -- the record "
            "region is erased, or this image is not a full dump of a supported "
            "board"
        )
    return _emit(records, args)


def _one(device: Device, args: argparse.Namespace) -> int:
    """Attempt one unit's records over the wire (see the module docstring)."""
    try:
        records = H.read(device, args.base)
    except H.HistoryError as exc:
        error(str(exc))
        return EXIT_ERROR
    if not records and not args.json:
        print("This unit answered, and holds no stored records.")
        return EXIT_OK
    return _emit(records, args)


def _emit(records: list[H.Record], args: argparse.Namespace) -> int:
    """Print the records as a table, or as JSON with --json."""
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
        help="the board's own stored fault records (from a flash dump)",
        parents=[common],
    )
    sp.add_argument(
        "--from-flash-dump",
        metavar="FULL.bin",
        help="read the records out of a full flash dump (an image the board "
        "was dumped to, not a file to write) instead of the wire -- needs no "
        "hardware, and is the way that works on stock firmware",
    )
    sp.add_argument(
        "--base",
        type=lambda s: int(s, 0),
        default=None,
        metavar="OFF",
        help=(
            "offset from --addr-offset for the over-the-wire attempt "
            f"(default 0x{H.CANDIDATE_BASES[H.TABLE]:03X}; stock firmware does "
            "not serve records over Modbus -- use --from-flash-dump)"
        ),
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = sub.add_parser(
        "log-codes", help="what each stored record's code means (no hardware)"
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")


COMMANDS: dict[str, Command] = {
    # NOTHING: the handler opens the bus itself only when it needs one, so
    # `--from-flash-dump` can decode an image with no port open.
    "history": Command(cmd_history, Need.NOTHING),
    "log-codes": Command(cmd_log_codes, Need.NOTHING),
}
