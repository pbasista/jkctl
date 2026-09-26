"""Modbus RTU master + the JK register map, as implemented by jk-bms-monitor.

Wire format -- recovered verbatim from FUN_140015560 @ 0x140015560:

    [slave] [func] [reg_hi] [reg_lo] [cnt_hi] [cnt_lo]
    (func 0x10 only:) [byte_count] [data ...]
    [crc_lo] [crc_hi]                       crc16_modbus over everything before

    slave  = global.addrCode from config/main.json (default 1)
    func   = 0x03 (read holding registers) or 0x10 (write multiple registers)

Response framing -- FUN_140013f80 @ 0x140013f80:
    byte 0 must equal the slave address; byte 1 the function code (bit 7 set =
    exception, total length 5); for 0x03 total length = byte_count + 5, for
    0x10 it is 8; CRC little-endian over length-2.

Register map -- FUN_140019c80 @ 0x140019c80 (the app's own bus simulator) maps
a register to a datasource table, and FUN_140010730/750/770/790 supply the
bases.  With base = frameAddrOffset (config/main.json "0x1000"):

    base + 0x000 .. 0x1FF   frame/01   settings
    base + 0x200 .. 0x3FF   frame/02   cell / runtime data
    base + 0x400 .. 0x5FF   frame/03   device info
    base + 0x600 + id       action / command space

Inside a table the register address IS the byte offset of the field within the
frame payload (the 293 bytes after the 6-byte 55AAEB90 header): FC03 at register
(table_base + B) with count C returns C*2 bytes = payload[B : B+2C].  So a field
at payload byte offset B is read at register table_base + B, count = bytes/2.
This matches the vendor Modbus PDF verbatim -- its "address" column is the byte
offset (0x0000, 0x0004, 0x0008 for consecutive UINT32s) and doubles as the
register offset from the section base.

  Proven on the live BMS (2026-09-07, 3rd probe) by an overlap test: two reads
  R0 = FC03(0x1400,32) and R1 = FC03(0x1420,32) satisfy R0[32:64] == R1[0:32],
  i.e. register 0x1420 returns byte offset 0x20 (32), NOT word 32 (byte 64).
  Under this model deviceSN@80 decodes to "40705491867" and manufactureDate@72
  to "241103" -- real values at their exact datasource offsets.

Two device quirks the reader must respect (from the same probe):
  * FC03 quantity is capped near 122 registers: count 122 works, 124 answers
    Modbus exception 2.  So a read is split into chunks of at most
    MAX_READ_CHUNK registers -- read_span does that, and is how anything
    wanting more than one field asks for it.
  * Reads appear to require an even (word-aligned) byte offset -- reading at an
    odd register was refused -- so read_payload word-aligns and trims.
  * A read must not run past the table's real end (some tables are shorter than
    293 bytes); that also answers exception 2 / silence.

And a fourth, which costs every caller and not only the reader: the board
will not be spoken to immediately after it has answered.  A request sent a
couple of milliseconds behind the previous reply is not refused, it is simply
not heard -- no exception code, no reply, only silence -- while the same
request sent after a pause is answered first time.  So every transaction
waits out a turnaround delay first (TURNAROUND_START_S), and a board that was
answering and then goes quiet makes that delay longer.

Action ids seen in the app's RX dispatcher (FUN_1400138e0 @ 0x1400138e0
switches on (register - base) - 0x600):
    0x0a 0x0c 0x0e 0x1c 0x1e 0x20 0x24   -> FUN_140013290
    0x22                                  -> FUN_140013c60   (25 s timeout)
    0x26                                  -> FUN_140014610   FIRMWARE UPGRADE
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from jkctl.errors import JkError

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 115200  # config/main.json: 115200 8N1
QUERY_TIMEOUT = 0.35  # config/main.json: queryTimeout = 350 ms
DEFAULT_SLAVE = 1  # config/main.json: global.addrCode = 1

FRAME_ADDR_OFFSET = 0x1000  # config/main.json: global.frameAddrOffset

TABLE_BASE = {  # table code -> register base (add the offset)
    "01": 0x000,
    "02": 0x200,
    "03": 0x400,
}
ACTION_BASE = 0x600
ACTION_UPGRADE = 0x26  # -> register 0x1626 by default

FC_READ = 0x03
FC_WRITE = 0x10
MAX_READ_REGS = 125  # Modbus protocol ceiling
# The live BMS rejects large reads with exception 2 (illegal data address): a
# 125-register read at 0x1400 fails while 120 works, so the device caps FC03
# quantity somewhere around there.  We never actually need a big read -- every
# field the tool wants is <=32 registers -- so keep per-request reads small and
# well under the cap.  read_frame (diagnostic only) chunks by this and tolerates
# the device refusing a chunk near the end of a table.
MAX_READ_CHUNK = 32

# Writes have never been sized against the real device -- the read cap above was
# measured, this one was not -- so keep each FC16 well inside the smallest thing
# the device is known to accept.  `jkctl probe --probe-writes` measures the real
# ceiling by rewriting registers with the values just read from them.
MAX_WRITE_CHUNK = 16

# Reply lengths: an exception reply is five bytes, and an FC16 acknowledgement
# echoes the request's first six bytes plus its CRC.
MIN_REPLY = 5
WRITE_REPLY_LEN = 8

# How long to wait before trying a lost or garbled reply again.
RETRY_PAUSE_S = 0.03

# How long the bus is left alone after a board has finished answering, before
# the next request goes out.
#
# Measured on a live JK_PB2A16S20P (2026-09-25, from a recorded trace): every
# request sent 2 ms after the previous reply went unanswered -- nine of them in
# a row, reads and writes alike -- and every request sent after a pause was
# answered first time.  The board is not refusing anything; it is not listening
# yet.  Nothing in Modbus says it should be, either: RTU's own rule is 3.5
# character times, which at 115200 baud is a third of a millisecond, and this
# board wants orders of magnitude more than that.
#
# The value below is where a bus *starts*, not what it settles at, because the
# trace says only that 2 ms is too little and that 380 ms is plenty -- nothing
# in between was ever tried.  So the bus finds out for itself: a board that has
# answered before and then says nothing has been spoken to too soon, and the
# delay doubles.  It stops at TURNAROUND_MAX_S, past which the query timeout
# would expire first and waiting longer buys nothing.
TURNAROUND_START_S = 0.03
TURNAROUND_MAX_S = 0.40

# How long to let a USB-RS485 adapter settle after opening the port.
POST_OPEN_SETTLE_S = 0.10

# Modbus exception codes the BMS actually returns (vendor document, section 1).
EXC_ILLEGAL_FUNCTION = 1
EXC_ILLEGAL_ADDRESS = 2
EXC_ILLEGAL_VALUE = 3
EXCEPTION_NAMES = {
    EXC_ILLEGAL_FUNCTION: "illegal function",
    EXC_ILLEGAL_ADDRESS: "illegal data address",
    EXC_ILLEGAL_VALUE: "illegal data value",
    4: "slave device failure",
}


def exception_text(code: int) -> str:
    """Name a Modbus exception code the way the vendor document does."""
    return "modbus exception %d (%s)" % (code, EXCEPTION_NAMES.get(code, "unknown"))


def crc16(buf: bytes) -> int:
    """CRC-16/MODBUS over ``buf`` -- the reflected 0xA001 form, seeded 0xFFFF."""
    crc = 0xFFFF
    for b in buf:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusError(JkError):
    """The BMS did not answer, answered garbage, or refused the request."""

    # The board, or the wire to it, is what failed: a recording of the wire
    # is what says why.  See devicectl.errors.DeviceError.traceable.
    traceable = True

    def __init__(self, message: str, exception_code: int | None = None):
        """Record the message and, when the device answered, its exception code."""
        super().__init__(message)
        self.exception_code = exception_code


class Wire(Protocol):
    """The half of a serial port something answers on.

    A simulated board writes its reply and never reads: it is fed the frames
    it should see rather than polling for them.  Everything here is passed
    positionally, so a stand-in may name the arguments what it likes.
    """

    def write(self, data: bytes, /) -> Any:
        """Put bytes on the wire."""

    def flush(self) -> None:
        """Let anything buffered go."""

    def read(self, n: int = 1, /) -> bytes:
        """Take up to ``n`` bytes."""


class SerialLink(Wire, Protocol):
    """A whole serial port, as :class:`Bus` drives one.

    pyserial's shape, and the reason ``Bus(link=...)`` works at all: a
    loopback, a scripted device or a real adapter are the same thing from
    here.  Written down because it used to be a sentence in a docstring, and
    four classes in two repositories were holding themselves to it by hand.
    """

    @property
    def in_waiting(self) -> int:
        """How many bytes are waiting to be read."""

    def reset_input_buffer(self) -> None:
        """Drop anything unread."""

    def reset_output_buffer(self) -> None:
        """Drop anything unsent."""

    def close(self) -> None:
        """Let the port go."""


class Bus:
    """Minimal Modbus RTU master over a serial port."""

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        timeout: float = QUERY_TIMEOUT,
        addr_offset: int = FRAME_ADDR_OFFSET,
        log=None,
        retries: int = 2,
        link: SerialLink | None = None,
        turnaround: float | None = None,
    ):
        """Open the port (or adopt ``link``, which is how the tests drive this).

        ``link`` is anything of :class:`SerialLink`'s shape.  It exists so
        the master can be exercised against a scripted device with no serial
        port in sight.
        """
        self.ser: SerialLink
        if link is not None:
            self.ser = link
        else:
            try:
                import serial
            except ImportError:  # pragma: no cover
                raise SystemExit("pyserial missing:  pip install pyserial")
            self.ser = serial.Serial(port, baud, 8, "N", 1, timeout=timeout)
        self.timeout = timeout
        self.addr_offset = addr_offset
        self.log = log
        self.note: Callable[[str], None] | None = None
        """Told, in words, about anything worth recording that is not a frame."""
        self.retries = retries
        # A board on a wire needs the delay; a scripted stand-in on the end of
        # an injected link is not on a wire and would only be slowed down by
        # it, the same reasoning that skips the adapter settle below.
        self.turnaround = (
            (TURNAROUND_START_S if link is None else 0.0)
            if turnaround is None
            else turnaround
        )
        self._ready_at = 0.0
        self._answered: set[int] = set()
        # Some USB-RS485 adapters need a moment after open before the first
        # frame is carried cleanly (direction control settling); on a slow host
        # the very first query is otherwise lost.  Settle and clear both
        # buffers.  An injected link is not an adapter and needs neither.
        if link is None:
            time.sleep(POST_OPEN_SETTLE_S)
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:  # noqa: BLE001 - a port with nothing to flush is flushed
            pass

    def __enter__(self) -> "Bus":
        """Return the open bus, so a caller can use it in a ``with`` block."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Close the port however the block ended."""
        self.close()

    def close(self):
        """Close the serial port."""
        self.ser.close()

    # -- raw ---------------------------------------------------------------

    def write_raw(self, data: bytes):
        """Put bytes on the wire, tracing them when --trace is on."""
        if self.log:
            self.log("TX", data)
        self.ser.write(data)
        self.ser.flush()

    def read_raw(self, timeout: float, max_bytes: int = 4096) -> bytes:
        """Read whatever arrives within ``timeout``, plus a short inter-byte grace."""
        end = time.time() + timeout
        buf = bytearray()
        while time.time() < end and len(buf) < max_bytes:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                end = time.time() + 0.05  # short inter-byte grace
            else:
                time.sleep(0.005)
        if buf and self.log:
            self.log("RX", bytes(buf))
        return bytes(buf)

    # -- keeping off the board's toes --------------------------------------

    def _settle(self) -> None:
        """Wait, if need be, for the board to be ready to be spoken to again."""
        wait = self._ready_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _spoke(self) -> None:
        """Note that the wire has just been used, so the next frame waits."""
        self._ready_at = time.monotonic() + self.turnaround

    def _slow_down(self) -> None:
        """Leave a longer delay from now on: a board that was there went quiet.

        Only ever called about a board that has already answered on this bus,
        so an address nobody is at -- fifteen of the sixteen a scan tries --
        does not drag the whole bus down to its slowest setting.
        """
        if self.turnaround >= TURNAROUND_MAX_S:
            return
        self.turnaround = min(self.turnaround * 2, TURNAROUND_MAX_S)
        if self.note:
            self.note(
                "nothing came back from a board that was answering; "
                f"leaving {self.turnaround * 1000:.0f} ms between frames from now on"
            )

    # -- modbus ------------------------------------------------------------

    def _txn(self, req: bytes, expect: int) -> bytes:
        """Send one request and read whatever comes back before the timeout."""
        self._settle()
        self.ser.reset_input_buffer()
        self.write_raw(req)
        end = time.time() + self.timeout
        buf = bytearray()
        while time.time() < end and len(buf) < expect:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                end = time.time() + 0.05
            else:
                time.sleep(0.005)
        self._spoke()
        # Logged even when nothing came back, which is the one case worth
        # logging most.  A transaction that times out used to leave a TX in
        # the trace with no RX after it, which looks exactly like the end of
        # the trace -- so the failure everybody actually hits, "no/short
        # response", was the one failure the trace did not record.
        if self.log:
            self.log("RX", bytes(buf))
        if buf:
            self._answered.add(req[0])
        elif req[0] in self._answered:
            self._slow_down()
        return bytes(buf)

    def _tried(self, once: Callable[[], bytes], retries: int) -> bytes:
        """Run one transaction again while it is worth running again.

        A short or corrupt reply is a comms fault and is tried again; a Modbus
        exception is the device's own answer and will not change.
        """
        for attempt in range(retries + 1):
            try:
                return once()
            except ModbusError as exc:
                if exc.exception_code is not None:
                    raise  # device answered; retrying is pointless
                if attempt == retries:
                    raise
                time.sleep(RETRY_PAUSE_S)
        raise ModbusError("unreachable")  # pragma: no cover

    def read_registers(self, slave: int, reg: int, count: int) -> bytes:
        """Read holding registers, retrying only what is worth retrying."""
        return self._tried(
            lambda: self._read_registers_once(slave, reg, count), self.retries
        )

    def _read_registers_once(self, slave: int, reg: int, count: int) -> bytes:
        body = (
            bytes([slave, FC_READ]) + reg.to_bytes(2, "big") + count.to_bytes(2, "big")
        )
        resp = self._txn(body + crc16(body).to_bytes(2, "little"), 5 + 2 * count)
        if len(resp) < MIN_REPLY:
            raise ModbusError("no/short response")
        if resp[0] != slave:
            raise ModbusError("wrong slave in reply: %02x" % resp[0])
        if resp[1] == FC_READ | 0x80:
            raise ModbusError(exception_text(resp[2]), resp[2])
        if resp[1] != FC_READ:
            raise ModbusError("unexpected function %02x" % resp[1])
        n = resp[2]
        if len(resp) < 5 + n:
            raise ModbusError("truncated (%d of %d)" % (len(resp), 5 + n))
        if int.from_bytes(resp[3 + n : 5 + n], "little") != crc16(resp[: 3 + n]):
            raise ModbusError("bad CRC")
        return resp[3 : 3 + n]

    def write_registers(
        self,
        slave: int,
        reg: int,
        data: bytes,
        expect_reply: bool = True,
        retries: int | None = None,
    ) -> bytes:
        """FC 0x10.  data must be an even number of bytes (2 per register).

        Retried like a read, and for the same reason: a lost frame on this bus
        is ordinary, and the only thing that told the difference between "the
        board refuses this" and "the board did not hear it" was that a read
        got three goes and a write got one.  Writing a register twice with the
        same value leaves it where writing it once does, so a retry costs
        nothing -- which is why ``retries=0`` exists for the one caller where
        that is not true: an action slot is a button, not a value, and
        ``shutdown`` is *expected* to leave the question unanswered.
        """
        if len(data) % 2:
            data += b"\x00"
        count = len(data) // 2
        body = (
            bytes([slave, FC_WRITE])
            + reg.to_bytes(2, "big")
            + count.to_bytes(2, "big")
            + bytes([len(data)])
            + data
        )
        req = body + crc16(body).to_bytes(2, "little")
        if not expect_reply:
            self._settle()
            self.ser.reset_input_buffer()
            self.write_raw(req)
            self._spoke()
            return b""
        return self._tried(
            lambda: self._write_registers_once(req),
            self.retries if retries is None else retries,
        )

    def _write_registers_once(self, req: bytes) -> bytes:
        """One FC16 exchange, with no retry of its own."""
        resp = self._txn(req, WRITE_REPLY_LEN)
        if len(resp) < MIN_REPLY:
            raise ModbusError("no/short response")
        if resp[1] == FC_WRITE | 0x80:
            raise ModbusError(exception_text(resp[2]), resp[2])
        return resp

    # -- JK helpers --------------------------------------------------------

    def write_payload(
        self, slave: int, table: str, payload_off: int, data: bytes
    ) -> None:
        """Write ``data`` at byte ``payload_off`` of a table's payload.

        The same byte-addressed map the reads use, so a field at payload byte
        offset B is written at register ``table_base + B`` with a count of
        ``len(data)//2``.  Both the offset and the length must be even: the
        device refuses a non-word-aligned access (findings §28), and unlike a
        read, a write cannot be aligned down and trimmed -- that would put the
        neighbouring byte back to a value nobody asked for.
        """
        if payload_off % 2 or len(data) % 2:
            raise ModbusError(
                "a write must start and end on a word boundary "
                "(offset %d, %d bytes)" % (payload_off, len(data))
            )
        base = self.table_register(table)
        for done in range(0, len(data), 2 * MAX_WRITE_CHUNK):
            chunk = data[done : done + 2 * MAX_WRITE_CHUNK]
            self.write_registers(slave, base + payload_off + done, chunk)

    def write_action(
        self,
        slave: int,
        slot: int,
        value: int,
        width: int = 2,
        expect_reply: bool = True,
    ) -> None:
        """Write one action slot in the command space at ``base + 0x600``.

        Action offsets are slot numbers, not byte offsets into any payload.

        Never retried.  A setting is a value and writing it twice is writing
        it once; an action is a button, and the board is entitled to answer a
        press by doing the thing and saying nothing -- ``shutdown`` stops
        answering on the bus, which is the point of it.
        """
        self.write_registers(
            slave,
            self.action_register(slot),
            value.to_bytes(width, "big"),
            expect_reply=expect_reply,
            retries=0,
        )

    def table_register(self, table: str, byte_off: int = 0) -> int:
        """Register for a byte offset inside a table (register == byte offset)."""
        return self.addr_offset + TABLE_BASE[table] + byte_off

    def action_register(self, action_id: int) -> int:
        """Return the register one action slot lives at."""
        return self.addr_offset + ACTION_BASE + action_id

    def read_payload(
        self, slave: int, table: str, payload_off: int, nbytes: int
    ) -> bytes:
        """Read ``nbytes`` of a table's payload starting at byte ``payload_off``.

        The map is byte-addressed (register == byte offset; a read of C registers
        returns 2*C bytes -- see the module docstring).  Reads are word-aligned
        (the device refuses odd offsets) and split into chunks well under the
        device's ~122-register quantity cap.
        """
        base = self.table_register(table)
        start = payload_off & ~1  # word-align down (even offset)
        lead = payload_off - start  # 0 or 1 byte before the field
        nregs = (lead + nbytes + 1) // 2
        out = bytearray()
        done = 0
        while done < nregs:
            n = min(MAX_READ_CHUNK, nregs - done)
            out += self.read_registers(slave, base + start + 2 * done, n)
            done += n
        return bytes(out[lead : lead + nbytes])

    def read_span(
        self, slave: int, table: str, payload_off: int, nbytes: int, *, partial=False
    ) -> bytes:
        """Read a run of a table's payload in as few transactions as the cap allows.

        The same byte-addressed, word-aligned map :meth:`read_payload` uses --
        this one exists to be given a *range* rather than a field, so a caller
        that wants forty fields out of one table pays for the bytes rather than
        for the fields.  A 293-byte table costs five reads here and seventy-odd
        one field at a time.

        With ``partial``, a read the device refuses with a Modbus exception is
        taken as the end of the mapped range rather than as a failure: the
        request is halved until it fits or runs out, and the prefix that could
        be read is returned.  Some boards map a table shorter than the 293
        bytes the frame allows (about 244 on the unit measured, findings §28),
        and reading past that answers exception 2.  A *timeout* is never
        treated that way -- a device that says nothing at all is a link fault,
        not a short table -- so it is raised whatever ``partial`` says.
        """
        base = self.table_register(table)
        start = payload_off & ~1  # word-align down (even offset)
        lead = payload_off - start  # 0 or 1 byte before the range
        nregs = (lead + nbytes + 1) // 2
        out = bytearray()
        done = 0
        while done < nregs:
            want = min(MAX_READ_CHUNK, nregs - done)
            at = base + start + 2 * done
            try:
                out += self.read_registers(slave, at, want)
            except ModbusError as exc:
                if not partial or exc.exception_code is None:
                    raise
                # The device refused, so the mapped range ends inside this
                # chunk.  Find the largest read that still fits, take it, and
                # stop: nothing past a register the device will not serve is
                # going to be served either.
                out += self._largest_read(slave, at, want)
                break
            done += want
        return bytes(out[lead : lead + nbytes])

    def _largest_read(self, slave: int, reg: int, refused: int) -> bytes:
        """Return the longest read at ``reg`` shorter than ``refused`` that works.

        A binary search, so a table's true end is found in about five queries
        rather than by walking down a register at a time.  ``b""`` when even
        one register is refused, which is what a caller reading past the end
        of a table gets.
        """
        low, high = 0, refused  # low is known to work, high is known not to
        best = b""
        while high - low > 1:
            mid = (low + high) // 2
            try:
                best, low = self.read_registers(slave, reg, mid), mid
            except ModbusError as exc:
                if exc.exception_code is None:
                    raise
                high = mid
        return best

    def read_frame(self, slave: int, table: str, length: int = 293) -> bytes:
        """Read as much of a table as the device exposes, as a synthetic frame.

        The result is a whole 300-byte frame: header, code, counter, payload.

        Diagnostic helper: it reads byte-addressed chunks and *stops* at the
        first chunk the device refuses (exception 2 / timeout marks the end of
        the mapped range) rather than failing outright, so a caller can see how
        far the table actually goes.  The tool's info/cells paths read fields
        individually instead -- see jkbms.Device.
        """
        base = self.table_register(table)
        out = bytearray()
        off = 0  # byte offset into the payload
        while off < length:
            regs = min(MAX_READ_CHUNK, (length - off + 1) // 2)
            try:
                chunk = self.read_registers(slave, base + off, regs)
            except ModbusError:
                break
            out += chunk
            off += len(chunk)  # == 2 * regs
        payload = bytes(out[:length])
        frame = bytearray(300)
        frame[0:4] = b"\x55\xaa\xeb\x90"
        frame[4] = int(table, 16)
        frame[6 : 6 + len(payload)] = payload
        frame[299] = sum(frame[:299]) & 0xFF
        return bytes(frame)
