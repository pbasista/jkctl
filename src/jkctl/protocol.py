"""JK BMS frame protocol, recovered from jk-bms-monitor 3.11.0.

Source of truth: config/en_US.jsonds shipped with the Windows application.
That file is AES-256-CBC(iv=0) over  uint32_le raw_len || zlib(json)  with the
key b"2B10F23AC94C4910AE8BCCE19E4D485B" (.rdata of protocore.dll).  The
decrypted JSON is the datasource the app's JSearchEngine walks, i.e. the exact
field layout the GUI itself uses.  It is shipped here as protocol_en.json.

Frame layout (from the datasource's own top-level item list):

    off 0   header    4 bytes  55 AA EB 90
    off 4   frameCode 1 byte   01..06 = which table follows
    off 5   counter   1 byte
    off 6   frame     293 bytes payload, laid out per table
    off 299 check     1 byte   sum8 of bytes 0..298      ("ct":"sum8","ep":298)
    ------------------------------
    total   300 bytes

Every one of the six tables measures out to exactly offset 299, which is what
validates the size model below.

Tables:
    01  settings, device -> host
    02  cell / runtime data, device -> host
    03  device info, device -> host
    04  settings, host -> device
    05  system log
    06  fault records
"""

from __future__ import annotations

import json
import math
import os
import struct
from dataclasses import dataclass, field
from typing import Literal

FRAME_LEN = 300
HEADER = bytes((0x55, 0xAA, 0xEB, 0x90))
CMD_HEADER = bytes((0xAA, 0x55, 0x90, 0xEB))
DATA_OFF = 6
CHECK_OFF = 299

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DS = os.path.join(_HERE, "protocol_en.json")

# Struct code + byte size for each datasource scalar type.  The code carries NO
# endianness prefix: the caller supplies one (see decode_field's ``byteorder``).
# The datasource itself describes the native 55AAEB90 UART frames, which are
# little-endian; the Modbus transport presents the same fields big-endian
# (standard Modbus word order), so numbers must be decoded with the byte order
# of whatever transport produced the bytes.
_SCALAR = {
    "u8": ("B", 1),
    "i8": ("b", 1),
    "u16": ("H", 2),
    "i16": ("h", 2),
    "u32": ("I", 4),
    "i32": ("i", 4),
    "u64": ("Q", 8),
    "i64": ("q", 8),
    "f32": ("f", 4),
    "f64": ("d", 8),
}

# Byte order of multi-byte numbers, per transport.
NATIVE_BYTEORDER = "<"  # 55AAEB90 UART frames are little-endian
MODBUS_BYTEORDER = ">"  # the BMS serves Modbus holding registers big-endian

# The same two orders spelled the way int.from_bytes wants them, for the
# bitmap fields, which struct cannot express.
_INT_ORDER: dict[str, Literal["little", "big"]] = {
    NATIVE_BYTEORDER: "little",
    MODBUS_BYTEORDER: "big",
}


@dataclass
class Field:
    """One item of one datasource table: what it is, and where it sits."""

    key: str  # machine name ("m"), "" for reserved fields
    label: str  # human name ("n")
    kind: str  # datasource type: n / a / bm / bv / h
    off: int  # byte offset inside the 300-byte frame
    size: int  # byte length
    ntype: str = ""  # scalar type for kind == "n"
    atype: str = ""  # element type for kind == "a"
    count: int = 0  # element / bit count
    scale: float = 1.0
    unit: str = ""
    decimals: int | None = None
    bits: dict = field(default_factory=dict)  # "sps" bit descriptions
    # The datasource's own bounds and factory default for a settable field,
    # already in scaled units ("mi"/"mx"/"dv").  93 of the 221 items carry
    # them; the rest leave these None.
    minimum: float | None = None
    maximum: float | None = None
    default: float | None = None


def _elem_size(t: str) -> int:
    return _SCALAR[t][1]


def element_size(f: Field) -> int:
    """Return the width in bytes of one element of an array field."""
    return _elem_size(f.atype or "i8")


def encode_element(f: Field, value, byteorder: str = NATIVE_BYTEORDER) -> bytes:
    """Encode one element of an array field, scaled, in the transport's order.

    The 32 connection-wire resistances are one array field of the settings
    table, and JK's own application edits them one box at a time.  Each is a
    whole 4-byte register, so one of them can be written without touching its
    neighbours -- which is the difference between an editable panel and a
    command line that demands all thirty-two values at once.
    """
    fmt, _ = _SCALAR[f.atype or "i8"]
    raw = value / f.scale if f.scale != 1.0 else value
    if fmt in ("f", "d"):
        return struct.pack(byteorder + fmt, float(value))
    return struct.pack(byteorder + fmt, int(round(raw)))


def _ntype(item: dict) -> str:
    """Return a number's scalar type, including the four the datasource forgets.

    A number with no ``nt`` is one byte, and one byte read as unsigned is what
    the datasource's own loader assumes.  Four of them -- the two heating
    thresholds and the discharge under-temperature pair -- are also given a
    range that starts at -40 degC, which no unsigned byte can hold.  They are
    signed: JK's software is C++, and a -5 it puts in a byte goes out as
    ``0xfb`` whether it calls that byte signed or not, and the board compares
    it with a temperature that is below zero half the winter.  So a missing
    type on a number whose minimum is negative is read as ``i8``, and every
    other missing type stays ``u8``, as it was.
    """
    if "nt" in item:
        return item["nt"]
    return "i8" if (item.get("mi") or 0) < 0 else ""


def _field_size(item: dict) -> int:
    t = item.get("t")
    if t == "n":
        return _elem_size(_ntype(item) or "u8")
    if t in ("a", "h"):
        return item.get("c", 1) * _elem_size(item.get("at", "i8"))
    if t in ("bm", "bv"):
        return math.ceil(item.get("c", 8) / 8)
    raise ValueError("unknown datasource field type %r" % (t,))


class Protocol:
    """The frame tables from one decrypted .jsonds datasource."""

    def __init__(self, path: str = _DEFAULT_DS):
        """Parse one decrypted datasource into its six frame tables."""
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        top = doc["vs"][0]["ss"][0]["tas"][0]
        self.name = top.get("n", "")
        self.model_key = top.get("m", "")
        frame = next(i for i in top["is"] if i.get("t") == "f")
        self.tables: dict[str, list[Field]] = {}
        self.table_names: dict[str, str] = {}
        for tab in frame["tas"]:
            code = tab["m"]
            self.table_names[code] = tab.get("n", "")
            off = DATA_OFF
            fields = []
            for item in tab["is"]:
                try:
                    size = _field_size(item)
                except (ValueError, KeyError):
                    break  # nested record tables (06) -- not needed here
                fields.append(
                    Field(
                        key=item.get("m", ""),
                        label=item.get("n", ""),
                        kind=item["t"],
                        off=off,
                        size=size,
                        ntype=_ntype(item) if item["t"] == "n" else "",
                        atype=item.get("at", ""),
                        count=item.get("c", 0),
                        scale=item.get("s", 1.0),
                        unit=item.get("u", ""),
                        decimals=item.get("dm"),
                        bits=item.get("sps", {}) or {},
                        minimum=item.get("mi"),
                        maximum=item.get("mx"),
                        default=item.get("dv"),
                    )
                )
                off += size
            self.tables[code] = fields

    def field(self, table: str, key: str) -> Field:
        """Return one named field of one table."""
        for f in self.tables[table]:
            if f.key == key:
                return f
        raise KeyError("%s not in table %s" % (key, table))

    def decode(
        self, table: str, frame: bytes, byteorder: str = NATIVE_BYTEORDER
    ) -> dict:
        """Decode a full 300-byte frame against one table.

        ``byteorder`` is the endianness of multi-byte numbers: "<" for a native
        UART frame (the default), ">" for bytes read over Modbus.
        """
        out = {}
        for f in self.tables[table]:
            if not f.key:
                continue
            out[f.key] = decode_field(f, frame, byteorder)
        return out


def _scaled(f: Field, raw_value: int) -> float | int:
    """Apply a field's scale, rounded to the resolution the device really has.

    52800 mV times 0.001 is 52.800000000000004 in binary floating point, and
    that number then travels into every JSON document and CSV row the tool
    writes.  The rounding is to the scale's own precision -- a millivolt field
    keeps three decimals -- and deliberately not to the datasource's "dm",
    which is how many digits the vendor's UI *shows*: a current stored to the
    milliamp should not lose one on the way into a log file.  Display rounding
    belongs to jkctl.values.format_value.
    """
    if f.scale == 1.0:
        return raw_value
    return round(raw_value * f.scale, max(0, -math.floor(math.log10(f.scale))))


def decode_field(f: Field, frame: bytes, byteorder: str = NATIVE_BYTEORDER):
    """Decode one field out of a frame, scaled, in the transport's byte order."""
    raw = frame[f.off : f.off + f.size]
    if f.kind == "n":
        fmt = byteorder + _SCALAR[f.ntype or "u8"][0]
        return _scaled(f, struct.unpack_from(fmt, raw)[0])
    if f.kind == "a":
        at = f.atype or "i8"
        if at in ("i8", "u8"):
            return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        fmt, sz = _SCALAR[at]
        vals = [
            struct.unpack_from(byteorder + fmt, raw, i * sz)[0] for i in range(f.count)
        ]
        return [_scaled(f, v) for v in vals]
    if f.kind in ("bm", "bv"):
        # A bitmap is a plain unsigned integer of f.size bytes -- the vendor
        # Modbus map lists sysAlarm and switchStatus as UINT32/UINT16 -- so it
        # follows the transport's byte order like every other number.  Reading
        # a 4-byte alarm word off the big-endian Modbus link as little-endian
        # would report the wrong protections entirely.
        return int.from_bytes(raw, _INT_ORDER[byteorder])
    if f.kind == "h":
        return raw
    return raw


def encode_field(f: Field, value, byteorder: str = NATIVE_BYTEORDER) -> bytes:
    """Encode ``value`` into the ``f.size`` bytes the wire expects.

    The exact inverse of :func:`decode_field`, and the only place a scale is
    undone: a scaled scalar is divided by ``f.scale`` and rounded to the
    nearest integer, because the device stores mV/mA/0.1degC integers and the
    tool works in V/A/degC.  ``byteorder`` follows the transport, as it does
    for decoding -- ">" for anything going out over Modbus.
    """
    if f.kind == "n":
        fmt = byteorder + _SCALAR[f.ntype or "u8"][0]
        if fmt.endswith(("f", "d")):
            return struct.pack(fmt, float(value))
        raw = value / f.scale if f.scale != 1.0 else value
        return struct.pack(fmt, int(round(raw)))
    if f.kind == "a":
        at = f.atype or "i8"
        if at in ("i8", "u8"):
            data = value.encode("ascii") if isinstance(value, str) else bytes(value)
            if len(data) > f.size:
                raise ValueError(
                    "%r is longer than the %d-byte field %s"
                    % (value, f.size, f.key or f.label)
                )
            return data.ljust(f.size, b"\x00")
        fmt, sz = _SCALAR[at]
        vals = list(value)
        if len(vals) != f.count:
            raise ValueError(
                "%s takes %d elements, got %d" % (f.key or f.label, f.count, len(vals))
            )
        out = bytearray(f.size)
        for i, v in enumerate(vals):
            raw = v / f.scale if f.scale != 1.0 else v
            struct.pack_into(byteorder + fmt, out, i * sz, int(round(raw)))
        return bytes(out)
    if f.kind in ("bm", "bv"):
        return int(value).to_bytes(f.size, _INT_ORDER[byteorder])
    if f.kind == "h":
        return bytes(value).ljust(f.size, b"\x00")[: f.size]
    raise ValueError("cannot encode datasource field type %r" % (f.kind,))


# ---------------------------------------------------------------- framing ---


def checksum(buf: bytes) -> int:
    """sum8 over bytes 0..298 -- datasource "ck" item: ct=sum8, ep=298."""
    return sum(buf[:CHECK_OFF]) & 0xFF


def frame_ok(buf: bytes) -> bool:
    """Whether a buffer is a whole, correctly checksummed native frame."""
    return (
        len(buf) == FRAME_LEN and buf[:4] == HEADER and buf[CHECK_OFF] == checksum(buf)
    )


def frame_type(buf: bytes) -> int:
    """Return which table a native frame carries."""
    return buf[4]


def build_command(cmd: int, value: int = 0, payload: bytes = b"") -> bytes:
    """20-byte host->device command: AA 55 90 EB cmd len value... cksum.

    Matches the dispatcher in the BMS firmware (FUN_08004490), which switches
    on byte 4 of the frame.  Checksum is sum of bytes 0..18 & 0xFF.
    """
    body = payload if payload else struct.pack("<I", value)
    buf = bytearray(20)
    buf[0:4] = CMD_HEADER
    buf[4] = cmd & 0xFF
    buf[5] = len(body)
    buf[6 : 6 + len(body)] = body
    buf[19] = sum(buf[:19]) & 0xFF
    return bytes(buf)


# Verified read-only commands (BMS firmware dispatcher, case label == cmd - 1):
CMD_READ_SETTINGS_AND_INFO = 0x96  # emits table 01 + table 03
CMD_READ_DEVICE_INFO = 0x97  # emits table 03
CMD_READ_CELL_INFO = 0xC3  # emits table 02
# 0x18 is a SETTINGS WRITE that persists to EEPROM -- never send it to probe.
# 0xFF writes 0x5AA5 to a backup register and resets into the bootloader.
