"""The Modbus RTU framing, the byte-addressed map, and the device's quirks."""

from __future__ import annotations

import time

import pytest
from conftest import MasterEnd, SimEnd

from jkctl import modbus as M
from jkctl.modbus import Bus
from jkctl.simulator import Sim


def test_crc16_matches_the_captured_arming_frame():
    # From a capture of the vendor's own application arming a firmware
    # upgrade on RS485-1: slave 1, FC16, register 0x1626, one register, 0x0000.
    body = bytes.fromhex("01 10 16 26 00 01 02 00 00".replace(" ", ""))
    assert M.crc16(body).to_bytes(2, "little").hex(" ") == "d6 97"


def test_arming_write_produces_the_captured_bytes(pair):
    bus, sim, link = pair
    bus.write_registers(1, bus.action_register(0x26), b"\x00\x00", expect_reply=False)
    assert link.sent[-1].hex(" ") == "01 10 16 26 00 01 02 00 00 d6 97"


def test_table_registers_are_byte_addressed(bus):
    # Proven on the live unit: register == table base + payload byte offset.
    assert bus.table_register("03", 0) == 0x1400
    assert bus.table_register("03", 0x18) == 0x1418
    assert bus.table_register("02", 0x94) == 0x1294


def test_a_read_returns_two_bytes_per_register(bus):
    data = bus.read_registers(1, 0x1400, 8)
    assert len(data) == 16
    assert data.split(b"\x00")[0] == b"JK_PB2A16S20P"


def test_overlapping_reads_confirm_byte_addressing(bus):
    # The test that settled it: R0 = FC03(0x1400, 32), R1 = FC03(0x1420, 32)
    # satisfy R0[32:64] == R1[0:32] only if 0x1420 is byte offset 32.
    r0 = bus.read_registers(1, 0x1400, 32)
    r1 = bus.read_registers(1, 0x1420, 32)
    assert r0[32:64] == r1[0:32]


def test_a_read_above_the_quantity_cap_is_refused(bus):
    with pytest.raises(M.ModbusError) as exc:
        bus.read_registers(1, 0x1400, 125)
    assert exc.value.exception_code == M.EXC_ILLEGAL_ADDRESS


def test_an_odd_offset_is_refused(bus):
    with pytest.raises(M.ModbusError) as exc:
        bus.read_registers(1, 0x1401, 2)
    assert exc.value.exception_code == M.EXC_ILLEGAL_ADDRESS


def test_a_read_past_the_end_of_a_table_is_refused(bus):
    with pytest.raises(M.ModbusError):
        bus.read_registers(1, 0x1400 + 290, 8)


def test_read_payload_word_aligns_an_odd_field(bus):
    # maxCells sits at byte 15 of frame/03 -- an odd offset, which the device
    # will not read.  read_payload must align down and trim.
    assert bus.read_payload(1, "03", 15, 1) == b"\x10"


def test_read_payload_chunks_under_the_cap(bus):
    data = bus.read_payload(1, "02", 0, 64)
    assert len(data) == 64


def test_a_write_must_be_word_aligned(bus):
    with pytest.raises(M.ModbusError, match="word boundary"):
        bus.write_payload(1, "01", 3, b"\x00\x00")
    with pytest.raises(M.ModbusError, match="word boundary"):
        bus.write_payload(1, "01", 4, b"\x00")


def test_a_write_reads_back(bus):
    bus.write_payload(1, "01", 4, b"\x00\x00\x0b\x54")
    assert bus.read_payload(1, "01", 4, 4) == b"\x00\x00\x0b\x54"


def test_a_long_write_is_split_and_still_lands(bus):
    data = bytes(range(2 * M.MAX_WRITE_CHUNK + 8))
    bus.write_payload(1, "01", 136, data)  # cellConWireRes, 128 bytes wide
    assert bus.read_payload(1, "01", 136, len(data)) == data


def test_an_exception_reply_is_not_retried(pair):
    bus, sim, link = pair
    bus.retries = 3
    before = len(link.sent)
    with pytest.raises(M.ModbusError):
        bus.read_registers(1, 0x1400, 125)
    assert len(link.sent) - before == 1


# --- the turnaround delay ------------------------------------------------------------


class Deaf(MasterEnd):
    """A link that drops every request sent too soon after the last answer.

    What the live board does, from the trace of 2026-09-25: a frame put on
    the wire a couple of milliseconds behind the previous reply is not
    refused, it is not heard.
    """

    def __init__(self, needs: float):
        super().__init__()
        self.needs = needs
        self.last = 0.0
        self.heard = 0
        self.ignored = 0

    def write(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        if time.monotonic() - self.last < self.needs:
            self.ignored += 1
            return
        self.heard += 1
        assert self.sim is not None
        self.sim.feed(bytes(data))
        self.last = time.monotonic()


def deaf_pair(needs: float, **kwargs):
    """A bus wired to a board that will not be spoken to too soon.

    Its very first frame is always heard: a board that has been sitting
    there is ready, which is why the fault this models only ever shows up
    from the second request onwards.
    """
    link = Deaf(needs)
    sim = Sim(port=None, slave=1, link=SimEnd(link), log=lambda msg: None)
    link.sim = sim
    return Bus(link=link, timeout=0.05, **kwargs), link


def test_a_scripted_link_is_not_made_to_wait(bus):
    # There is no board and no adapter on the other end of an injected link,
    # so the delay a real one needs is only a slower test suite.
    assert bus.turnaround == 0.0
    assert M.Bus(link=bus.ser, turnaround=0.5).turnaround == 0.5


def test_a_board_that_needs_a_pause_gets_one(bus):
    bus.turnaround = 0.02
    started = time.monotonic()
    bus.read_registers(1, 0x1400, 8)
    bus.read_registers(1, 0x1400, 8)
    assert time.monotonic() - started >= 0.02


def test_a_board_that_goes_quiet_is_given_longer_from_then_on():
    bus, link = deaf_pair(0.05, retries=3, turnaround=0.004)
    said: list[str] = []
    bus.note = said.append
    bus.read_registers(1, 0x1400, 8)  # heard: the link starts out ready
    bus.read_registers(1, 0x1400, 8)  # too soon, so retried and widened
    assert bus.turnaround > 0.004
    assert said and "between frames" in said[0]


def test_an_address_nobody_is_at_does_not_slow_the_whole_bus_down():
    bus, link = deaf_pair(0.0, retries=0, turnaround=0.004)
    with pytest.raises(M.ModbusError):
        bus.read_registers(9, 0x1400, 8)  # no simulator at address 9
    assert bus.turnaround == 0.004


def test_the_delay_stops_growing_at_the_ceiling():
    bus, link = deaf_pair(0.0, retries=0, turnaround=M.TURNAROUND_MAX_S)
    bus.read_registers(1, 0x1400, 8)  # heard: the link starts out ready
    link.needs = 9.0
    with pytest.raises(M.ModbusError):
        bus.read_registers(1, 0x1400, 8)
    assert bus.turnaround == M.TURNAROUND_MAX_S


# --- writing when a frame goes missing ------------------------------------------------


def test_a_write_nobody_heard_is_tried_again(pair):
    bus, sim, link = pair
    bus.retries = 2
    swallow = {"left": 1}
    real = link.write

    def flaky(data: bytes) -> None:
        if swallow["left"]:
            swallow["left"] -= 1
            link.sent.append(bytes(data))
            return
        real(data)

    link.write = flaky  # ty: ignore
    bus.write_payload(1, "01", 4, b"\x00\x00\x0b\x54")
    assert bus.read_payload(1, "01", 4, 4) == b"\x00\x00\x0b\x54"


def test_a_write_the_board_refuses_is_not_tried_again(pair):
    bus, sim, link = pair
    bus.retries = 3
    before = len(link.sent)
    with pytest.raises(M.ModbusError):
        bus.write_registers(1, 0x1401, b"\x00\x00")  # odd offset: refused
    assert len(link.sent) - before == 1


def test_an_action_is_never_tried_again(pair):
    bus, sim, link = pair
    bus.retries = 3
    before = len(link.sent)
    link.write = lambda data: link.sent.append(bytes(data))  # ty: ignore
    with pytest.raises(M.ModbusError):
        bus.write_action(1, 0x0A, 1)
    assert len(link.sent) - before == 1
