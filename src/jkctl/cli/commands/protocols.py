"""The communication ports: which protocol each speaks, and the dry contacts.

The selector registers hold a number; the vendor's own application shows a
name.  The lists those names come from ship with this package, extracted from
the application's resources, so ``jkctl protocols show`` reads the way the
vendor's drop-down does.
"""

from __future__ import annotations

import argparse

from devicectl.cli.command import Command, Need
from devicectl.cli.output import error, print_json, print_rows, print_table

from jkctl import identity as I
from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.device import Device
from jkctl.registers import INFO, protocol_entries, protocol_names

# The selector a command-line word maps to, and which list names its values.
SELECTORS = {
    "uart1": ("uart1ProtoNo", "uart"),
    "uart2": ("uart2ProtoNo", "uart"),
    "uart3": ("uart3ProtoNo", "uart"),
    "uart4": ("uart4ProtoNo", "uart"),
    "can": ("canProtoNo", "can"),
}

# The ones JK's register-map document marks RW.  It describes a board with two
# serial ports, so ``uart3ProtoNo`` and ``uart4ProtoNo`` -- which exist in the
# datasource, and which ``protocols show`` reads -- appear in it nowhere, and a
# field the document does not mark writable is treated as read-only rather
# than guessed at (registers.py).  ``set`` therefore offers these three and
# says why when asked for one of the others, instead of offering four ports
# and refusing two of them at the last moment.  ``tests/test_cli_commands.py``
# holds this to the catalog.
SETTABLE = ("uart1", "uart2", "can")

# The two dry contacts and the buzzer, and the registers that drive each.
TRIGGERS = {
    "buzzer": ("lcdBuzzerTrigger", "lcdBuzzerTriggerVal", "lcdBuzzerReleaseVal"),
    "1": ("dry1Trigger", "dry1TriggerVal", "dry1ReleaseVal"),
    "2": ("dry2Trigger", "dry2TriggerVal", "dry2ReleaseVal"),
}


def cmd_protocols(device: Device, args: argparse.Namespace) -> int:
    """Dispatch one ``jkctl protocols`` action."""
    return {"list": _list, "show": _show, "set": _set}[args.action](device, args)


def _list(device: Device | None, args: argparse.Namespace) -> int:
    """Print the protocol lists the selectors index into."""
    entries = protocol_entries()
    kinds = [args.kind] if args.kind else ["uart", "can"]
    if args.json:
        print_json(
            {
                kind: [
                    {"id": number, "name": en, "vendor": native}
                    for number, (en, native) in sorted(entries.get(kind, {}).items())
                ]
                for kind in kinds
            }
        )
        return EXIT_OK
    for kind in kinds:
        print(f"{kind.upper()} protocols:\n")
        print_table(
            ["NO.", "PROTOCOL", "AS JK NAMES IT"],
            [
                [str(number), en, native]
                for number, (en, native) in sorted(entries.get(kind, {}).items())
            ],
        )
        print()
    return EXIT_OK


def _show(device: Device, args: argparse.Namespace) -> int:
    """Print which protocol each port is set to, and the trigger sources."""
    ident = I.read(device)
    if args.json:
        print_json(
            {
                "ports": [
                    {"port": label, "number": number, "protocol": name}
                    for label, number, name in ident.ports
                ],
                "triggers": _trigger_doc(device),
            }
        )
        return EXIT_OK
    if not ident.ports:
        error("this unit reported no protocol selectors")
        return EXIT_ERROR
    print_table(
        ["PORT", "NO.", "PROTOCOL"],
        [[label, str(number), name] for label, number, name in ident.ports],
    )
    rows = _trigger_rows(device)
    if rows:
        print()
        print_table(["OUTPUT", "SOURCE", "TRIGGER", "RELEASE"], rows)
    return EXIT_OK


def _set(device: Device, args: argparse.Namespace) -> int:
    """Point one port at a different protocol."""
    if args.selector not in SETTABLE:
        error(
            f"{args.selector} cannot be set: JK's register map describes a "
            f"two-port board and does not mark {SELECTORS[args.selector][0]} "
            f"writable (jkctl protocols show still reads it)"
        )
        return EXIT_ERROR
    key, kind = SELECTORS[args.selector]
    names = protocol_names().get(kind, {})
    if args.number not in names:
        error(
            f"{kind} protocol {args.number} is not in the list "
            f"(try: jkctl protocols list --{kind})"
        )
        return EXIT_ERROR
    device.write(key, args.number, INFO)
    print(f"{args.selector.upper()}: {names[args.number]}")
    print(
        "note: the unit may need a restart before it speaks the new protocol.",
        file=__import__("sys").stderr,
    )
    return EXIT_OK


def cmd_dry_contact(device: Device, args: argparse.Namespace) -> int:
    """Show or change a dry-contact or buzzer trigger."""
    if args.action == "show":
        rows = _trigger_rows(device)
        if args.json:
            print_json(_trigger_doc(device))
            return EXIT_OK
        if not rows:
            error("this unit reported no trigger registers")
            return EXIT_ERROR
        print_table(["OUTPUT", "SOURCE", "TRIGGER", "RELEASE"], rows)
        names = protocol_names().get("trigger", {})
        if names:
            print()
            print_table(
                ["NO.", "TRIGGER SOURCE"],
                [[str(n), t] for n, t in sorted(names.items())],
            )
        return EXIT_OK

    source_key, trig_key, rel_key = TRIGGERS[args.output]
    written = []
    for key, value in (
        (source_key, args.source),
        (trig_key, args.on),
        (rel_key, args.off),
    ):
        if value is None:
            continue
        device.write(key, value, INFO)
        written.append(key)
    if not written:
        error("nothing to do: give at least one of --source, --on or --off")
        return EXIT_ERROR
    print_rows(f"output {args.output}", _rows_for(device, args.output))
    return EXIT_OK


def _trigger_rows(device: Device) -> list[list[str]]:
    """Return one table row per dry contact and the buzzer."""
    names = protocol_names().get("trigger", {})
    rows = []
    for output, (source_key, trig_key, rel_key) in TRIGGERS.items():
        values = device.read_many([source_key, trig_key, rel_key], INFO)
        if source_key not in values:
            continue
        source = int(values[source_key])
        # The trigger names carry their own "00 - " prefix, so the number is
        # not repeated in front of them the way it is for the port table.
        rows.append(
            [
                output,
                names.get(source, f"{source} - unknown"),
                str(values.get(trig_key)),
                str(values.get(rel_key)),
            ]
        )
    return rows


def _rows_for(device: Device, output: str) -> list[tuple[str, str]]:
    """Return the label/value rows for one output, after a change."""
    source_key, trig_key, rel_key = TRIGGERS[output]
    names = protocol_names().get("trigger", {})
    values = device.read_many([source_key, trig_key, rel_key], INFO)
    source = int(values.get(source_key) or 0)
    return [
        ("source", names.get(source, f"{source} - unknown")),
        ("trigger value", str(values.get(trig_key))),
        ("release value", str(values.get(rel_key))),
    ]


def _trigger_doc(device: Device) -> dict:
    """Return the trigger settings as JSON-shaped data."""
    return {output: dict(_rows_for(device, output)) for output in TRIGGERS}


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    p = sub.add_parser("protocols", help="which protocol each port speaks")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)

    sp = actions.add_parser("list", help="list the protocols a port can be set to")
    sp.add_argument(
        "--uart",
        dest="kind",
        action="store_const",
        const="uart",
        help="only the serial-port list",
    )
    sp.add_argument(
        "--can",
        dest="kind",
        action="store_const",
        const="can",
        help="only the CAN list",
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = actions.add_parser("show", help="print each port's protocol", parents=[common])
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = actions.add_parser("set", help="point a port at a protocol", parents=[common])
    # Not "port": every subparser inherits the connection options, and --port
    # is one of them, so a positional of that name would overwrite it.
    sp.add_argument("selector", choices=sorted(SETTABLE), metavar="PORT")
    sp.add_argument("number", type=int, metavar="NO.")

    p = sub.add_parser("dry-contact", help="the dry contacts and the LCD buzzer")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    sp = actions.add_parser(
        "show", help="print the trigger sources and thresholds", parents=[common]
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")
    sp = actions.add_parser("set", help="change one output's trigger", parents=[common])
    sp.add_argument("output", choices=sorted(TRIGGERS))
    sp.add_argument("--source", type=int, help="trigger source number")
    sp.add_argument("--on", type=int, help="value at which it engages")
    sp.add_argument("--off", type=int, help="value at which it releases")


COMMANDS: dict[str, Command] = {
    "protocols": Command(
        cmd_protocols, per_action={"list": Need.NOTHING}, default_action="show"
    ),
    "dry-contact": Command(cmd_dry_contact, default_action="show"),
}
