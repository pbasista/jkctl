"""Changing what the BMS does: the switches, the presets and the calibrations.

Two kinds of thing live here.  The switches -- charging, discharging,
balancing, and the ten flags of the multiplexed switch word -- are ordinary
settings registers that read back, so they are written like any other setting.
The presets, the emergency start, the shutdown and the two calibrations are
write-only action slots that cannot be read, cannot be undone, and in three
cases replace every protection setpoint on the board; those ask first.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from devicectl.cli.command import Command
from devicectl.cli.output import CLOCK_FORMAT, aborted, confirm, print_table

from jkctl import controls as C, settings as S
from jkctl.cli.commands.settings import print_plan
from jkctl.cli.exits import EXIT_ABORTED, EXIT_OK
from jkctl.device import Device
from jkctl.registers import ACTIONS, SETTINGS


def cmd_toggle(device: Device, args: argparse.Namespace) -> int:
    """Turn charging, discharging or balancing on or off, or report it."""
    what = args.command
    if args.state is None:
        state = C.toggle_state(device)[what]
        print(f"{what}: {_onoff(state)}")
        return EXIT_OK
    C.toggle(device, what, args.state == "on")
    print(f"{what}: {args.state}")
    return EXIT_OK


def cmd_switches(device: Device, args: argparse.Namespace) -> int:
    """Show or change the flags of the multiplexed switch word."""
    if args.action == "show":
        word = device.read(S.SWITCH_REGISTER, SETTINGS)
        print(f"switchStatus = 0x{int(word or 0):04X}\n")
        print_table(
            ["BIT", "SWITCH", "STATE"],
            [
                [str(bit), name, state]
                for bit, name, _, state in S.switch_state(device.catalog, word)
            ],
        )
        return EXIT_OK

    name, sep, state = args.assignment.partition("=")
    if not sep or state.strip().lower() not in ("on", "off"):
        raise S.SettingsError(f"{args.assignment!r} is not NAME=on or NAME=off")
    bit = S.find_switch(device.catalog, name)
    word = device.set_bit(
        S.SWITCH_REGISTER, bit, state.strip().lower() == "on", SETTINGS
    )
    print(f"{name.strip()}: {state.strip().lower()}  (switchStatus = 0x{word:04X})")
    return EXIT_OK


def cmd_preset(device: Device, args: argparse.Namespace) -> int:
    """Apply a chemistry preset: JK's published values, as a settings plan.

    Shown and asked like ``settings set``, so a preset is never a guess at
    what the board will do.  ``--one-key`` fires the board's own slot
    instead, whose values the firmware chooses and nobody sees first.
    """
    if args.one_key:
        if not _permit(device, args, args.chemistry):
            return EXIT_ABORTED
        C.preset(device, args.chemistry)
        print(
            f"Fired the {args.chemistry} one-key slot.  See what it set with:"
            f"  jkctl settings show"
        )
        return EXIT_OK
    changes = S.plan(device, C.preset_values(args.chemistry))
    if not changes:
        print(f"Nothing to change; the unit already holds the {args.chemistry} preset.")
        return EXIT_OK
    print_plan(device, changes, args.dry_run)
    if args.dry_run:
        print("\nDry run; nothing written.")
        return EXIT_OK
    if not args.yes and not confirm(
        f"\nApply the {args.chemistry} preset to BMS {device.slave}"
        f" ({len(changes)} change(s))?"
    ):
        aborted()
        return EXIT_ABORTED
    S.apply(device, changes)
    print(f"\nWrote {len(changes)} setting(s).")
    return EXIT_OK


def cmd_emergency(device: Device, args: argparse.Namespace) -> int:
    """Fire the emergency-start action."""
    C.emergency(device)
    print("Emergency start sent.")
    return EXIT_OK


def cmd_shutdown(device: Device, args: argparse.Namespace) -> int:
    """Power the protection board down, after confirming."""
    if not _permit(device, args, "shutdown"):
        return EXIT_ABORTED
    C.shutdown(device)
    print("Shutdown sent; the unit will stop answering on this bus.")
    return EXIT_OK


def cmd_board(device: Device, args: argparse.Namespace) -> int:
    """Fire one of the board actions JK's register map does not list.

    Each names what it does and asks, because none of the three is
    recoverable: a factory restore takes your whole configuration with it, and
    an erase takes the history the board would otherwise be able to tell you
    about afterwards.  They are also, all three, unproven -- their slot
    numbers come from the vendor application's own buttons rather than from
    its register-map document.
    """
    what = args.command
    if not _permit(device, args, what):
        return EXIT_ABORTED
    C.board_action(device, what)
    print(f"{C.describe(what).label} sent.")
    if what == "restart":
        print("The board restarts; give it a moment before reading it again.")
    return EXIT_OK


def cmd_time(device: Device, args: argparse.Namespace) -> int:
    """Show or set the unit's real-time clock."""
    if args.action == "show":
        when = C.read_clock(device)
        print(
            when.strftime(CLOCK_FORMAT)
            if when
            else "this unit does not report an RTC counter"
        )
        return EXIT_OK
    written = C.sync_clock(device, datetime.now().astimezone())
    print(f"Set the clock to {written.strftime(CLOCK_FORMAT)}.")
    return EXIT_OK


def cmd_calibrate(device: Device, args: argparse.Namespace) -> int:
    """Recalibrate the voltage or current measurement, after confirming."""
    what = "voltage-calibration" if args.action == "voltage" else "current-calibration"
    if not _permit(device, args, what):
        return EXIT_ABORTED
    if args.action == "voltage":
        C.calibrate_voltage(device, args.value)
        print(f"Told the BMS the pack is {args.value} mV.")
    else:
        C.calibrate_current(device, args.value)
        print(f"Told the BMS the current is {args.value} mA.")
    return EXIT_OK


def _permit(device: Device, args: argparse.Namespace, action: str) -> bool:
    """Warn what an action does and ask, unless -y was given."""
    act = ACTIONS[action]
    if args.yes:
        return True
    import sys

    print(f"{act.label}: this {act.warning}.", file=sys.stderr)
    print("It cannot be undone.", file=sys.stderr)
    if confirm(
        f"{act.label} on BMS {device.slave} ({device.model or 'unknown model'})?"
    ):
        return True
    aborted()
    return False


def _onoff(state: bool | None) -> str:
    """Render a tri-state switch reading."""
    return "unknown" if state is None else ("on" if state else "off")


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    for name, what in (
        ("charge", "charging"),
        ("discharge", "discharging"),
        ("balance", "balancing"),
    ):
        sp = sub.add_parser(
            name, help=f"turn {what} on or off, or report it", parents=[common]
        )
        sp.add_argument(
            "state",
            nargs="?",
            choices=("on", "off"),
            help="omit to report the current state",
        )

    p = sub.add_parser("switches", help="the multiplexed on/off switch word")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser(
        "show", help="list the switches and their states", parents=[common]
    )
    sp = actions.add_parser("set", help="turn one switch on or off", parents=[common])
    sp.add_argument("assignment", metavar="NAME=on|off")

    sp = sub.add_parser(
        "preset",
        help="set JK's published defaults for a cell chemistry",
        parents=[common],
    )
    sp.add_argument("chemistry", choices=C.PRESETS)
    sp.add_argument(
        "--dry-run", action="store_true", help="show the plan and write nothing"
    )
    sp.add_argument(
        "--one-key",
        action="store_true",
        help="fire the board's own one-key slot instead; its values are unseen",
    )
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask first")

    sp = sub.add_parser(
        "emergency", help="fire the emergency-start action", parents=[common]
    )
    sp.add_argument(
        "-y", "--yes", action="store_true", help="unused; accepted for symmetry"
    )

    sp = sub.add_parser(
        "shutdown", help="power the protection board down", parents=[common]
    )
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask first")

    for name, blurb in (
        ("restart", "restart the protection board"),
        ("factory-restore", "return every setting to the factory configuration"),
        ("erase-data", "erase the board's stored data"),
    ):
        sp = sub.add_parser(name, help=f"{blurb} (undocumented slot)", parents=[common])
        sp.add_argument("-y", "--yes", action="store_true", help="do not ask first")

    p = sub.add_parser("time", help="the unit's real-time clock")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser("show", help="print the unit's clock", parents=[common])
    actions.add_parser(
        "sync", help="set the unit's clock from this host", parents=[common]
    )

    p = sub.add_parser("calibrate", help="recalibrate a measurement")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    for name, unit in (("voltage", "mV"), ("current", "mA")):
        sp = actions.add_parser(
            name, help=f"tell the BMS the true {name}, in {unit}", parents=[common]
        )
        sp.add_argument("value", type=int, metavar=unit.upper())
        sp.add_argument("-y", "--yes", action="store_true", help="do not ask first")


COMMANDS: dict[str, Command] = {
    "charge": Command(cmd_toggle),
    "discharge": Command(cmd_toggle),
    "balance": Command(cmd_toggle),
    "switches": Command(cmd_switches, default_action="show"),
    "preset": Command(cmd_preset),
    "emergency": Command(cmd_emergency),
    "shutdown": Command(cmd_shutdown),
    "restart": Command(cmd_board),
    "factory-restore": Command(cmd_board),
    "erase-data": Command(cmd_board),
    "time": Command(cmd_time, default_action="show"),
    "calibrate": Command(cmd_calibrate),
}
