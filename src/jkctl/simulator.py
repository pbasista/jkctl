"""A fake JK BMS on a serial port, so the tool can be exercised without hardware.

The Modbus side mirrors FUN_140019c80 @ 0x140019c80, which is jk-bms-monitor's
own bus simulator: a register selects a datasource table (frameAddrOffset +
0x000/0x200/0x400 for frame/01, /02, /03) and a byte offset inside it, and the
reply carries two bytes per register straight out of that table's buffer.

It reproduces the four quirks measured on the real unit (findings §27-§29), so
that code which works against this simulator works against the device:

* the map is byte-addressed and numbers are served big-endian;
* an FC03 read of more than :data:`MAX_READ_QTY` registers is refused with
  exception 2, as is one starting at an odd offset, as is one running past the
  end of the table;
* FC16 writes land in the settings table and read back -- except at the
  settings bytes in :data:`UNWRITABLE`, which the real unit refuses with
  exception 2 whatever it is sent.

The upgrade side implements the receiver the app's sender expects: after a
write to frameAddrOffset+0x626 it sends NAK, then ACKs each well-formed XMODEM
block and finally the EOT.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from jkctl import identity as I, modbus as M, protocol as P, upgrade as U
from jkctl.registers import UPGRADE_SLOT

# The device rejects an FC03 read larger than this with exception 2.  Measured
# at 122 on the live unit; 120 is the largest round number below it.
MAX_READ_QTY = 120

# Length of the payload each table serves.  The real unit answers about 244
# bytes of frame/03 and refuses beyond that; the simulator serves the whole
# 293-byte frame so a caller cannot come to depend on one board's short table.
PAYLOAD_LEN = 293

# Where each table lives, as an offset from frameAddrOffset.
TABLE_OFFSETS = {"01": 0x000, "02": 0x200, "03": 0x400}
TABLE_WINDOW = 0x200

MODBUS_EXCEPTION = 0x80
XMODEM_FRAME = 132
# The shortest RTU request: address, function, register, count and CRC.
RTU_MIN_FRAME = 8
BYTE_MASK = 0xFF


def _pack(frame: bytearray, f: P.Field, value) -> None:
    """Write one field into a frame the way the device would serve it."""
    frame[f.off : f.off + f.size] = P.encode_field(f, value, P.MODBUS_BYTEORDER)


def build_device_info(
    proto: P.Protocol, model="JK_PB2A16S20P", sw="15.41", hw="15A", sn="JK-SIM-0001"
) -> bytearray:
    """Build a 293-byte frame/03 payload."""
    frame = bytearray(P.FRAME_LEN)
    for key, value in (
        ("manuDeviceID", model),
        ("softwareVersion", sw),
        ("hardwareVersion", hw),
        ("deviceSN", sn),
        ("manufactureDate", "241103"),
        ("maxCells", 16),
        ("pwrOnTimes", 42),
        ("oddRunTime", 123456),
        ("protocolVer", 1),
        ("uart1ProtoNo", 1),
        ("uart2ProtoNo", 0),
        ("canProtoNo", 6),
        ("rcvTime", 5),
        ("rfvTime", 20),
        ("dataStoredPeriod", 36000),
    ):
        _pack(frame, proto.field("03", key), value)
    return bytearray(frame[P.DATA_OFF : P.DATA_OFF + PAYLOAD_LEN])


# A pack near the top of a charge, which is where a balancer works: cell 5
# has run ahead and cell 12 is behind.  Set on its own board of a simulated
# bank (see Sim) so the page has a balancing pack to draw beside a resting
# one.
_TOP_OF_CHARGE = [
    3.452, 3.455, 3.450, 3.457, 3.471, 3.453, 3.449, 3.454,
    3.451, 3.456, 3.452, 3.438, 3.450, 3.453, 3.455, 3.451,
]  # fmt: skip


# Which board of a simulated bank is the one mid-balance.
BALANCING_SLAVE = 2


def build_cell_info(
    proto: P.Protocol, cells: int = 16, balancing: bool = False
) -> bytearray:
    """Build a 293-byte frame/02 payload for a healthy 16S LiFePO4 pack.

    ``balancing`` is the same pack near the top of a charge, with its
    balancer moving charge from its highest cell to its lowest.
    """
    frame = bytearray(P.FRAME_LEN)
    ramp = [3.300 + 0.001 * i for i in range(cells)]
    live = (
        _TOP_OF_CHARGE[:cells] if balancing and cells <= len(_TOP_OF_CHARGE) else ramp
    )
    volts = live + [0.0] * (32 - cells)
    _pack(frame, proto.field("02", "cellVol"), volts)
    _pack(frame, proto.field("02", "cellWireRes"), [0.150] * 32)
    high, low = live.index(max(live)), live.index(min(live))
    for key, value in (
        ("cellStatus", (1 << cells) - 1),
        ("cellVolAve", round(sum(live) / cells, 3) if balancing else 3.307),
        ("maxVoltDelta", round(max(live) - min(live), 3) if balancing else 0.015),
        ("celMaxVol", high),
        ("celMinVol", low),
        ("batVol", round(sum(live), 1) if balancing else 52.8),
        ("batCurrent", 8.2 if balancing else -12.345),
        ("batWatt", round(sum(live) * 8.2, 0) if balancing else 651.0),
        ("socRelativeStateOfCharge", 87),
        ("sOCSOH", 100),
        ("socCapabilityRemain", 245.0),
        ("socFullChargeCapacity", 280.0),
        ("socCycleCount", 12),
        ("socCycleCapacity", 3360.0),
        ("tempMos", 28.5),
        ("batTemp1", 24.0),
        ("batTemp2", 24.5),
        # Which probes this pack has: the MOS one and two of the five battery
        # ones, which is how most packs are wired.  Left unset the word read
        # zero, and zero is a unit saying it has no probes at all -- three
        # temperatures on a card whose own flag word said there was nothing
        # to measure them with.
        ("tempSensorAbsent", 0b000111),
        ("equCurrent", 0.42 if balancing else 0.0),
        ("equStatus", 1 if balancing else 0),
        ("chargeStatus", 1),
        ("dischargeStatus", 1),
        ("heatingStatus", 0),
        ("chargePlugged", 0),
        ("runtime", 123456),
        ("sysAlarm", 0),
        ("userAlarm2", 0),
        # Set from this host, so the simulated unit has a clock that is right
        # rather than one five years out.  A fake BMS whose every reading is
        # plausible except the date is a fake BMS that makes `jkctl doctor`
        # report a fault nobody can fix.
        ("rtcCounter", I.datetime_to_rtc(datetime.now(timezone.utc))),
    ):
        _pack(frame, proto.field("02", key), value)
    return bytearray(frame[P.DATA_OFF : P.DATA_OFF + PAYLOAD_LEN])


def build_settings(proto: P.Protocol) -> bytearray:
    """Build a 293-byte frame/01 payload from the datasource's own defaults.

    Every settable field carries a factory default (``dv``); using them means
    the simulator serves a configuration that passes the same range checks the
    tool applies to input, rather than a table of zeroes that no real unit
    would ever report.
    """
    frame = bytearray(P.FRAME_LEN)
    for f in proto.tables["01"]:
        if not f.key or f.default is None:
            continue
        _pack(frame, f, f.default)
    # The datasource's `dv` for most setpoints is simply its minimum, which
    # would leave the simulator serving a configuration no real pack could
    # hold: a 1.2 V over-voltage protection, or -- the temperatures, which
    # were left at theirs until the dashboard started drawing them -- a
    # charge over-temperature protection of 30 C with its release also at
    # 30, which is a protection with no hysteresis at all and a picture with
    # two setpoints drawn on top of each other.  These are the settings a
    # 16S LiFePO4 bank actually runs, matching the runtime table above.
    for key, value in (
        ("volSmartSleep", 3.200),
        ("volCellUV", 2.800),
        ("volCellUVPR", 3.000),
        ("volCellOV", 3.650),
        ("volCellOVPR", 3.550),
        ("volBalanTrig", 0.005),
        ("volSOCP100", 3.550),
        ("volSOCP0", 2.900),
        ("volCellRCV", 3.450),
        ("volCellRFV", 3.375),
        ("volSysPwrOff", 2.600),
        ("timBatCOC", 100.0),
        ("timBatDcOC", 150.0),
        ("curBalanMax", 1.0),
        ("cellCount", 16),
        ("batChargeEn", 1),
        ("batDischargeEn", 1),
        ("balanEn", 1),
        ("capBatCell", 280.0),
        ("volStartBalan", 3.400),
        # Every protection with its release a few degrees inside it, which
        # is what keeps a unit from chattering on and off at the limit.
        ("tmpBatCOT", 55.0),
        ("tmpBatCOTPR", 50.0),
        ("tmpBatDcOT", 60.0),
        ("tmpBatDcOTPR", 55.0),
        ("tmpBatCUT", 0.0),
        ("tmpBatCUTPR", 3.0),
        ("tmpMosOT", 90.0),
        ("tmpMosOTPR", 80.0),
        ("tmpStartHeating", 0.0),
        ("tmpStopHeating", 10.0),
        # Below zero, as a real pack's are: these bytes are signed (see
        # protocol._ntype), and a simulator that held zero here would never
        # show the page a negative setting to draw.
        ("tmpBatDCHUT", -20.0),
        ("tmpBatDCHUTPR", -15.0),
        ("devAddr", 1),
        ("switchStatus", 0x0010),
    ):
        _pack(frame, proto.field("01", key), value)
    return bytearray(frame[P.DATA_OFF : P.DATA_OFF + PAYLOAD_LEN])


# Settings payload byte ranges the measured unit (JK_PB2A16S20P, firmware
# 15.41) refuses to be written, with exception 2, even with the value they
# already hold: the discharge under-temperature pair, which JK's Modbus
# document for the model does not list.  Reading it answers zeros.  Seen in a
# serial trace on 2026-09-26; see Device._refused.
UNWRITABLE = ((0x122, 0x124),)


class Sim:
    """A fake BMS answering Modbus on an open serial port."""

    def __init__(
        self,
        port,
        baud=M.DEFAULT_BAUD,
        slave=1,
        addr_offset=M.FRAME_ADDR_OFFSET,
        out_image=None,
        link: M.Wire | None = None,
        log=None,
    ):
        """Open ``port`` (or adopt ``link``) and build the three table buffers."""
        self.ser: M.Wire
        if link is not None:
            self.ser = link
        else:
            import serial

            self.ser = serial.Serial(port, baud, timeout=0.05)
        self.slave = slave
        self.off = addr_offset
        proto = P.Protocol()
        self.tables = {
            TABLE_OFFSETS["01"]: build_settings(proto),
            # The second board of a bank is the one mid-balance, so a
            # demonstration shows the balancer at work beside a pack at rest.
            TABLE_OFFSETS["02"]: build_cell_info(
                proto, balancing=slave == BALANCING_SLAVE
            ),
            # A serial per address.  Every simulated board used to report
            # JK-SIM-0001, which was invisible until the page began naming a
            # board by the one string no two of them share -- and then a
            # demonstration bank was three boards with one name.
            TABLE_OFFSETS["03"]: build_device_info(proto, sn=f"JK-SIM-{slave:04d}"),
        }
        self._rtc = proto.field("02", "rtcCounter")
        self.unwritable = UNWRITABLE
        self.actions: list[tuple[int, int]] = []  # (slot, value), for tests
        self.upgrading = False
        self.expect_block = 1
        self.received = bytearray()
        self.out_image = out_image
        self.buf = bytearray()
        self.log = log or (lambda msg: print(f"[sim] {msg}", file=sys.stderr))

    # -- modbus ------------------------------------------------------------

    def _reply(self, body: bytes) -> None:
        """Send one RTU reply, CRC appended."""
        self.ser.write(body + M.crc16(body).to_bytes(2, "little"))
        self.ser.flush()

    def _refuse(self, slave: int, fc: int, code: int = M.EXC_ILLEGAL_ADDRESS) -> None:
        """Send a Modbus exception reply."""
        self._reply(bytes([slave, fc | MODBUS_EXCEPTION, code]))

    def _locate(self, reg: int) -> tuple[int, bytearray] | None:
        """Return the table buffer a register falls in, and the offset into it."""
        for base, buf in self.tables.items():
            lo = self.off + base
            if lo <= reg < lo + TABLE_WINDOW:
                return reg - lo, buf
        return None

    def handle_modbus(self, req: bytes) -> None:
        """Answer one well-formed request."""
        slave, fc = req[0], req[1]
        reg = int.from_bytes(req[2:4], "big")
        cnt = int.from_bytes(req[4:6], "big")
        if fc == M.FC_READ:
            self._handle_read(slave, fc, reg, cnt)
        elif fc == M.FC_WRITE:
            self._handle_write(slave, fc, reg, cnt, req[7 : 7 + req[6]])
        else:
            self._refuse(slave, fc, M.EXC_ILLEGAL_FUNCTION)

    def _handle_read(self, slave: int, fc: int, reg: int, cnt: int) -> None:
        """Serve FC03, refusing exactly what the real device refuses."""
        if cnt > MAX_READ_QTY:
            return self._refuse(slave, fc)
        found = self._locate(reg)
        if found is None:
            return self._refuse(slave, fc)
        off, buf = found
        if off % 2 or off >= len(buf) or off + 2 * cnt > len(buf):
            return self._refuse(slave, fc)
        self._tick()
        data = bytes(buf[off : off + 2 * cnt])
        self._reply(bytes([slave, fc, len(data)]) + data)

    def _tick(self) -> None:
        """Move the simulated clock on to now.

        It was set once, when the board was built, and then stood still --
        so a page left open on it watched the board fall a second behind for
        every second it was open, and said so.
        """
        now = I.datetime_to_rtc(datetime.now(timezone.utc))
        at = self._rtc.off - P.DATA_OFF
        self.tables[TABLE_OFFSETS["02"]][at : at + self._rtc.size] = P.encode_field(
            self._rtc, now, P.MODBUS_BYTEORDER
        )

    def _handle_write(
        self, slave: int, fc: int, reg: int, cnt: int, data: bytes
    ) -> None:
        """Serve FC16: the action space, or a write into the settings table."""
        action_lo = self.off + M.ACTION_BASE
        if reg == action_lo + UPGRADE_SLOT:
            self.log("upgrade armed -> rebooting into bootloader")
            self.upgrading = True
            self.expect_block = 1
            self.received = bytearray()
            time.sleep(0.05)
            self.ser.write(bytes([U.NAK]))  # bootloader asks for block 1
            self.ser.flush()
            return
        if action_lo <= reg < action_lo + TABLE_WINDOW:
            self.actions.append((reg - action_lo, int.from_bytes(data, "big")))
            self.log(
                f"action slot 0x{reg - action_lo:02X} = {int.from_bytes(data, 'big')}"
            )
            return self._reply(bytes(req_echo(slave, fc, reg, cnt)))
        found = self._locate(reg)
        if found is None:
            return self._refuse(slave, fc)
        off, buf = found
        if (
            buf is not self.tables[TABLE_OFFSETS["01"]]
            and buf is not self.tables[TABLE_OFFSETS["03"]]
        ):
            return self._refuse(slave, fc, M.EXC_ILLEGAL_ADDRESS)
        if off % 2 or off + len(data) > len(buf):
            return self._refuse(slave, fc)
        if buf is self.tables[TABLE_OFFSETS["01"]] and any(
            off < hi and lo < off + len(data) for lo, hi in self.unwritable
        ):
            return self._refuse(slave, fc, M.EXC_ILLEGAL_ADDRESS)
        buf[off : off + len(data)] = data
        self._reply(bytes(req_echo(slave, fc, reg, cnt)))

    # -- xmodem receiver ---------------------------------------------------

    def handle_xmodem(self) -> None:
        """Consume whatever whole XMODEM blocks are buffered, ACKing each."""
        while True:
            if self.buf[:1] == bytes([U.EOT]):
                self.buf = self.buf[1:]
                self.ser.write(bytes([U.ACK]))
                self.ser.flush()
                if self.out_image:
                    with open(self.out_image, "wb") as fh:
                        fh.write(bytes(self.received))
                    self.log(f"wrote {len(self.received)} bytes to {self.out_image}")
                    self.out_image = None
                # A real unit restarts into the freshly written application and
                # answers Modbus again.  The sender sends EOT three times, so
                # the two that follow are dropped along with anything else left
                # in the buffer rather than being framed as a request.
                self.upgrading = False
                self.buf = bytearray()
                self.log("transfer complete -> restarting into the application")
                return
            if len(self.buf) < XMODEM_FRAME or self.buf[0] != U.SOH:
                return
            blk = bytes(self.buf[:XMODEM_FRAME])
            self.buf = self.buf[XMODEM_FRAME:]
            n, inv, csum = blk[1], blk[2], blk[XMODEM_FRAME - 1]
            ok = (n ^ inv) == BYTE_MASK and csum == (
                sum(blk[3 : XMODEM_FRAME - 1]) & BYTE_MASK
            )
            if ok and n == (self.expect_block & 0xFF):
                self.received += blk[3 : XMODEM_FRAME - 1]
                self.expect_block += 1
                self.ser.write(bytes([U.ACK]))
            else:
                self.log(f"bad block {n} (ok={ok})")
                self.ser.write(bytes([U.NAK]))
            self.ser.flush()

    # -- loop --------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """Push received bytes through the framer; the read loop's inner half."""
        self.buf += data
        if self.upgrading:
            self.handle_xmodem()
            return
        while len(self.buf) >= RTU_MIN_FRAME:
            # Minimal RTU framing: address plus function decide the length.
            if self.buf[0] != self.slave or self.buf[1] not in (M.FC_READ, M.FC_WRITE):
                self.buf = self.buf[1:]
                continue
            need = 8 if self.buf[1] == M.FC_READ else 9 + self.buf[6]
            if len(self.buf) < need:
                break
            req = bytes(self.buf[:need])
            self.buf = self.buf[need:]
            if int.from_bytes(req[-2:], "little") == M.crc16(req[:-2]):
                self.handle_modbus(req)
            if self.upgrading:
                break

    def run(self) -> None:
        """Serve until interrupted."""
        self.log(f"listening, slave {self.slave}, frameAddrOffset 0x{self.off:04X}")
        while True:
            data = self.ser.read(4096)
            if data:
                self.feed(data)


def req_echo(slave: int, fc: int, reg: int, count: int) -> bytes:
    """Build the FC16 acknowledgement: the request's first six bytes."""
    return bytes([slave, fc]) + reg.to_bytes(2, "big") + count.to_bytes(2, "big")


# --- a bank with no serial port ------------------------------------------
#
# ``jkctl simulate`` puts one fake board on a real port so a second process
# can talk to it.  The other thing worth simulating is the whole bus inside
# one process, which is what makes the UI demonstrable on a laptop with no
# hardware and no pty: a :class:`Loopback` is pyserial's shape, so
# ``Bus(link=...)`` cannot tell it from a port, and every simulator on it sees
# every frame while only the addressed one answers -- which is what an RS485
# bus does, and what makes a scan of sixteen addresses find exactly the boards
# that are there.


class SimLink:
    """A simulator's end of an in-memory pair: what it writes reaches the master."""

    def __init__(self, master: "Loopback"):
        """Answer into ``master``'s receive buffer."""
        self.master = master

    def write(self, data: bytes) -> None:
        """Hand an answer to the master."""
        self.master.rx += bytes(data)

    def flush(self) -> None:
        """Nothing is buffered in memory."""

    def read(self, n: int = 1) -> bytes:
        """Never used: a simulator on a loopback is driven by :meth:`Sim.feed`."""
        return b""


class Loopback:
    """A master's end backed by simulated boards rather than by a serial port.

    Synchronous throughout: a write is delivered to every board and the
    addressed one answers before ``write`` returns, so a read that follows
    always finds its reply waiting.  One worker thread owns this, as it owns
    a real port.
    """

    def __init__(self, sims: "list[Sim] | None" = None):
        """Start with these boards on the bus."""
        self.rx = bytearray()
        self.sims: list[Sim] = list(sims or ())

    def write(self, data: bytes) -> None:
        """Put a frame on the bus; the board it addresses answers."""
        for sim in self.sims:
            sim.feed(bytes(data))

    def read(self, n: int = 1) -> bytes:
        """Take up to ``n`` answer bytes."""
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    @property
    def in_waiting(self) -> int:
        """How many answer bytes are waiting."""
        return len(self.rx)

    def flush(self) -> None:
        """Nothing is buffered in memory."""

    def reset_input_buffer(self) -> None:
        """Drop anything unread."""
        self.rx.clear()

    def reset_output_buffer(self) -> None:
        """Nothing is buffered in memory."""

    def close(self) -> None:
        """Nothing to close."""


def bank(
    slaves: "tuple[int, ...] | list[int]" = (1,),
    addr_offset: int = M.FRAME_ADDR_OFFSET,
    **sim_kwargs,
) -> Loopback:
    """Put a simulated board at each of these addresses on one in-memory bus."""
    link = Loopback()
    link.sims = [
        Sim(
            port=None,
            slave=slave,
            addr_offset=addr_offset,
            link=SimLink(link),
            log=lambda msg: None,
            **sim_kwargs,
        )
        for slave in slaves
    ]
    return link


__all__ = [
    "MAX_READ_QTY",
    "PAYLOAD_LEN",
    "Loopback",
    "Sim",
    "SimLink",
    "bank",
    "build_cell_info",
    "build_device_info",
    "build_settings",
]
