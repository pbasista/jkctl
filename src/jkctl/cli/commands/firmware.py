"""Reading a ``.jkbms`` file, checking it against a unit, and flashing it.

The three commands are deliberately separate.  ``info`` needs no hardware at
all and answers "what is in this file"; ``check`` answers "would the vendor's
application let me put this on that unit"; ``flash`` does it.

The transfer has succeeded on repeated flashes of the official V15.41 image to
one physical JK_PB2A16S20P.  That verifies this sender and that bootloader
combination; it does not establish compatibility with other models, hardware
revisions or firmware versions.

The captured bootloader confirms that it checks XMODEM's block number,
complement and 8-bit additive checksum, but has no whole-image checksum,
signature, model/version check, expected-length check or destination upper
bound.  After transfer it checks only that the application's initial stack
pointer resembles SRAM before launching it.  A wrong, corrupt, truncated or
oversized image can therefore be written and may be run.

What the file *is* remains cross-checked to the byte: the image ``flash`` sends
is decoded from the same AES-CBC + zlib container the vendor ships, and its
extracted bytes match the selected image.  The compatibility gate protects
against an ordinary model or major-version mistake on the host; it is not a
device-side integrity guarantee.
"""

from __future__ import annotations

import argparse
import hashlib
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

from jkctl import aes, controls as C, firmware as F, flashdump as D, upgrade as U
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
  * The captured bootloader does NOT verify a signature, whole-image checksum,
    model, version, expected length or destination upper bound. On the wire it
    checks only the XMODEM block number/complement and weak 8-bit checksum; at
    launch it checks only the initial stack pointer. Assume a wrong, corrupt,
    truncated or oversized image can be written and may run.
  * This exact path has succeeded on repeated flashes of official V15.41 to a
    JK_PB2A16S20P. That is evidence for this one combination, not for another
    model, hardware revision or firmware version.
  * A failed or interrupted flash can leave the unit with no working
    application: it will not run or manage the battery until reflashed.
    Re-arming the upgrade over RS485 is done by the application, so a dead app
    cannot be re-flashed over the wire; recovery is then a hardware bench job
    through the STM32 ROM bootloader (BOOT0 high, USB-TTL and stm32flash).
    Clearing readout protection mass-erases the JK bootloader too.

Do not do this to a battery you cannot afford to lose."""

_DUMPER_FLASH_NOTICE = """\
This is a target-specific, modified firmware image:

  * It is accepted only when built from one of the exact audited vendor images
    named by SHA-256. It adds one Modbus action and a bounded 256-byte read
    path; all stock actions and the stock application remain present.
  * The V15.41 patch has been flashed to one JK_PB2A16S20P; that board booted,
    continued operating, and produced a validated 128 KiB full-flash capture.
    No other model, hardware revision or firmware version has been proven on
    hardware. Keep the pack attended and independently disconnectable.
  * The bootloader has no end-to-end image checksum or length/bounds check. An
    interrupted or bad flash can leave no working application, and a dead
    application cannot re-arm JK's bootloader over RS485. Have BOOT0/UART
    recovery access before proceeding.

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


def _repack(device: Device | None, args: argparse.Namespace) -> int:
    """Re-encode a .jkbms, optionally with a new image or metadata; no hardware.

    The inverse of what ``info`` decodes.  It takes a source file for its
    metadata, lets you swap in a patched image and/or edit the in-image header
    (version, model, expiry), writes a valid ``.jkbms``, and reloads it to prove
    it decodes.  A repacked file is not byte-identical to a vendor build (its
    zlib stream differs) but decodes to the same image and metadata.
    """
    fw = _load(args.file)
    image = None
    if args.image:
        with open(args.image, "rb") as fh:
            image = fh.read()
        warn(
            f"replacing the carried image ({len(fw.image)} bytes) with "
            f"{args.image} ({len(image)} bytes)"
        )
    try:
        blob = F.repack(
            fw,
            image=image,
            version=args.set_version,
            model=args.set_model,
            build_ms=args.build_ms,
            valid_hours=args.valid_hours,
        )
    except F.FirmwareError as exc:
        error(str(exc))
        return EXIT_ERROR
    with open(args.out, "wb") as fh:
        fh.write(blob)
    print(f"wrote {args.out} ({len(blob)} bytes)\n")
    _describe(_load(args.out))
    if image is not None or args.set_version or args.set_model:
        warn("this is a repacked, non-vendor file -- flash only a device you own")
    return EXIT_OK


def _make_dumper(device: Device | None, args: argparse.Namespace) -> int:
    """Patch one exact audited image and wrap it in a new container."""
    fw = _load(args.file)
    try:
        target = D.identify_image(fw.image)
        image = D.patch_image(fw.image)
        blob = F.repack(fw, image=image)
    except (D.FlashDumpError, F.FirmwareError) as exc:
        error(str(exc))
        return EXIT_ERROR
    with open(args.out, "wb") as fh:
        fh.write(blob)
    rebuilt = _load(args.out)
    if rebuilt.image != image or not D.is_patched(rebuilt.image):
        error("repacked dumper did not decode to the patched image")
        return EXIT_ERROR
    print(f"wrote {args.out} ({len(blob)} bytes)")
    print(
        f"patched image: {len(image)} bytes, SHA-256 {hashlib.sha256(image).hexdigest()}"
    )
    print(
        f"Flash it only onto {target.model} {target.version} with:\n"
        f"  jkctl firmware flash --force {args.out}"
    )
    warn("this is modified, target-specific firmware -- read the flash warning first")
    return EXIT_OK


def _dump_flash(device: Device, args: argparse.Namespace) -> int:
    """Request every flash block from the patched application and save it."""
    if not D.supports_device(device.model, device.version):
        supported = ", ".join(
            f"{model} {version}" for model, version in sorted(D.SUPPORTED_DEVICES)
        )
        error(
            f"flash dump action does not support {device.model} {device.version}; "
            f"audited targets: {supported}"
        )
        return EXIT_INCOMPATIBLE
    with TerminalReporter(trace=args.trace) as report:
        try:
            image = D.dump_flash(
                device.bus,
                device.slave,
                report=report,
                timeout=args.dump_timeout,
                max_retries=args.dump_retries,
            )
        except D.FlashDumpError as exc:
            report.__exit__(None, None, None)
            error(str(exc))
            return EXIT_ERROR
    with open(args.out, "wb") as fh:
        fh.write(image)
    digest = hashlib.sha256(image).hexdigest()
    print(f"wrote {args.out} ({len(image)} bytes, SHA-256 {digest})")
    if args.bootloader_out:
        pre_app = image[: D.APP_BASE - D.FLASH_BASE]
        with open(args.bootloader_out, "wb") as fh:
            fh.write(pre_app)
        print(
            f"wrote {args.bootloader_out} ({len(pre_app)} pre-application bytes; "
            "0x1800-0x1fff are persistent pages)"
        )
    problems = D.vector_sanity(image, device.model, device.version)
    if problems:
        for problem in problems:
            warn(problem)
    else:
        boot_sp = int.from_bytes(image[:4], "little")
        boot_reset = int.from_bytes(image[4:8], "little")
        print(
            "vector sanity: bootloader "
            f"SP=0x{boot_sp:08x} RESET=0x{boot_reset:08x}; application vectors match"
        )
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
    # Shown most important first, the same order the library uses, so a file's
    # reasons read the same whether it is checked here or listed there.
    checks = sorted(
        F.gate(fw, device.model, device.version, force=force),
        key=lambda c: _VERDICT_ORDER.get(c.name, 99),
    )
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
        verdict = "would flash" if not blocking else _verdict(blocking)
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


# Worst first: a wrong board or major version is the reason that matters, and
# it must not be hidden behind a merely-not-newer minor version.
_VERDICT_ORDER = {
    "model": 0,
    "major version": 1,
    "device version": 2,
    "expiry": 3,
    "minor version": 4,
}


def _why(check: F.Check) -> str:
    """Say in a few words why a file was turned down."""
    return {
        "expiry": "expired build",
        "device version": "unit version unreadable",
        "major version": "wrong major version",
        "minor version": "not newer",
        "model": "wrong model",
    }.get(check.name, check.name)


def _verdict(blocking: list[F.Check]) -> str:
    """Name every reason a file was turned down, worst first.

    The vendor's dialog reports only the first refusal, so a file for the wrong
    board that is also not newer comes back as "not newer" and the wrong model
    is never mentioned.  This lists them all, most important first.
    """
    ordered = sorted(blocking, key=lambda c: _VERDICT_ORDER.get(c.name, 99))
    return ", ".join(_why(c) for c in ordered)


def _flash(device: Device, args: argparse.Namespace) -> int:
    """Check the image, ask, then arm the bootloader and send it."""
    fw = _load(args.file)
    _describe(fw)
    print()
    rc = _gate(fw, device, args.force)
    if rc != EXIT_OK:
        return rc
    print(
        f"\n{_DUMPER_FLASH_NOTICE if D.is_patched(fw.image) else _FLASH_NOTICE}",
        file=sys.stderr,
    )
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
    _resync_clock(device)
    print("The BMS restarts into the new application; confirm it with:  jkctl info")
    return EXIT_OK


def _resync_clock(device: Device) -> None:
    """After a flash, set the clock to local time once the unit reboots.

    A reboot can leave the board's real-time clock at the firmware's own
    default, and the board keeps no time zone, so without this a flash can
    leave the clock a whole zone offset from local time.  Best effort: a unit
    that never answers just gets a hint to sync it by hand.
    """
    print("\nwaiting for the unit to reboot, then setting its clock to local time ...")
    when = C.resync_clock_after_flash(device)
    if when is None:
        warn(
            "could not set the clock (the unit did not answer in time); "
            "run 'jkctl time sync' once it is back on the bus"
        )
    else:
        print(f"clock set to {when.strftime('%Y-%m-%d %H:%M:%S')} local time")


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
        "repack",
        help="re-encode a .jkbms (swap image / edit metadata), with no hardware",
    )
    sp.add_argument("file", metavar="FILE.jkbms", help="source file for the metadata")
    sp.add_argument("-o", "--out", required=True, metavar="OUT.jkbms")
    sp.add_argument(
        "--image",
        metavar="IMAGE.bin",
        help="replace the carried image with this raw binary",
    )
    sp.add_argument(
        "--set-version", metavar="MAJOR.MINOR", help="set the in-image version string"
    )
    sp.add_argument("--set-model", metavar="NAME", help="set the in-image model string")
    sp.add_argument(
        "--valid-hours",
        type=int,
        metavar="N",
        help="set the expiry window in hours (0 = none; default: keep the source's)",
    )
    sp.add_argument(
        "--build-ms",
        type=int,
        metavar="MS",
        help="set the build timestamp in ms (default: keep the source's)",
    )

    sp = actions.add_parser(
        "make-dumper",
        help="patch any exact audited archive image for full-flash reads",
    )
    sp.add_argument("file", metavar="FILE.jkbms")
    sp.add_argument("-o", "--out", required=True, metavar="OUT.jkbms")

    sp = actions.add_parser(
        "dump-flash",
        help="read all MCU flash from the patched firmware",
        parents=[common],
    )
    sp.add_argument("-o", "--out", required=True, metavar="FULL.bin")
    sp.add_argument(
        "--bootloader-out",
        metavar="PRE_APP.bin",
        help="also write 0x08000000-0x08001fff (bootloader plus persistent pages)",
    )
    sp.add_argument(
        "--dump-timeout",
        type=float,
        default=1.0,
        metavar="S",
        help="seconds to wait for each block (default: 1)",
    )
    sp.add_argument(
        "--dump-retries",
        type=int,
        default=3,
        metavar="N",
        help="retries per bad or missing block (default: 3)",
    )

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


_ACTIONS.update(
    info=_info,
    repack=_repack,
    check=_check,
    list=_list,
    flash=_flash,
    **{"make-dumper": _make_dumper, "dump-flash": _dump_flash},
)

COMMANDS: dict[str, Command] = {
    "firmware": Command(
        cmd_firmware,
        per_action={
            "info": Need.NOTHING,
            "repack": Need.NOTHING,
            "make-dumper": Need.NOTHING,
        },
    ),
}
