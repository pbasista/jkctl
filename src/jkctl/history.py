"""The board's own stored fault records: their layout, and reading them.

The datasource's table 06 is twelve records of twenty-four bytes each, and
each one is a snapshot of the whole pack at the moment something tripped:
when it happened, what it was, which switches were closed, which cell was
highest and lowest and at what voltage, the pack voltage and current, the
capacity, three temperatures and the heater current.  A history of numbers
becomes a history of sentences once the codes are named
(:mod:`jkctl.logcodes`).

**Where it lives over Modbus is not known.**  The vendor application's four
base getters return ``+0x000/0x200/0x400/0x600`` and stop, which covers
frames 01, 02, 03 and the action space and nothing else; frames 05 and 06 are
served over the native UART channel, and whether the Modbus interface maps
them anywhere is unanswered.  ``+0x800`` and ``+0xA00`` are the candidates by
the same pattern, and reading them is harmless, so :func:`jkctl.probe` sweeps
them and this module reads whichever one answers.  Until a board answers,
:func:`read` reports that this one does not map it -- which is a different
sentence from "there are no records", and is the honest one.

The record layout, by contrast, *is* known: it is in the datasource, in the
same file every other field in this program comes from.  So the decoder is
testable without a device, and it is tested that way.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime

from jkctl import identity as I, logcodes
from jkctl.device import Device
from jkctl.errors import JkError
from jkctl.modbus import MAX_READ_CHUNK, ModbusError

# The table the datasource calls "故障信息" -- fault records.
TABLE = "06"

# Where the two undocumented frames might be mapped, as offsets from
# frameAddrOffset, following the +0x200 per frame the four known ones use.
CANDIDATE_BASES = {"05": 0x800, "06": 0xA00}

# One record, laid out as the datasource lays it out: rtcCount u32, logCode u8,
# switchSta (a four-bit bitmap, so one byte), maxVolCellNo u8, minVolCellNo u8,
# then six u16 readings, then four one-byte ones.
#
# Twenty-four bytes, which the datasource confirms twice over: the header is
# three bytes, "record 1" is one of these, "records 2-12" is an array of 264
# bytes -- eleven more of them -- and two bytes are reserved, which is 293
# exactly, the payload every table measures out to.
#
# The four one-byte readings are three temperatures and the heater current,
# and the datasource gives them no type at all.  They are read signed: a
# charge-under-temperature record is one of the things this history is *for*,
# and a below-freezing reading is the whole point of it.
RECORD_BYTES = 24
RECORD_FORMAT = ">IBBBBHHHHHHbbbb"
assert struct.calcsize(RECORD_FORMAT) == RECORD_BYTES

# The header in front of the records: the index the first one carries, and how
# many follow.
HEADER_BYTES = 3

# How many records one frame holds.
RECORDS_PER_FRAME = 12

# The four bits of switchSta, in the datasource's own order.
SWITCHES = ("charging", "discharging", "balancing", "heating")


class HistoryError(JkError):
    """The stored records could not be read."""


@dataclass(frozen=True)
class Record:
    """One stored fault record: what happened, and what the pack looked like."""

    index: int
    rtc: int
    code: int
    switches: int
    max_cell_no: int
    min_cell_no: int
    cell_max_v: float
    cell_min_v: float
    pack_v: float
    pack_a: float
    remaining_ah: float
    full_ah: float
    max_temp_c: int
    min_temp_c: int
    mos_temp_c: int
    heat_a: float

    @property
    def name(self) -> str:
        """What the vendor's application calls this event."""
        return logcodes.name(self.code)

    @property
    def when(self) -> datetime | None:
        """When it happened, from the board's own real-time clock."""
        return I.rtc_to_datetime(self.rtc)

    @property
    def closed(self) -> list[str]:
        """Which switches were closed at the moment it tripped."""
        return [word for bit, word in enumerate(SWITCHES) if self.switches >> bit & 1]


def decode_record(data: bytes, index: int = 0) -> Record:
    """Decode one record.

    Scales are the datasource's: cell voltages in millivolts, the pack in
    centivolts, current and capacity in tenths.  Numbers are big-endian
    because everything the Modbus interface serves is (findings §29).
    """
    if len(data) < RECORD_BYTES:
        raise HistoryError(f"a record is {RECORD_BYTES} bytes, got {len(data)}")
    (
        rtc,
        code,
        switches,
        max_no,
        min_no,
        cell_max,
        cell_min,
        pack_v,
        pack_a,
        remaining,
        full,
        max_temp,
        min_temp,
        mos_temp,
        heat,
    ) = struct.unpack_from(RECORD_FORMAT, data)
    return Record(
        index=index,
        rtc=rtc,
        code=code,
        switches=switches,
        max_cell_no=max_no,
        min_cell_no=min_no,
        cell_max_v=round(cell_max * 0.001, 3),
        cell_min_v=round(cell_min * 0.001, 3),
        pack_v=round(pack_v * 0.01, 2),
        pack_a=round(pack_a * 0.1, 1),
        remaining_ah=round(remaining * 0.1, 1),
        full_ah=round(full * 0.1, 1),
        max_temp_c=max_temp,
        min_temp_c=min_temp,
        mos_temp_c=mos_temp,
        heat_a=round(heat * 0.1, 1),
    )


def decode_frame(payload: bytes) -> list[Record]:
    """Decode a whole table-06 payload into the records it carries."""
    if len(payload) < HEADER_BYTES:
        raise HistoryError("the reply is too short to be a fault-record frame")
    begin = int.from_bytes(payload[0:2], "big")
    count = payload[2]
    records = []
    for n in range(min(count, RECORDS_PER_FRAME)):
        at = HEADER_BYTES + n * RECORD_BYTES
        if at + RECORD_BYTES > len(payload):
            break
        records.append(decode_record(payload[at : at + RECORD_BYTES], begin + n))
    return records


def read(device: Device, base: int | None = None) -> list[Record]:
    """Read the stored fault records, if this board maps them anywhere.

    ``base`` is the offset from ``frameAddrOffset`` to try; the default is the
    candidate the pattern of the four known frames suggests.  A board that
    does not map it refuses the read, and that refusal is reported as what it
    is rather than as an empty history -- "no records" and "this board does
    not answer there" are different answers and the second one is the one we
    can actually stand behind.
    """
    where = CANDIDATE_BASES[TABLE] if base is None else base
    register = device.bus.addr_offset + where
    want = HEADER_BYTES + RECORDS_PER_FRAME * RECORD_BYTES
    # Chunked like every other read: the whole frame is 146 registers and the
    # device caps a single FC03 near 122 (findings §28).
    try:
        payload = bytearray()
        regs = (want + 1) // 2
        while len(payload) // 2 < regs:
            done = len(payload) // 2
            payload += device.bus.read_registers(
                device.slave,
                register + done,
                min(MAX_READ_CHUNK, regs - done),
            )
    except (ModbusError, OSError) as exc:
        raise HistoryError(
            f"this board does not answer at register 0x{register:04X}, where the "
            f"stored records would be if the Modbus interface maps them ({exc}). "
            "Which window serves them, if any, is an open question -- see "
            "docs/reference.md."
        ) from exc
    return decode_frame(bytes(payload))


__all__ = [
    "CANDIDATE_BASES",
    "RECORDS_PER_FRAME",
    "RECORD_BYTES",
    "TABLE",
    "HistoryError",
    "Record",
    "decode_frame",
    "decode_record",
    "read",
]
