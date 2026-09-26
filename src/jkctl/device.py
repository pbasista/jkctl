"""One BMS on the bus, read and written by field name.

Every command talks to this and to nothing below it.  It hides three things
that were measured the hard way on a live unit and are documented in
``research/windows/windows-findings.md`` §26-§29:

* the register map is byte-addressed, so a field's payload offset *is* its
  register offset, and a read of C registers returns 2C bytes;
* the device caps an FC03 read near 122 registers and answers exception 2
  above it, and also for any read running past a table's real end -- which on
  the unit measured is about 244 bytes, not the 293 the frame allows.  So
  fields are read one at a time, never a table at a stroke;
* numbers arrive big-endian, though the datasource that describes the fields
  describes the little-endian native UART frames.

A field the device does not map comes back absent rather than failing the read
around it: which fields a given model answers for varies, and a status page
that dies on the one register this board does not have is worse than a status
page with a gap in it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jkctl import protocol as P, values as V
from jkctl.errors import JkError
from jkctl.modbus import EXC_ILLEGAL_ADDRESS, Bus, ModbusError
from jkctl.registers import ACTIONS, Catalog, Register

# How far apart two wanted fields may be before it is cheaper to ask twice
# than to read the bytes in between.  A Modbus round trip costs a frame each
# way plus the turnaround; thirty-two bytes of payload it did not want is
# sixteen registers on a read that was going out anyway, which is cheaper on
# every link this runs over.
SPAN_GAP = 32


class DeviceError(JkError):
    """The BMS refused something it was asked to do."""

    # The board, or the wire to it, is what failed: a recording of the wire
    # is what says why.  See devicectl.errors.DeviceError.traceable.
    traceable = True


class WriteRefused(DeviceError):
    """The board answered a settings write with "illegal data address".

    ``unwritable`` says which of two things that meant, found out by writing
    back what the register already holds: True when the board refuses the
    address whatever it is sent, False when it took its own value back and
    so was refusing only the one it was offered.
    """

    # Already diagnosed, in words, by asking the board a second question: a
    # recording of the next attempt has nothing to add, and a notice saying
    # "try it again" would be asking for a write the plan now turns down.
    traceable = False

    def __init__(self, message: str, unwritable: bool):
        """Keep the message, and whether the address itself is refused."""
        super().__init__(message)
        self.unwritable = unwritable


def plan_spans(regs: Iterable[Register], gap: int = SPAN_GAP) -> list[tuple[int, int]]:
    """Merge a set of registers into the byte ranges that cover them.

    Returns ``(start, end)`` payload byte offsets, in order, with any two
    ranges closer together than ``gap`` joined into one.  Overlapping fields
    -- an array and a scalar the datasource lays over each other -- fall out
    as one range, which is what a reader wants anyway.
    """
    ranges = sorted((r.byte_off, r.byte_off + r.size) for r in regs)
    spans: list[list[int]] = []
    for start, end in ranges:
        if spans and start - spans[-1][1] <= gap:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [(a, b) for a, b in spans]


class Device:
    """One BMS, addressed by its Modbus slave address (its DIP-switch id)."""

    def __init__(self, bus: Bus, slave: int, catalog: Catalog | None = None):
        """Bind to slave ``slave`` on ``bus``, using ``catalog`` for field names."""
        self.bus = bus
        self.slave = slave
        self.catalog = catalog or Catalog()
        self._identity: dict[str, Any] | None = None
        # How many payload bytes of each table this board turned out to map.
        # Filled in the first time a read runs off the end, and consulted
        # afterwards so the next read does not walk into the same refusal.
        # Only a Modbus exception sets it (read_span raises on a timeout), so
        # a lost reply never shrinks a table permanently.
        self._extent: dict[str, int] = {}
        # Registers this board would not be written at all, by key, with the
        # sentence saying so.  Filled by a refused write (see _refused) and
        # consulted before the next one, so a setting the firmware does not
        # take is said once and then shown as such rather than tried again.
        self.unwritable: dict[str, str] = {}

    # --- reading ------------------------------------------------------------------------

    def read(self, key: str, table: str | None = None) -> Any:
        """Read one named field and return its decoded, scaled value."""
        return self.read_register(self.catalog.find(key, table))

    def read_register(self, reg: Register) -> Any:
        """Read one register of the catalog."""
        raw = self.bus.read_payload(self.slave, reg.table, reg.byte_off, reg.size)
        # The field is decoded in place inside a synthetic frame, so the
        # datasource offsets stay the single description of where things are.
        frame = bytearray(P.FRAME_LEN)
        frame[reg.field.off : reg.field.off + reg.size] = raw[: reg.size]
        return P.decode_field(reg.field, bytes(frame), P.MODBUS_BYTEORDER)

    def read_many(
        self, keys: Iterable[str], table: str | None = None
    ) -> dict[str, Any]:
        """Read several named fields, skipping any this device does not map.

        The fields are grouped into the byte ranges that cover them and each
        range is read whole (:func:`plan_spans`, :meth:`Bus.read_span`), so
        the cost is the bytes rather than the field count: the 68-field
        runtime table is five transactions, not sixty-eight.
        """
        wanted = [
            reg
            for reg in (self.catalog.get(key, table) for key in keys)
            if reg is not None
        ]
        out: dict[str, Any] = {}
        for name in dict.fromkeys(reg.table for reg in wanted):
            out.update(self.read_fields([r for r in wanted if r.table == name]))
        return out

    def read_fields(self, regs: list[Register]) -> dict[str, Any]:
        """Read several registers of ONE table, in as few transactions as it takes.

        A field the read could not reach -- because this board's table stops
        short of it -- is left out of the result rather than reported as an
        error, which is the same contract the one-field-at-a-time reader had.

        A unit that answers *nothing* is a different thing entirely, and used
        to be indistinguishable from a very short table: every field failed,
        every field was dropped, and the caller got an empty document that
        read like a board with no registers on it.  A failure before anything
        has been read is raised, named by the address that did not answer.
        """
        frame, covered = self.read_frame_bytes(regs)
        out: dict[str, Any] = {}
        for reg in regs:
            lo, hi = reg.byte_off, reg.byte_off + reg.size
            if any(a <= lo and hi <= b for a, b in covered):
                out[reg.key] = P.decode_field(
                    reg.field, bytes(frame), P.MODBUS_BYTEORDER
                )
        return out

    def read_frame_bytes(
        self, regs: list[Register]
    ) -> tuple[bytes, list[tuple[int, int]]]:
        """Read the bytes covering ``regs`` and say which of them arrived.

        Returns a whole synthetic 300-byte frame with the payload placed where
        the datasource says it lives, and the payload byte ranges that were
        actually read.  Everything that wants a decoded value goes through
        :meth:`read_fields`; this exists for the two callers that want the
        bytes themselves -- the register browser's raw column, and anything
        recording what a board really answered.
        """
        if not regs:
            return bytes(P.FRAME_LEN), []
        table = regs[0].table
        frame = bytearray(P.FRAME_LEN)
        covered: list[tuple[int, int]] = []
        for start, end in plan_spans(regs):
            limit = self._extent.get(table)
            if limit is not None:
                if start >= limit:
                    continue  # this board does not map that far
                end = min(end, limit)
            try:
                data = self.bus.read_span(
                    self.slave, table, start, end - start, partial=True
                )
            except ModbusError as exc:
                if covered:
                    raise
                raise DeviceError(f"address {self.slave}: {exc}") from exc
            if len(data) < end - start:
                # The device refused part of the range: that is where its
                # table ends, and every later span can be skipped outright.
                self._extent[table] = start + len(data)
            if not data:
                continue
            at = P.DATA_OFF + start
            frame[at : at + len(data)] = data
            covered.append((start, start + len(data)))
        return bytes(frame), covered

    def snapshot(self, table: str) -> dict[str, Any]:
        """Read every named field of one table that this device answers for."""
        return self.read_fields(self.catalog.table(table))

    def present(self) -> bool:
        """Whether anything answers at this address."""
        try:
            return bool(self.read("manuDeviceID"))
        except (ModbusError, OSError):
            return False

    # --- identity -----------------------------------------------------------------------

    @property
    def identity(self) -> dict[str, Any]:
        """The device-info fields, read once and remembered."""
        if self._identity is None:
            self._identity = self.read_many(_IDENTITY_KEYS, "03")
        return self._identity

    @property
    def model(self) -> str:
        """The manufacturer device id, e.g. ``JK_PB2A16S20P``."""
        return self.identity.get("manuDeviceID") or ""

    @property
    def version(self) -> str:
        """The running software version, e.g. ``15.41``."""
        return self.identity.get("softwareVersion") or ""

    # --- writing ------------------------------------------------------------------------

    def write(self, key: str, value: Any, table: str | None = None) -> None:
        """Write a coerced value to one named field."""
        self.write_register(self.catalog.find(key, table), value)

    def write_register(self, reg: Register, value: Any) -> None:
        """Write one register, refusing any the vendor's map does not mark RW.

        A field narrower than a Modbus register, or one starting on an odd
        byte, cannot be written on its own: the smallest thing the wire can
        carry is a whole 16-bit register.  Several JK settings are exactly
        that -- ``uart1ProtoNo`` and ``canProtoNo`` are the two halves of one
        register, as are ``rcvTime`` and ``rfvTime`` -- so the containing word
        is read first and only the field's own bytes replaced, leaving its
        neighbour as it was.
        """
        if not reg.writable:
            raise DeviceError(
                f"{reg.key} is read-only: JK's register map does not list it as writable"
            )
        if reg.key in self.unwritable:
            raise DeviceError(self.unwritable[reg.key])
        self._write_at(reg, reg.byte_off, V.encode(reg, value))

    def write_element(self, reg: Register, index: int, value: Any) -> None:
        """Write one element of an array field, leaving the other elements alone.

        ``cellConWireRes`` is thirty-two connection-wire resistances in one
        field, and JK's own application edits them a box at a time.  Each is
        its own whole register, so one can go out without the other
        thirty-one -- which is the difference between an editable panel and a
        command line that will not take a change unless you retype every
        value beside it.
        """
        if not reg.writable:
            raise DeviceError(
                f"{reg.key} is read-only: JK's register map does not list it as writable"
            )
        if not reg.is_array:
            raise DeviceError(f"{reg.key} is not an array: it has no element {index}")
        if not 0 <= index < reg.field.count:
            raise DeviceError(
                f"{reg.key} has elements 0..{reg.field.count - 1}, not {index}"
            )
        width = P.element_size(reg.field)
        self._write_at(
            reg,
            reg.byte_off + index * width,
            P.encode_element(reg.field, value, P.MODBUS_BYTEORDER),
        )

    def _write_at(self, reg: Register, byte_off: int, data: bytes) -> None:
        """Put ``data`` at one byte offset of a table, a whole register at a time.

        Anything narrower than a Modbus register, or starting on an odd byte,
        cannot be written on its own: the smallest thing the wire can carry is
        a whole 16-bit register.  Several JK settings are exactly that --
        ``uart1ProtoNo`` and ``canProtoNo`` are the two halves of one register,
        as are ``rcvTime`` and ``rfvTime`` -- so the containing word is read
        first and only the wanted bytes replaced, leaving the neighbour as it
        was.  That makes such a write a read-modify-write, and so not atomic:
        nothing else may be writing the same word at the same time.
        """
        start = byte_off & ~1
        end = (byte_off + len(data) + 1) & ~1
        if (start, end) != (byte_off, byte_off + len(data)):
            window = bytearray(
                self.bus.read_payload(self.slave, reg.table, start, end - start)
            )
            window[byte_off - start : byte_off - start + len(data)] = data
            data = bytes(window)
        try:
            self.bus.write_payload(self.slave, reg.table, start, data)
        except ModbusError as exc:
            if exc.exception_code != EXC_ILLEGAL_ADDRESS:
                raise
            raise self._refused(reg, start, len(data)) from exc

    def _refused(self, reg: Register, start: int, length: int) -> DeviceError:
        """Say why a write was refused as an illegal address, having asked.

        JK's firmware answers exception 2 for two different things, and
        which one it was decides what the person should do next.  So the
        words the write covered are read and written back unchanged: a board
        that refuses even its own value does not take the address at all
        (firmware 15.41 does this for the two discharge under-temperature
        settings at 0x1122, which JK's Modbus document for the model does not
        list); one that takes it back was refusing the value.  Writing back
        what a register holds changes nothing -- JK's application writes these
        pairs the same way.
        """
        addr = self.bus.table_register(reg.table, start)
        label = f"{reg.title} ({reg.key})" if reg.title else reg.key
        try:
            held = self.bus.read_payload(self.slave, reg.table, start, length)
            self.bus.write_payload(self.slave, reg.table, start, held)
        except ModbusError as exc:
            if exc.exception_code != EXC_ILLEGAL_ADDRESS:
                return DeviceError(
                    f"BMS {self.slave} refused {label} at register 0x{addr:04x}"
                    " as an illegal data address"
                )
        else:
            return WriteRefused(
                f"BMS {self.slave} refused this value of {label}: it answered"
                f" the write to register 0x{addr:04x} with exception 2 (illegal"
                " data address), but took back the value it already holds, so"
                " the setting is writable and it is this value it will not"
                " take -- usually because it would cross a related setting",
                unwritable=False,
            )
        shared = [
            r
            for r in self.catalog.table(reg.table)
            if r.byte_off < start + length and r.byte_off + r.field.size > start
        ]
        others = [r.key for r in shared if r.key != reg.key]
        message = (
            f"BMS {self.slave} does not take {label} over Modbus: it refused"
            f" the write to register 0x{addr:04x} as an illegal data address,"
            " even with the value the register already holds."
            + (f" {', '.join(others)} shares the register." if others else "")
            + " JK's application sends the same frame, so this is the"
            " board's firmware, not jkctl"
        )
        for r in shared:
            self.unwritable[r.key] = message
        return WriteRefused(message, unwritable=True)

    def set_bit(self, key: str, bit: int, on: bool, table: str | None = None) -> int:
        """Flip one bit of a bitmap register, leaving the others as they were.

        Read-modify-write: the switch word holds ten unrelated settings and the
        only way to change one over Modbus is to write all sixteen bits back.
        Returns the word that was written.
        """
        reg = self.catalog.find(key, table)
        current = int(self.read_register(reg) or 0)
        updated = current | (1 << bit) if on else current & ~(1 << bit)
        if updated != current:
            self.write_register(reg, updated)
        return updated

    # --- the action space ---------------------------------------------------------------

    def action(self, name: str, value: int = 0) -> None:
        """Fire one of the write-only action registers by name."""
        act = ACTIONS[name]
        self.bus.write_action(self.slave, act.slot, value, act.width)


# What `jkctl info` and the firmware gate need to know about a unit.  Read as a
# group and cached, because three separate commands ask for the model.
_IDENTITY_KEYS = (
    "manuDeviceID",
    "softwareVersion",
    "hardwareVersion",
    "deviceSN",
    "manufactureDate",
    "maxCells",
    "pwrOnTimes",
    "oddRunTime",
    "uart1ProtoNo",
    "uart2ProtoNo",
    "canProtoNo",
    "protocolVer",
)


def scan(
    bus: Bus, ids: Iterable[int], catalog: Catalog | None = None
) -> list[tuple[int, Device]]:
    """Return ``(id, Device)`` for every address on the bus that answers.

    Retries are turned off while sweeping: a device that is there usually
    answers the first query, and waiting out two retries per empty address
    turns a sixteen-address sweep into a minute of nothing.

    The whole sweep is done before anything comes back, rather than yielded
    address by address, so that the suppression cannot outlive it.  A
    generator hands each device over while it is still suspended inside that
    ``try``, so the obvious next line -- read the nameplate of the board the
    sweep just found -- would run on a bus with no retries left, on exactly
    the marginal wire that made them worth having.  Nothing at the call site
    shows it: ``for slave, dev in scan(...): read(dev)`` reads like a normal
    bus, and fails one frame in however many.
    """
    catalog = catalog or Catalog()
    saved = bus.retries
    found: list[tuple[int, Device]] = []
    try:
        bus.retries = 0
        for slave in ids:
            dev = Device(bus, slave, catalog)
            if dev.present():
                found.append((slave, dev))
    finally:
        bus.retries = saved
    return found


__all__ = ["SPAN_GAP", "Device", "DeviceError", "WriteRefused", "plan_spans", "scan"]
