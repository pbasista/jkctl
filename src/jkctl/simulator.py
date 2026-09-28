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
from dataclasses import dataclass
from datetime import datetime, timezone

from jkctl import (
    flashdump as D,
    identity as I,
    modbus as M,
    protocol as P,
    upgrade as U,
)
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


@dataclass(frozen=True)
class Pack:
    """One simulated pack's live state -- enough to draw a distinct tile and dashboard.

    A bank of these is what makes ``--simulate`` worth showing: the boards
    differ the way real ones on a bus do -- one resting, one charging and
    balancing near the top, one working hard and warm -- rather than three
    copies of one reading with only the serial number changed.
    """

    name: str
    cells: tuple[float, ...]  # per-cell volts, in cell order
    current: float  # pack amps: positive charging, negative discharging
    soc: int  # relative state of charge, %
    remain_ah: float  # capacity remaining
    mos_temp: float
    bat_temps: tuple[float, float]
    # The nameplate the board reports.  A real bank is rarely one part number
    # bought at one time: the packs differ by model, hardware revision and
    # firmware even when they are all 16S and safe to parallel, and jkctl
    # names each board by what it says here.  Every model below is a 16S part,
    # so the bank the simulator draws is one that would actually be wired in
    # parallel rather than a mix of cell counts nobody would connect.
    model: str = "JK_PB2A16S20P"
    hw: str = "15A"
    sw: str = "15.41"
    cycles: int = 12
    full_ah: float = 280.0
    equ_current: float = 0.0  # balance current, 0 unless balancing
    balancing: bool = False
    alarm: int = 0  # sysAlarm bitmap; 0 is all clear
    # Which probes this pack has: the MOS one and two of the five battery ones,
    # which is how most packs are wired.  Left unset the word reads zero, and
    # zero is a unit saying it has no probes at all -- three temperatures on a
    # card whose own flag word said there was nothing to measure them with.
    sensors_absent: int = 0b000111


# Board 1, and the pack the dashboard screenshot is taken of: a healthy 16S
# LiFePO4 pack a little below full, discharging into a light load, its cells
# within a natural dozen millivolts of one another rather than the clean ramp
# a fake pack betrays itself with.
RESTING = Pack(
    name="resting",
    cells=(
        3.301,
        3.298,
        3.303,
        3.300,
        3.299,
        3.304,
        3.297,
        3.302,
        3.300,
        3.301,
        3.299,
        3.303,
        3.298,
        3.302,
        3.300,
        3.293,
    ),  # fmt: skip
    current=-12.345,
    soc=87,
    remain_ah=245.0,
    mos_temp=28.5,
    bat_temps=(24.0, 24.5),
)

# Board 2: the same chemistry near the top of a charge, which is where a
# balancer works -- cell 5 has run ahead and cell 12 is behind, and the board
# is moving charge from the one to the other.  Beside the resting pack it gives
# the page a balancer to draw.
BALANCING = Pack(
    name="balancing",
    cells=(
        3.452,
        3.455,
        3.450,
        3.457,
        3.471,
        3.453,
        3.449,
        3.454,
        3.451,
        3.456,
        3.452,
        3.438,
        3.450,
        3.453,
        3.455,
        3.451,
    ),  # fmt: skip
    current=8.2,
    soc=91,
    remain_ah=270.0,
    mos_temp=29.0,
    bat_temps=(25.5, 26.0),
    model="JK_PB1A16S15P",
    hw="14B",
    sw="14.28",
    cycles=13,
    equ_current=0.42,
    balancing=True,
)

# Board 3: a pack working hard in a warm enclosure -- a heavier discharge, a
# lower charge, cells sagging under the load with one weaker than the rest, and
# a battery-over-temperature *alarm* raised (sysAlarm bit 21).  That is the
# warning that colours a tile and shows the alarm badge without cutting the
# pack off the way a protection would, so the bank has a board asking to be
# looked at beside two that are fine.
WORKING = Pack(
    name="working",
    cells=(
        3.281,
        3.278,
        3.284,
        3.276,
        3.271,
        3.283,
        3.279,
        3.274,
        3.280,
        3.262,
        3.277,
        3.282,
        3.275,
        3.279,
        3.284,
        3.272,
    ),  # fmt: skip
    current=-41.6,
    soc=58,
    remain_ah=162.0,
    mos_temp=41.0,
    bat_temps=(46.5, 45.0),
    model="JK_B2A16S20P",
    hw="11.XW",
    sw="11.34",
    cycles=47,
    alarm=1 << 21,  # "Battery over temp. alarm"
)

# The boards `--simulate` puts on the bus, in address order.  It cycles once
# past the third, so a larger bank keeps the same spread of states rather than
# filling out with clones -- while address 1 stays the resting board the
# screenshots are taken of and address 2 stays the balancing one.
PACKS = (RESTING, BALANCING, WORKING)


def pack_for(slave: int) -> Pack:
    """Return the profile the simulated board at this address runs."""
    return PACKS[(slave - 1) % len(PACKS)]


def build_cell_info(
    proto: P.Protocol, cells: int = 16, pack: "Pack | None" = None
) -> bytearray:
    """Build a 293-byte frame/02 payload for one pack's live reading.

    ``pack`` selects which profile to serve (see :data:`PACKS`); the default is
    the resting board the dashboard screenshot is taken of.
    """
    pack = pack or RESTING
    frame = bytearray(P.FRAME_LEN)
    live = list(pack.cells[:cells])
    volts = live + [0.0] * (32 - cells)
    _pack(frame, proto.field("02", "cellVol"), volts)
    _pack(frame, proto.field("02", "cellWireRes"), [0.150] * 32)
    high, low = live.index(max(live)), live.index(min(live))
    total = sum(live)
    for key, value in (
        ("cellStatus", (1 << cells) - 1),
        ("cellVolAve", round(total / cells, 3)),
        ("maxVoltDelta", round(max(live) - min(live), 3)),
        ("celMaxVol", high),
        ("celMinVol", low),
        ("batVol", round(total, 1)),
        ("batCurrent", pack.current),
        ("batWatt", round(total * abs(pack.current), 0)),
        ("socRelativeStateOfCharge", pack.soc),
        ("sOCSOH", 100),
        ("socCapabilityRemain", pack.remain_ah),
        ("socFullChargeCapacity", pack.full_ah),
        ("socCycleCount", pack.cycles),
        ("socCycleCapacity", round(pack.full_ah * pack.cycles, 0)),
        ("tempMos", pack.mos_temp),
        ("batTemp1", pack.bat_temps[0]),
        ("batTemp2", pack.bat_temps[1]),
        ("tempSensorAbsent", pack.sensors_absent),
        ("equCurrent", pack.equ_current),
        ("equStatus", 1 if pack.balancing else 0),
        ("chargeStatus", 1),
        ("dischargeStatus", 1),
        ("heatingStatus", 0),
        ("chargePlugged", 0),
        ("runtime", 123456),
        ("sysAlarm", pack.alarm),
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
        flash_image: bytes | None = None,
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
        pack = pack_for(slave)
        self.tables = {
            TABLE_OFFSETS["01"]: build_settings(proto),
            # Each address runs its own profile, so a demonstration bank shows
            # a resting pack, a balancing one and a warm, hard-working one side
            # by side rather than three copies of one reading.
            TABLE_OFFSETS["02"]: build_cell_info(proto, pack=pack),
            # A serial per address, and the model and versions the profile
            # carries.  Every simulated board used to report one JK-SIM-0001 on
            # one JK_PB2A16S20P at 15.41 -- invisible until the page began
            # naming a board by the strings no two of them share, and then a
            # demonstration bank was three boards with one name and one
            # nameplate.  They are three 16S parts on three firmwares now.
            TABLE_OFFSETS["03"]: build_device_info(
                proto,
                model=pack.model,
                sw=pack.sw,
                hw=pack.hw,
                sn=f"JK-SIM-{slave:04d}",
            ),
        }
        self._rtc = proto.field("02", "rtcCounter")
        self.unwritable = UNWRITABLE
        self.actions: list[tuple[int, int]] = []  # (slot, value), for tests
        self.upgrading = False
        self.expect_block = 1
        self.received = bytearray()
        self.out_image = out_image
        self.flash_image = flash_image
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
        if reg == action_lo + D.DUMP_SLOT and self.flash_image is not None:
            block = int.from_bytes(data, "little")
            self.actions.append((D.DUMP_SLOT, block))
            self.log(f"flash dump block {block}")
            self.ser.write(D.encode_frame(self.flash_image, block))
            self.ser.flush()
            return self._reply(bytes(req_echo(slave, fc, reg, cnt)))
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
    "PACKS",
    "Loopback",
    "Pack",
    "Sim",
    "SimLink",
    "bank",
    "build_cell_info",
    "build_device_info",
    "build_settings",
    "pack_for",
]
