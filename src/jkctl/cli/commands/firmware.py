"""Reading a ``.jkbms`` file, checking it against a unit, and flashing it.

The three commands are deliberately separate.  ``info`` needs no hardware at
all and answers "what is in this file"; ``check`` answers "would the vendor's
application let me put this on that unit"; ``flash`` does it.

Nothing about the transfer has been exercised on a real BMS.  The arming write
matches a capture of the vendor's application byte for byte, and the block
format was recovered from its sender, but the receiving side is the
bootloader, which ships in no firmware file and could not be read.

What the file *is* has been cross-checked to the byte: the image ``flash``
sends is the same AES-CBC + zlib container the vendor ships, and its extracted
bytes are identical (SHA-256) to an image pulled independently from the vendor
build.  What the *device* does with it is the unknown.  A ``.jkbms`` carries no
signature and no whole-image checksum -- only metadata (model, version, a build
timestamp and an optional expiry) and the raw image -- and JK's application
validates only that metadata, never the image content.  On the wire the sole
guard is XMODEM's 8-bit per-block checksum.  Whether the bootloader verifies
the image before it runs it is unknown, because it could not be read.  So the
risk this command carries is not that it sends the wrong bytes -- it sends
exactly the vendor's -- but that the device is not known to defend itself
against bytes that are wrong for any other reason.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Callable

from devicectl.cli.command import Command, Need
from devicectl.cli.output import (
    aborted,
    error,
    print_json,
    print_rows,
    print_table,
    warn,
)

from jkctl import aes, firmware as F, upgrade as U
from jkctl.cli.exits import (
    EXIT_ABORTED,
    EXIT_ERROR,
    EXIT_INCOMPATIBLE,
    EXIT_OK,
    EXIT_UPDATE_FAILED,
)
from jkctl.cli.report import TerminalReporter
from jkctl.device import Device

MS_PER_HOUR = 3_600_000

# Shown before every flash, with -y or without.  It states how the device is
# understood to behave, because that -- not the sender, which is verified --
# is where the risk lives.  See the module docstring and jkctl's own
# `firmware` docs for the evidence.
_FLASH_NOTICE = """\
Before flashing, know how the BMS is understood to behave:

  * The image sent is exactly the vendor's -- the .jkbms container is decoded
    the way JK's application decodes it, and the bytes match a vendor image to
    the SHA-256.  This command does not send the wrong bytes.
  * The BMS is NOT known to verify what it receives.  The file carries no
    signature and no whole-image checksum; JK's application checks only model,
    version and expiry, never the image; and the receiving bootloader ships in
    no firmware file, so it could never be read.  On the wire the only guard is
    XMODEM's weak 8-bit per-block checksum.  Assume a wrong or corrupt image
    can be written and run.
  * This transfer has never run against a real BMS -- only a simulator.
  * A failed or interrupted flash leaves the unit with no working application:
    it will not run or manage the battery until reflashed.  Re-arming the
    upgrade over RS485 is done by the application, so a dead app cannot be
    re-flashed over the wire; recovery is then a hardware bench job -- the STM32
    ROM bootloader (BOOT0 high, a USB-TTL adapter, stm32flash), which can
    rewrite the application from the image jkctl extracts *if* the MCU's readout
    protection is off.  If it is on, clearing it mass-erases the JK bootloader
    too, and recovery then needs a full image this tool does not have.

Do not do this to a battery you cannot afford to lose."""


# `info` reads a file and needs no unit; the other two need one.
_ACTIONS: dict[str, Callable[[Any, argparse.Namespace], int]] = {}


def cmd_firmware(device: Device | None, args: argparse.Namespace) -> int:
    """Dispatch one ``jkctl firmware`` action."""
    return _ACTIONS[args.action](device, args)


def _load(path: str) -> F.Firmware:
    """Load a .jkbms, warning first if only the slow pure-Python AES is around.

    Decrypting a firmware image with it takes seconds on x86 and minutes on a
    small ARM host, and the decrypt runs before anything is printed -- so
    without this the command simply looks hung.
    """
    if aes.backend_name() == "pure-python":
        warn(
            "no OpenSSL libcrypto found; decrypting with the pure-Python AES "
            "fallback, which can take a while on a slow host.\n"
            "  installing OpenSSL gives an instant decrypt with no Python "
            "packages to build (jkctl calls libcrypto via ctypes):\n"
            "    apt install openssl libssl3   |   pacman -S openssl   |   apk add openssl"
        )
    return F.load(path)


def _describe(fw: F.Firmware) -> None:
    """Print what a firmware file contains."""
    print_rows(
        "firmware file",
        [
            ("model", fw.model),
            ("version", f"{fw.version}  (major={fw.major} minor={fw.minor})"),
            ("device code", "n/a" if fw.device_code is None else str(fw.device_code)),
            ("built", f"{fw.build_date} {fw.build_time}"),
            ("image size", f"{len(fw.image)} bytes"),
            ("vector table", f"SP=0x{fw.sp:08x} RESET=0x{fw.reset:08x}"),
            ("xmodem blocks", str(U.block_count(fw.image))),
            ("time-limited", _validity(fw)),
        ],
    )


def _validity(fw: F.Firmware) -> str:
    """Say whether a time-limited build is still inside its window."""
    if fw.valid_hours <= 0:
        return "no"
    end = fw.build_ms + fw.valid_hours * MS_PER_HOUR
    return (
        f"{fw.valid_hours} h from build -- "
        f"{'EXPIRED' if time.time() * 1000 > end else 'still valid'}"
    )


def _info(device: Device | None, args: argparse.Namespace) -> int:
    """Describe a firmware file without touching any hardware."""
    _describe(_load(args.file))
    return EXIT_OK


def _check(device: Device, args: argparse.Namespace) -> int:
    """Run the vendor's whole compatibility gate against the connected unit."""
    fw = _load(args.file)
    _describe(fw)
    print()
    return _gate(fw, device, args.force)


def _gate(fw: F.Firmware, device: Device, force: bool) -> int:
    """Show every step of the gate, and return whether it may be flashed.

    All of the steps, not the first refusal: a file turned down on its minor
    version says nothing about whether the model matched, and the vendor
    dialog's habit of answering with one sentence is why nobody can tell a
    file for the wrong board from a file that is merely not newer.
    """
    if not device.model:
        error(f"no unit answered at address {device.slave}")
        return EXIT_ERROR
    print(f"connected unit: {device.model} v{device.version}")
    checks = F.gate(fw, device.model, device.version, force=force)
    print()
    print_table(
        ["", "CHECK", "DETAIL"],
        [[_mark(c), c.name, c.detail] for c in checks],
    )
    if any(c.waived for c in checks):
        warn("--force waived the checks marked (!)")
    blocking = [c for c in checks if c.blocking]
    if not blocking:
        return EXIT_OK
    # The table above is the reasoning; this is the one line a script greps
    # for and the one sentence somebody reads before scrolling back up.
    for check in blocking:
        error(check.detail)
    return EXIT_INCOMPATIBLE


def _mark(check: F.Check) -> str:
    """One character for how a gate step went."""
    if check.ok:
        return "ok"
    return "!" if check.waived else "no"


def _list(device: Device, args: argparse.Namespace) -> int:
    """Parse every firmware file in a directory and say which this unit takes."""
    found = F.scan(args.directory)
    if not found:
        error(f"no .jkbms files under {args.directory}")
        return EXIT_ERROR
    model, version = device.model, device.version
    rows = []
    doc = []
    for item in found:
        fw = item.firmware
        if fw is None:
            rows.append(
                [
                    os.path.relpath(item.path, args.directory),
                    "-",
                    "-",
                    "-",
                    f"unreadable: {item.error}",
                ]
            )
            doc.append({"path": item.path, "error": item.error})
            continue
        checks = F.gate(fw, model, version, force=args.force)
        blocking = [c for c in checks if c.blocking]
        verdict = "would flash" if not blocking else _why(blocking[0])
        if args.compatible and blocking:
            continue
        rows.append(
            [
                os.path.relpath(item.path, args.directory),
                fw.model,
                fw.version,
                fw.build_date,
                verdict,
            ]
        )
        doc.append(
            {
                "path": item.path,
                "model": fw.model,
                "version": fw.version,
                "built": f"{fw.build_date} {fw.build_time}",
                "timeLimited": fw.valid_hours > 0,
                "compatible": not blocking,
                "verdict": verdict,
                "checks": [
                    {"name": c.name, "ok": c.ok, "waived": c.waived, "detail": c.detail}
                    for c in checks
                ],
            }
        )
    if args.json:
        print_json({"unit": {"model": model, "version": version}, "files": doc})
        return EXIT_OK
    print(f"connected unit: {model} v{version}\n")
    print_table(["FILE", "MODEL", "VERSION", "BUILT", "VERDICT"], rows)
    return EXIT_OK


def _why(check: F.Check) -> str:
    """Say in three words why a file was turned down."""
    return {
        "inside its validity window": "expired build",
        "the unit reports a version": "unit version unreadable",
        "major version matches": "wrong major version",
        "minor version is newer": "not newer",
        "model matches": "wrong model",
    }.get(check.name, check.name)


def _flash(device: Device, args: argparse.Namespace) -> int:
    """Check the image, ask, then arm the bootloader and send it."""
    fw = _load(args.file)
    _describe(fw)
    print()
    rc = _gate(fw, device, args.force)
    if rc != EXIT_OK:
        return rc
    print(f"\n{_FLASH_NOTICE}", file=sys.stderr)
    if not args.yes:
        print(
            f"\nAbout to flash {fw.model} v{fw.version} onto BMS "
            f"{device.slave} ({device.model} v{device.version}).",
            file=sys.stderr,
        )
        if input("Type 'yes' to continue: ").strip().lower() != "yes":
            aborted()
            return EXIT_ABORTED
    reg = device.bus.action_register(_upgrade_slot())
    print(f"\narming upgrade mode (write 0x0000 to register 0x{reg:04X}) ...")
    with TerminalReporter(trace=args.trace) as report:
        try:
            U.upgrade(device.bus, device.slave, fw.image, report=report)
        except U.UpgradeError as exc:
            report.__exit__(None, None, None)
            error(str(exc))
            return EXIT_UPDATE_FAILED
    print("Upload firmware successfully.")
    print("The BMS restarts into the new application; confirm it with:  jkctl info")
    return EXIT_OK


def _upgrade_slot() -> int:
    """Return the action slot the arming write goes to."""
    from jkctl.registers import UPGRADE_SLOT

    return UPGRADE_SLOT


def add_parsers(
    sub: argparse._SubParsersAction, common: argparse.ArgumentParser
) -> None:
    """Add this group's commands to the root parser."""
    p = sub.add_parser("firmware", help="inspect, check and flash a .jkbms file")
    actions = p.add_subparsers(dest="action", metavar="ACTION", required=True)

    sp = actions.add_parser("info", help="describe a file, with no hardware")
    sp.add_argument("file", metavar="FILE.jkbms")

    sp = actions.add_parser(
        "check", help="check a file against the connected unit", parents=[common]
    )
    sp.add_argument("file", metavar="FILE.jkbms")
    sp.add_argument(
        "--force",
        action="store_true",
        help="bypass the minor-version and expiry gates "
        "(model and major version stay hard requirements)",
    )

    sp = actions.add_parser(
        "list",
        help="every .jkbms in a directory, and which this unit would take",
        parents=[common],
    )
    sp.add_argument("directory", metavar="DIR", help="a directory to walk")
    sp.add_argument(
        "--compatible",
        action="store_true",
        help="only the files this unit would accept",
    )
    sp.add_argument(
        "--force",
        action="store_true",
        help="judge them as --force would: waive the minor-version and expiry gates",
    )
    sp.add_argument("--json", action="store_true", help="JSON instead of a table")

    sp = actions.add_parser(
        "flash", help="check, then write a file to the unit", parents=[common]
    )
    sp.add_argument("file", metavar="FILE.jkbms")
    sp.add_argument(
        "--force", action="store_true", help="bypass the minor-version and expiry gates"
    )
    sp.add_argument(
        "-y", "--yes", action="store_true", help="do not ask before flashing"
    )


_ACTIONS.update(info=_info, check=_check, list=_list, flash=_flash)

COMMANDS: dict[str, Command] = {
    "firmware": Command(cmd_firmware, per_action={"info": Need.NOTHING}),
}
