"""What went over the RS485 bus, in words -- and the report that carries it.

The recording itself is :mod:`devicectl.trace`, which knows nothing about
Modbus: it keeps timestamped frames and renders them as hex.  This is the
half that knows what the hex *says* -- which board was addressed, which
function, which register, and, because a JK register is a byte offset into a
named table, which settings the frame actually touched.

That last part is the reason this exists.  "no/short response" names the
symptom and nothing else; a report saying

    14:02:11.204  TX  writing 3 setting(s)
                  01 10 10 60 00 04 08 ...
                  write · slave 1 · 4 registers at 0x1060 · settings byte 0x60
                  · covers tempMosProtect, tempMosRecovery
    14:02:11.554  RX  (+350 ms)
                  (nothing)
    14:02:11.554  --  writing 3 setting(s) failed: no/short response

names the register the board would not take, how long it waited, and whether
anything at all came back.  That is a bug report somebody can act on.

The decoding is deliberately of the frame in front of it and nothing else: a
reply is read as the function code it carries rather than as the answer to
the request above it, so a trace with a lost frame in the middle of it still
decodes every frame after that one.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from devicectl import trace as T

from jkctl import modbus as M
from jkctl.registers import TABLE_LABELS, Catalog

TITLE = "jkctl serial trace"

# The shortest thing that can be decoded at all: an address, a function code
# and the two CRC bytes.  An exception reply is one byte longer, and the
# shortest request -- a read, or the head of a write -- is eight.
MIN_FRAME = 4
MIN_EXCEPTION = 3
MIN_REQUEST = 8

# How wide a table's register window is, in bytes.  The three tables sit
# 0x200 apart, so this is also how far past a base an address may be and
# still belong to it.
TABLE_SPAN = 0x200

# How many register names a decoded line will print before giving up and
# saying how many there were.  A read of a whole table covers forty of them,
# and forty names on one line is not a line anybody reads.
MAX_NAMED = 6

PREAMBLE = """
Every frame this program put on the RS485 bus and everything that came back,
oldest first.  Each entry is the wall clock time, the direction, how long it
was since the entry before it, and what the program was doing at the time;
under that, the bytes, and under those, what they mean.

  TX   the program wrote this to the port
  RX   this came back.  "(nothing)" means the read timed out with the port
       silent, which is what "no/short response" is reported for
  UI   something the page asked the program to do.  It appears twice: once
       as it arrives, with the body the page sent under it, and once with
       the status it was answered with.  The frames between the two are
       what that request put on the bus
  --   not a frame: a note, or an operation that failed

A JK register is a byte offset into a named table, so "settings byte 0x60"
and the register names beside it are the settings the frame touched.  Retries
are not marked: a read that is tried three times appears three times.

Nothing here is secret except what you have configured your battery to do.
""".strip()


def frames(direction: str, data: bytes, *, addr_offset: int, catalog: Catalog) -> str:
    """Describe one frame in a sentence, or return "" if it cannot be read."""
    if len(data) < MIN_FRAME:
        return "too short to be a Modbus frame" if data else ""
    slave, code = data[0], data[1]
    if code & 0x80:
        return (
            f"slave {slave} · refused: "
            f"{M.exception_text(data[2]) if len(data) >= MIN_EXCEPTION else 'no code'}"
        )
    if direction == T.TX:
        return _request(slave, code, data, addr_offset, catalog)
    return _reply(slave, code, data)


def _request(
    slave: int, code: int, data: bytes, addr_offset: int, catalog: Catalog
) -> str:
    """Decode a frame the program sent."""
    if len(data) < MIN_REQUEST:
        return f"slave {slave} · function {code:#04x} · {len(data)} bytes"
    reg = int.from_bytes(data[2:4], "big")
    count = int.from_bytes(data[4:6], "big")
    what = {M.FC_READ: "read", M.FC_WRITE: "write"}.get(code, f"function {code:#04x}")
    said = [
        f"{what} · slave {slave} · {count} register(s) at {reg:#06x}",
        _where(reg, addr_offset, count * 2, catalog),
        _crc(data),
    ]
    return " · ".join(part for part in said if part)


def _reply(slave: int, code: int, data: bytes) -> str:
    """Decode a frame the board sent back."""
    if code == M.FC_READ:
        said = f"reply · slave {slave} · read · {data[2]} data byte(s)"
    elif code == M.FC_WRITE:
        reg = int.from_bytes(data[2:4], "big")
        count = int.from_bytes(data[4:6], "big")
        said = f"reply · slave {slave} · write accepted · {count} register(s) at {reg:#06x}"
    else:
        said = f"reply · slave {slave} · function {code:#04x}"
    return f"{said} · {_crc(data)}"


def _where(reg: int, addr_offset: int, nbytes: int, catalog: Catalog) -> str:
    """Say which table and which settings a register address lands in."""
    offset = reg - addr_offset
    if offset >= M.ACTION_BASE:
        return f"action slot {offset - M.ACTION_BASE:#04x}"
    for table, base in M.TABLE_BASE.items():
        if base <= offset < base + TABLE_SPAN:
            byte_off = offset - base
            where = f"{TABLE_LABELS[table]} byte {byte_off:#04x}"
            named = catalog.covering(table, byte_off, nbytes)
            if not named:
                return where
            if len(named) > MAX_NAMED:
                return f"{where} · covers {len(named)} registers"
            return f"{where} · covers {', '.join(reg.key for reg in named)}"
    return f"outside every mapped table (offset {offset:#06x})"


def _crc(data: bytes) -> str:
    """Whether the frame's own checksum agrees with its contents."""
    if len(data) < MIN_FRAME:
        return ""
    want = int.from_bytes(data[-2:], "little")
    return "CRC ok" if M.crc16(data[:-2]) == want else "CRC BAD"


def decoder(addr_offset: int, catalog: Catalog):
    """Bind the decoder to one bus's address offset and register catalog."""
    return lambda direction, data: frames(
        direction, data, addr_offset=addr_offset, catalog=catalog
    )


def report(
    recorder: T.Recorder,
    *,
    version: str,
    target: Any | None,
    units: list[Any],
    catalog: Catalog,
    turnaround: float = M.TURNAROUND_START_S,
) -> str:
    """Render the recording as the file the page downloads."""
    offset = target.addr_offset if target else M.FRAME_ADDR_OFFSET
    return T.render(
        recorder,
        title=TITLE,
        facts=[
            ("program", f"jkctl {version}"),
            ("running on", f"{platform.platform()}, python {sys.version.split()[0]}"),
            ("port", f"{target.port} at {target.baud} baud 8N1" if target else None),
            (
                "settings",
                f"{target.timeout:g}s timeout, {target.retries} retries, "
                f"frame offset {target.addr_offset:#06x}, "
                f"{turnaround * 1000:.0f} ms between frames"
                if target
                else None,
            ),
            ("boards", [_named(unit) for unit in units]),
        ],
        decode=decoder(offset, catalog),
        preamble=PREAMBLE,
    )


def _named(unit: Any) -> str:
    """One board, as the report's header lists it."""
    parts = [unit.model, unit.hardware, unit.version, unit.serial]
    said = " ".join(part for part in parts if part)
    return f"BMS {unit.slave}" + (f" ({said})" if said else "")


__all__ = ["PREAMBLE", "TITLE", "decoder", "frames", "report"]
