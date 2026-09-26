"""Reconnaissance of a JK BMS on RS485: what answers, and how far.

By default this is strictly read-only.  It never writes a register and never
sends a native command that changes state -- in particular never 0x18 (a
settings write that persists to EEPROM) nor 0xFF (reboot into the bootloader).

What one run settles:

1. which Modbus slave address the BMS answers on, at which baud rate;
2. the largest FC03 read the device will serve -- the quantity cap that makes
   whole-table reads impossible;
3. how far each table actually extends on this board, by binary search over
   even byte offsets;
4. a full decoded dump of each table, read one field at a time;
5. as a fallback, whether the port instead speaks the native 55AAEB90 framing.

:func:`probe_write_limit` is the exception to read-only and is never run unless
asked for.  Even then it only ever writes a register the value it has just read
from it, so a run that is interrupted halfway leaves the settings as they were.
"""

from __future__ import annotations

import time
from typing import Any

from jkctl import modbus as M, protocol as P

BAUDS = [115200, 9600]
SLAVES = list(range(0, 17))

# How far a table's registers run before the next table's base.
TABLE_WINDOW = 0x200
# The payload a full frame carries, and so the most a table can hold.
PAYLOAD_LEN = 293
# Printable ASCII, for the hex-dump's right-hand column.
PRINTABLE_LO, PRINTABLE_HI = 32, 127
# Enough to hold two whole 300-byte native frames while looking for one.
NATIVE_REPLY_MAX = 600


class Log:
    """A log that both prints and remembers, so a run can be sent to someone."""

    def __init__(self, path: str | None = None):
        """Collect lines, to be written to ``path`` by :meth:`save`."""
        self.lines: list[str] = []
        self.path = path

    def __call__(self, *a) -> None:
        """Print one line and keep it."""
        s = " ".join(str(x) for x in a)
        print(s)
        self.lines.append(s)

    def save(self) -> None:
        """Write everything logged so far to the path given at construction."""
        if not self.path:
            return
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.lines) + "\n")


def hexs(b: bytes) -> str:
    """Render bytes as spaced hex."""
    return b.hex(" ")


def ascii_of(b: bytes) -> str:
    """Render bytes as ASCII, unprintables as dots."""
    return "".join(chr(c) if PRINTABLE_LO <= c < PRINTABLE_HI else "." for c in b)


def passive_listen(port, baud, log, seconds=3.0):
    """Listen without transmitting, to hear whether the bus talks by itself."""
    import serial

    log(
        "\n--- passive listen %d baud, %.0f s (does the bus talk on its own?)"
        % (baud, seconds)
    )
    ser = serial.Serial(port, baud, timeout=0.2)
    end = time.time() + seconds
    buf = bytearray()
    while time.time() < end:
        buf += ser.read(4096)
    ser.close()
    log("   %d bytes" % len(buf))
    if buf:
        log("   " + hexs(bytes(buf[:96])))
        for name, sig in (
            ("55AAEB90", b"\x55\xaa\xeb\x90"),
            ("AA5590EB", b"\xaa\x55\x90\xeb"),
        ):
            if sig in buf:
                log("   contains %s at offset %d" % (name, buf.find(sig)))
    return bytes(buf)


def scan_modbus(port, baud, log, timeout=0.35, retries=1, trace=None):
    """Find a slave that answers FC 0x03 on the device-info table base."""
    found = []
    bus = M.Bus(port, baud, timeout=timeout, retries=retries, log=trace)
    try:
        for base_name, base in (
            ("frame/03", 0x400),
            ("frame/01", 0x000),
            ("raw 0x0000", None),
        ):
            reg = (M.FRAME_ADDR_OFFSET + base) if base is not None else 0
            log("\n--- modbus FC03 scan @ %d baud, register 0x%04X" % (baud, reg))
            for s in SLAVES:
                try:
                    data = bus.read_registers(s, reg, 8)
                except M.ModbusError as exc:
                    if exc.exception_code is not None:
                        log(
                            "   slave %-2d  EXCEPTION: %s  (device present!)" % (s, exc)
                        )
                        found.append(
                            {
                                "slave": s,
                                "baud": baud,
                                "reg": reg,
                                "result": "exception",
                                "detail": str(exc),
                            }
                        )
                    continue
                except Exception:  # noqa: BLE001 - a scan does not stop at an unhappy slave
                    continue
                log("   slave %-2d  OK  %s  |%s|" % (s, hexs(data), ascii_of(data)))
                found.append(
                    {
                        "slave": s,
                        "baud": baud,
                        "reg": reg,
                        "result": "ok",
                        "data": data.hex(),
                    }
                )
            if found:
                break
    finally:
        bus.close()
    return found


def probe_read_limit(bus, slave, log):
    """Measure how many registers the device will return in one FC03 read."""
    log("\n--- max-read probe @ register 0x1400 (read-only)")
    base = M.FRAME_ADDR_OFFSET + 0x400
    out = {}
    biggest_ok = 0
    for cnt in (1, 2, 4, 8, 16, 32, 48, 64, 96, 100, 110, 118, 120, 122, 124, 125):
        try:
            data = bus.read_registers(slave, base, cnt)
            log("   count %3d -> OK (%d bytes)" % (cnt, len(data)))
            out[cnt] = "ok"
            biggest_ok = max(biggest_ok, cnt)
        except Exception as exc:  # noqa: BLE001 - the refusal is the measurement
            log("   count %3d -> %s" % (cnt, exc))
            out[cnt] = str(exc)
    log("   => largest single read that works: %d registers" % biggest_ok)
    return out


def _readable(bus, slave, reg):
    """Whether a single-register read at ``reg`` is answered."""
    try:
        bus.read_registers(slave, reg, 1)
        return True
    except Exception:  # noqa: BLE001 - any refusal means the same: not answered
        return False


def probe_extent(bus, slave, log):
    """Highest readable byte offset in each table = its real mapped length.

    The map is byte-addressed and refuses odd offsets, so this walks even byte
    offsets (single-register reads, read-only) and binary-searches the last one
    that answers.
    """
    log("\n--- table-extent probe (read-only, even-offset single reads)")
    out = {}
    for table in ("01", "02", "03"):
        base = M.FRAME_ADDR_OFFSET + M.TABLE_BASE[table]
        if not _readable(bus, slave, base):
            log("   frame/%s  base register not readable" % table)
            out[table] = None
            continue
        # A table's register window is only 0x200 wide before the next table
        # begins, so cap the search there (reads past it hit the next table).
        limit = TABLE_WINDOW
        lo, hi = 0, 2  # even byte offsets
        while hi < limit and _readable(bus, slave, base + hi):
            lo, hi = hi, hi * 2
        hi = min(hi, limit)
        while lo + 2 < hi:
            mid = ((lo + hi) // 2) & ~1  # keep it even
            if mid == lo:
                break
            if _readable(bus, slave, base + mid):
                lo = mid
            else:
                hi = mid
        log(
            "   frame/%s  highest readable byte offset = %d (reg 0x%04X) => ~%d payload bytes"
            % (table, lo, base + lo, lo + 2)
        )
        out[table] = lo
    return out


def dump_tables(port, baud, slave, log, timeout=0.5, retries=2, trace=None):
    """Characterise the device, then dump and decode every table.

    Measures the read limit and each table's extent first, then reads every
    table the way the tool does -- one field at a time.  Strictly read-only.
    """
    proto = P.Protocol()
    out = {}
    bus = M.Bus(port, baud, timeout=timeout, retries=retries, log=trace)
    try:
        out["read_limit"] = probe_read_limit(bus, slave, log)
        out["extent"] = probe_extent(bus, slave, log)

        for table in ("01", "02", "03"):
            base = bus.table_register(table)
            log(
                "\n--- table frame/%s  base register 0x%04X (byte-addressed)"
                % (table, base)
            )
            payload = bytearray(
                bus.read_frame(slave, table)[P.DATA_OFF : P.DATA_OFF + PAYLOAD_LEN]
            )
            got = len(payload.rstrip(b"\x00"))
            log("   read %d bytes before the device stopped answering" % got)
            out["frame/" + table] = bytes(payload).hex()
            for i in range(0, min(got + 16, 304), 16):
                chunk = bytes(payload[i : i + 16])
                log("   +%03X  %-47s |%s|" % (i, hexs(chunk), ascii_of(chunk)))
            if table == "03":
                frame = bytearray(P.FRAME_LEN)
                frame[P.DATA_OFF : P.DATA_OFF + PAYLOAD_LEN] = payload
                d = proto.decode("03", bytes(frame), P.MODBUS_BYTEORDER)
                log("   decode (payload-relative):")
                for k in (
                    "manuDeviceID",
                    "hardwareVersion",
                    "softwareVersion",
                    "deviceSN",
                    "manufactureDate",
                    "maxCells",
                    "pwrOnTimes",
                    "oddRunTime",
                ):
                    log("      %-16s %r" % (k, d.get(k)))
    finally:
        bus.close()
    return out


def try_native(port, baud, log):
    """Try the 20-byte AA5590EB command framing, with read-only commands only."""
    import serial

    log("\n--- native 55AAEB90 framing probe @ %d baud" % baud)
    res = {}
    ser = serial.Serial(port, baud, timeout=0.5)
    try:
        for cmd, what in (
            (P.CMD_READ_DEVICE_INFO, "device info"),
            (P.CMD_READ_CELL_INFO, "cell info"),
        ):
            frame = P.build_command(cmd)
            ser.reset_input_buffer()
            ser.write(frame)
            ser.flush()
            end = time.time() + 1.5
            buf = bytearray()
            while time.time() < end and len(buf) < NATIVE_REPLY_MAX:
                n = ser.in_waiting
                if n:
                    buf += ser.read(n)
                    end = time.time() + 0.15
                else:
                    time.sleep(0.01)
            log("   cmd 0x%02X (%s) -> %d bytes" % (cmd, what, len(buf)))
            if buf:
                log("   " + hexs(bytes(buf[:64])))
                res["cmd%02X" % cmd] = bytes(buf).hex()
                i = bytes(buf).find(b"\x55\xaa\xeb\x90")
                if i >= 0 and len(buf) >= i + 300:
                    f = bytes(buf[i : i + 300])
                    log(
                        "   frame type %d, checksum %s"
                        % (f[4], "OK" if P.frame_ok(f) else "BAD")
                    )
    finally:
        ser.close()
    return res


def probe_write_limit(bus, slave, log, table="01"):
    """Measure how many registers the device will accept in one FC16 write.

    This is the one part of the probe that writes.  Every write here puts back
    the bytes it has just read, so the settings are unchanged whether the sweep
    finishes or is interrupted -- but it *is* a write, and a device that
    silently mangles a multi-register write would mangle this one too.  It runs
    only when explicitly asked for.
    """
    log("\n--- max-write probe @ frame/%s (writes back what it reads)" % table)
    base = bus.table_register(table)
    out = {}
    biggest_ok = 0
    for cnt in (1, 2, 4, 8, 16, 32, 48, 64, 96, 120):
        try:
            data = bus.read_registers(slave, base, cnt)
        except M.ModbusError as exc:
            log("   count %3d -> cannot read that much back (%s)" % (cnt, exc))
            out[cnt] = "unreadable"
            continue
        try:
            bus.write_registers(slave, base, data)
            log("   count %3d -> OK (%d bytes rewritten unchanged)" % (cnt, len(data)))
            out[cnt] = "ok"
            biggest_ok = max(biggest_ok, cnt)
        except M.ModbusError as exc:
            log("   count %3d -> %s" % (cnt, exc))
            out[cnt] = str(exc)
    log("   => largest single write that works: %d registers" % biggest_ok)
    return out


def probe_undocumented(bus, slave, log):
    """Look for the two frames the vendor's Modbus map does not mention.

    The datasource describes six tables; the four base getters in the vendor's
    application cover four of them and stop.  Whether the Modbus interface
    serves the system log (frame 05) and the fault records (frame 06)
    anywhere is unanswered, and the candidates are the two windows the same
    +0x200-per-frame pattern points at.

    Read-only and cheap: one small read per candidate, at the start of each
    window, and a refusal is the expected answer rather than a fault.  One run
    on a real board settles a question no amount of reading the application
    can.
    """
    from jkctl.history import CANDIDATE_BASES

    found = {}
    for table, offset in sorted(CANDIDATE_BASES.items()):
        register = bus.addr_offset + offset
        try:
            data = bus.read_registers(slave, register, 8)
        except (M.ModbusError, OSError) as exc:
            log("  frame/%s at 0x%04X: %s" % (table, register, exc))
            found[table] = {"register": register, "answered": False, "why": str(exc)}
            continue
        log("  frame/%s at 0x%04X: ANSWERED %s" % (table, register, data.hex(" ")))
        found[table] = {
            "register": register,
            "answered": True,
            "bytes": data.hex(),
        }
    return found


def run(
    port,
    *,
    bauds=None,
    timeout=0.5,
    retries=2,
    trace=None,
    log=None,
    probe_writes=False,
):
    """Characterise a bus and return the report a person would send on.

    Tries each baud rate in turn: listen, scan for a slave, and on the first
    that answers, dump and decode every table.  A bus that answers nothing
    falls through to the native-framing probe, because a unit configured for
    another protocol is not a broken unit.
    """
    log = log or Log()
    report: dict[str, Any] = {
        "port": port,
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    log(
        "jkctl probe -- READ ONLY. port=%s  timeout=%.2fs retries=%d"
        % (port, timeout, retries)
    )

    hit = None
    for baud in bauds or BAUDS:
        report.setdefault("passive", {})[str(baud)] = passive_listen(
            port, baud, log
        ).hex()
        found = scan_modbus(
            port, baud, log, timeout=timeout, retries=retries, trace=trace
        )
        report.setdefault("modbus_scan", []).extend(found)
        ok = [f for f in found if f["result"] == "ok"]
        if ok:
            hit = (baud, ok[0]["slave"])
            break

    if hit is None:
        log("\n=== no Modbus answer; trying the native framing")
        report["transport"] = "unknown"
        for baud in bauds or BAUDS:
            r = try_native(port, baud, log)
            if r:
                report["native"] = {"baud": baud, "responses": r}
                report["transport"] = "native"
                break
        return report

    baud, slave = hit
    log("\n=== BMS answers Modbus at %d baud, slave address %d" % (baud, slave))
    report.update(transport="modbus", baud=baud, slave=slave)
    report["tables"] = dump_tables(
        port, baud, slave, log, timeout=timeout, retries=retries, trace=trace
    )
    log("\n=== the two undocumented frames, if this board maps them")
    bus = M.Bus(port, baud, timeout=timeout, retries=retries, log=trace)
    try:
        report["undocumented"] = probe_undocumented(bus, slave, log)
    finally:
        bus.close()
    if probe_writes:
        bus = M.Bus(port, baud, timeout=timeout, retries=retries, log=trace)
        try:
            report["write_limit"] = probe_write_limit(bus, slave, log)
        finally:
            bus.close()
    return report


__all__ = [
    "BAUDS",
    "SLAVES",
    "Log",
    "dump_tables",
    "passive_listen",
    "probe_extent",
    "probe_read_limit",
    "probe_undocumented",
    "probe_write_limit",
    "run",
    "scan_modbus",
    "try_native",
]
