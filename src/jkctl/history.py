"""The board's own stored fault records: their layout, and reading them.

Each record is a twenty-four-byte snapshot of the whole pack at the moment
something tripped: when it happened, what it was, which switches were closed,
which cell was highest and lowest and at what voltage, the pack voltage and
current, the capacity, three temperatures and the heater current.  A history
of numbers becomes a history of sentences once the codes are named
(:mod:`jkctl.logcodes`).

**The records are not served over Modbus.**  This was an open question and is
now settled, from three sources that agree: the vendor's RS485 register
document lists no history window; the firmware's own request dispatcher bounds
a read to registers ``0x1000``-``0x15FF`` (frames 01, 02 and 03) and answers
anything at ``0x1600`` or above with an error; and the vendor application's own
bus simulator maps only ``base+0x000..0x600``.  The vendor *reads the records
over Bluetooth*, on a separate path this Modbus/RS485 interface does not
expose.  So :func:`read` -- the over-the-wire attempt -- will not find them on
a board running stock firmware, and says so plainly.

**They can be recovered from a full flash dump.**  A patched board (see
:mod:`jkctl.flashdump`) returns the whole 128 KiB image, and the records live
in it: on the audited ``JK_PB2A16S20P`` V15.41 they sit in a three-page ring
at ``0x08019000`` (a firmware literal at ``0x080094b8`` points there), written
one 1 KiB page at a time, forty-two records to a page.  In flash they are
stored **little-endian** -- native to the STM32, not the big-endian the Modbus
interface would present -- which is the one thing that differs from the wire
layout.  :func:`read_dump` walks those pages and decodes every valid record.

The record layout itself is the datasource's, the same file every other field
in this program comes from, so both decoders are testable without a device --
and :func:`read_dump` is tested against a real captured image.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
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
# Over the wire (were the Modbus interface to serve them) the numbers would be
# big-endian, like everything else it serves (findings §29).  In flash the same
# fields are stored little-endian, native to the STM32.  The pack current is
# signed -- a captured discharge record reads 0xFE47 there, which is -45.7 A,
# not the +6507.9 A an unsigned read would make of it -- while the voltages and
# capacities are not; the four temperatures are signed for the below-freezing
# readings a cold-charge record is kept for.
RECORD_FORMAT = ">IBBBBHHHhHHbbbb"
STORED_RECORD_FORMAT = "<IBBBBHHHhHHbbbb"
assert struct.calcsize(RECORD_FORMAT) == RECORD_BYTES
assert struct.calcsize(STORED_RECORD_FORMAT) == RECORD_BYTES

# Where the records live in the MCU's flash, and how they are packed there, on
# the one target whose full image has been audited (JK_PB2A16S20P V15.41).  The
# base is a firmware constant -- a literal at 0x080094b8 -- and the ring is
# three 1 KiB pages; each page starts a fresh run of records at its own base
# and holds forty-two, so the pages are walked one at a time rather than as one
# flat array.
FLASH_BASE = 0x08000000
STORAGE_BASE = 0x08019000
STORAGE_PAGE = 1024
RECORDS_PER_PAGE = STORAGE_PAGE // RECORD_BYTES  # 42, with 16 bytes of slack

# The header in front of the records: the index the first one carries, and how
# many follow.
HEADER_BYTES = 3

# How many records one frame holds.
RECORDS_PER_FRAME = 12

# The four bits of switchSta, in the datasource's own order.
SWITCHES = ("charging", "discharging", "balancing", "heating")

# What a written record slot looks like, for telling one from erased or
# half-written flash: the switch byte is a four-bit bitmap, and the two cell
# numbers index a pack (generous upper bound -- the largest JK pack is 32S).
SWITCH_BITMAP_MAX = 0x0F
CELL_NO_MAX = 64


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
    """Decode one record in the wire (big-endian) layout.

    Scales are the datasource's: cell voltages in millivolts, the pack in
    centivolts, current and capacity in tenths.  Numbers are big-endian
    because everything the Modbus interface serves is (findings §29).
    """
    return _decode(data, index, RECORD_FORMAT)


def decode_stored_record(data: bytes, index: int = 0) -> Record:
    """Decode one record as it is packed in flash: little-endian, native.

    The fields and scales are identical to :func:`decode_record`; only the byte
    order differs, because flash keeps the STM32's native little-endian while
    the (hypothetical) Modbus frame would be big-endian.
    """
    return _decode(data, index, STORED_RECORD_FORMAT)


def _decode(data: bytes, index: int, fmt: str) -> Record:
    """Decode one record with the given struct format."""
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
    ) = struct.unpack_from(fmt, data)
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


def _is_erased(chunk: bytes) -> bool:
    """Report whether a flash slot has never been written (is all ones)."""
    return chunk.count(0xFF) == len(chunk)


def _is_stored_record(chunk: bytes) -> bool:
    """Whether a flash slot holds a plausible record rather than slack or noise.

    The record region sits above the application in otherwise-erased flash, so
    this only has to tell a real record from an erased or half-written slot: a
    named event code, a four-bit switch bitmap, and cell numbers within a pack
    are enough, and together they do not fire on erased (all-``0xFF``) bytes.
    """
    if len(chunk) < RECORD_BYTES or _is_erased(chunk):
        return False
    code = chunk[4]
    switches = chunk[5]
    max_no, min_no = chunk[6], chunk[7]
    return (
        logcodes.known(code)
        and switches <= SWITCH_BITMAP_MAX
        and max_no <= CELL_NO_MAX
        and min_no <= CELL_NO_MAX
    )


def read_dump(image: bytes, *, storage_base: int = STORAGE_BASE) -> list[Record]:
    """Recover every stored fault record from a full flash image.

    Walks the record ring page by page from ``storage_base`` (the offset is
    relative to :data:`FLASH_BASE`), decodes each written slot with the
    little-endian flash layout, skips erased and half-written slots, and
    returns the records newest first.  A dump that does not reach the region,
    or whose region is erased, yields an empty list rather than an error --
    "this capture holds no records" is a real, honest answer.
    """
    start = storage_base - FLASH_BASE
    if start < 0:
        raise HistoryError(f"storage base 0x{storage_base:08X} is below flash")
    records: list[Record] = []
    page_off = start
    while page_off + RECORD_BYTES <= len(image):
        for slot in range(RECORDS_PER_PAGE):
            at = page_off + slot * RECORD_BYTES
            chunk = image[at : at + RECORD_BYTES]
            if len(chunk) < RECORD_BYTES:
                break
            if _is_stored_record(chunk):
                records.append(decode_stored_record(chunk))
        page_off += STORAGE_PAGE
    records.sort(key=lambda r: r.rtc, reverse=True)
    return [replace(record, index=index) for index, record in enumerate(records)]


def read(device: Device, base: int | None = None) -> list[Record]:
    """Attempt to read the stored fault records over Modbus.

    This does not work on stock firmware, and the reason is now known rather
    than guessed: the firmware bounds a read to frames 01-03 and the vendor
    reads the records over Bluetooth instead (see the module docstring).  The
    attempt is kept because a board could differ, ``base`` lets one point it
    elsewhere, and a clear "this board does not serve them here" beats a
    pretend empty history.  To actually recover the records, take a full flash
    dump and pass it to :func:`read_dump`.
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
            f"this board does not serve the stored records over Modbus ({exc}). "
            f"It bounds a read to frames 01-03 (registers 0x1000-0x15FF) and keeps "
            f"the records where only Bluetooth reaches them; register 0x{register:04X} "
            "is refused. To recover them, take a full flash dump of a patched board "
            "('jkctl firmware dump-flash') and read it with 'jkctl history "
            "--from-flash-dump'."
        ) from exc
    return decode_frame(bytes(payload))


__all__ = [
    "CANDIDATE_BASES",
    "FLASH_BASE",
    "RECORDS_PER_FRAME",
    "RECORDS_PER_PAGE",
    "RECORD_BYTES",
    "STORAGE_BASE",
    "STORAGE_PAGE",
    "TABLE",
    "HistoryError",
    "Record",
    "decode_frame",
    "decode_record",
    "decode_stored_record",
    "read",
    "read_dump",
]
