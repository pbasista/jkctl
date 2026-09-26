"""JK BMS firmware transfer -- XMODEM, exactly as jk-bms-monitor.exe does it.

Recovered from three functions in jk-bms-monitor.exe 3.11.0:

  FUN_140017260 @ 0x140017260   starts the upgrade: enqueues a Modbus write of
                                one register, value 0x0000, to register
                                frameAddrOffset + 0x600 + 0x26  (0x1626).
                                Its timeout argument is 0xFFFFFFFF -- unlike
                                every other action, this one never times out.
                                It refuses to start if a request for that same
                                register is already pending.

  FUN_1400138e0 @ 0x1400138e0   RX dispatch.  While a request for register
                                (base + 0x600 + 0x26) is outstanding, every
                                received byte goes to FUN_140014610 instead of
                                the normal Modbus response parser.

  FUN_140014610 @ 0x140014610   scans the received bytes *backwards* for the
                                first byte in {0x06 ACK, 0x15 NAK, 0x18 CAN}:
                                  ACK -> if blocks_sent == total: success
                                         else send the next block
                                  NAK -> step the counter back one and resend
                                  CAN -> abort
  FUN_140015bf0 @ 0x140015bf0   builds and writes one block:

        SOH(0x01) | blockNo | ~blockNo | data[128] | checksum
        blockNo   = (counter + 1) & 0xFF          (first block is 1)
        data      = image[counter*128 : +128], last block padded with 0xFF
        checksum  = sum(block[3:]) & 0xFF         (classic XMODEM, not CRC)
        counter  += 1
        if counter == total:  append EOT EOT EOT  (0x04 x3) to the same write
        QThread::msleep(1) before writing

Total block count is ceil(len(image)/128) -- FUN_1400181b0 @ 0x1400181b0, the
progress bar's maximum.

The receiving side is the BMS bootloader at 0x08000000-0x08003FFF, which is not
contained in any .jkbms file (those hold only the application at 0x08004000),
so the device side could not be cross-checked against firmware.  Everything
above comes from the sender.
"""

from __future__ import annotations

import time

from devicectl.report import SILENT, Reporter

from jkctl.errors import JkError
from jkctl.registers import UPGRADE_SLOT

SOH = 0x01
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18

BLOCK = 128
PAD = 0xFF


class UpgradeError(JkError):
    """The bootloader refused the transfer, or never spoke at all."""


def block_count(image: bytes) -> int:
    """How many 128-byte XMODEM blocks an image takes."""
    return (len(image) + BLOCK - 1) // BLOCK


def build_block(image: bytes, index: int) -> bytes:
    """Index is 0-based; the on-wire block number is index+1."""
    chunk = image[index * BLOCK : (index + 1) * BLOCK]
    if len(chunk) < BLOCK:
        chunk = chunk + bytes([PAD]) * (BLOCK - len(chunk))
    n = (index + 1) & 0xFF
    body = bytes([SOH, n, (~n) & 0xFF]) + chunk
    return body + bytes([sum(body[3:]) & 0xFF])


def _last_control(buf: bytes) -> int | None:
    """Return the last ACK/NAK/CAN in a buffer, scanning backwards as the app does."""
    for b in reversed(buf):
        if b in (ACK, NAK, CAN):
            return b
    return None


def upgrade(
    bus,
    slave: int,
    image: bytes,
    *,
    report: Reporter = SILENT,
    start_timeout: float = 60.0,
    block_timeout: float = 10.0,
    max_retries: int = 10,
) -> None:
    """Run the whole transfer.  Raises UpgradeError on failure."""
    total = block_count(image)
    if total == 0:
        raise UpgradeError("empty firmware image")

    reg = bus.action_register(UPGRADE_SLOT)
    # The app sends this and does not wait for a Modbus reply: the BMS reboots
    # into its bootloader instead of answering.
    bus.write_registers(slave, reg, b"\x00\x00", expect_reply=False)

    sent = 0  # == the app's watcher field +0xb8
    retries = 0
    started = time.time()
    report.step("Waiting for the bootloader")
    deadline = time.time() + start_timeout
    while True:
        buf = bus.read_raw(1.0)
        ctl = _last_control(buf)
        if ctl is None:
            if time.time() > deadline:
                raise UpgradeError(
                    "no ACK/NAK from the bootloader after %.0f s "
                    "(sent %d/%d blocks)" % (start_timeout, sent, total)
                )
            continue

        if ctl == CAN:
            raise UpgradeError(
                "bootloader cancelled the transfer (CAN) "
                "after %d/%d blocks" % (sent, total)
            )

        if ctl == ACK:
            if sent == total:
                report.sending(sent, total, time.time() - started)
                return
            retries = 0
        else:  # NAK -- step back one block and resend
            retries += 1
            if retries > max_retries:
                raise UpgradeError("too many NAKs at block %d" % (sent + 1))
            sent = max(0, sent - 1) if sent else 0

        frame = build_block(image, sent)
        sent += 1
        if sent == total:
            frame += bytes([EOT, EOT, EOT])
        time.sleep(0.001)  # QThread::msleep(1)
        bus.write_raw(frame)
        report.sending(sent, total, time.time() - started)
        deadline = time.time() + block_timeout
