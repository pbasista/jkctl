"""The configuration file: showing it, finding it, and writing a first one."""

from __future__ import annotations

import argparse
import sys

from devicectl.cli.command import Command, Need
from devicectl.cli.output import may_overwrite, print_rows, print_table

from jkctl.cli.exits import EXIT_ERROR, EXIT_OK
from jkctl.config import (
    EXAMPLE_CONFIG,
    default_config_path,
    load_config,
)
from jkctl.device import Device


def cmd_config(device: Device | None, args: argparse.Namespace) -> int:
    """Dispatch one ``jkctl config`` action."""
    return {"show": _show, "path": _path, "init": _init}[args.action](args)


def _show(args: argparse.Namespace) -> int:
    """Print what the configuration file actually says, ready to be pasted."""
    config = load_config(args.config)
    print(
        f"file: {config.path}"
        f"{'' if config.path and config.path.is_file() else '  (does not exist)'}\n"
    )
    rows = [
        (name, str(value))
        for name, value in (
            ("port", config.port),
            ("baud", config.baud),
            ("id", config.id),
            ("timeout", config.timeout),
            ("retries", config.retries),
            ("addr_offset", config.addr_offset),
        )
        if value is not None
    ]
    if rows:
        print_rows("defaults", rows)
        print()
    if config.devices:
        print_table(
            ["DEVICE", "PORT", "BAUD", "ID"],
            [
                [d.name, d.port or "-", str(d.baud or "-"), str(d.id or "-")]
                for d in config.devices.values()
            ],
        )
    elif not rows:
        print("Nothing configured; every command uses the built-in defaults.")
    return EXIT_OK


def _path(args: argparse.Namespace) -> int:
    """Print the path the configuration is read from."""
    print(args.config or default_config_path())
    return EXIT_OK


def _init(args: argparse.Namespace) -> int:
    """Write a commented example configuration."""
    path = args.config or default_config_path()
    if not may_overwrite(path, yes=args.yes):
        return EXIT_ERROR
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"Wrote {path}", file=sys.stderr)
    return EXIT_OK


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    p = sub.add_parser("config", help="the optional jk.toml settings file")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    for name, help_text in (
        ("show", "print what the file configures"),
        ("path", "print where the file is read from"),
        ("init", "write a commented example file"),
    ):
        sp = actions.add_parser(name, help=help_text, parents=[common])
        if name == "init":
            sp.add_argument(
                "-y",
                "--yes",
                action="store_true",
                help="overwrite an existing file without asking",
            )


COMMANDS: dict[str, Command] = {
    "config": Command(cmd_config, Need.NOTHING, default_action="show"),
}
