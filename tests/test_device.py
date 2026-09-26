"""Reading and writing a unit by field name, and what it refuses."""

from __future__ import annotations

import pytest

from jkctl import identity as I
from jkctl.device import Device, DeviceError, WriteRefused, scan
from jkctl.modbus import Bus, ModbusError
from jkctl.registers import INFO, RUNTIME, SETTINGS


def test_reading_by_name(device):
    assert device.read("manuDeviceID") == "JK_PB2A16S20P"
    assert device.read("softwareVersion") == "15.41"
    assert device.read("batVol", RUNTIME) == 52.8


def test_identity_is_read_once(device, pair):
    link = pair[2]
    device.identity
    after_first = len(link.sent)
    device.identity
    assert len(link.sent) == after_first


def test_a_field_the_board_does_not_map_is_absent_not_fatal(device):
    # protocolVer sits at payload byte 292 of a 293-byte table, so a
    # word-aligned read of it runs one byte past the end and is refused.
    values = device.read_many(["manuDeviceID", "protocolVer"], INFO)
    assert values["manuDeviceID"] == "JK_PB2A16S20P"
    assert "protocolVer" not in values


def test_a_whole_table_snapshot_never_asks_for_more_than_the_cap(device, pair):
    link = pair[2]
    device.snapshot(RUNTIME)
    for frame in link.sent:
        if len(frame) == 8 and frame[1] == 0x03:
            assert int.from_bytes(frame[4:6], "big") <= 32


def test_writing_by_name_reads_back(device):
    device.write("volCellUV", 2.9, SETTINGS)
    assert device.read("volCellUV", SETTINGS) == 2.9


def test_a_read_only_register_is_refused_before_anything_goes_out(device, pair):
    link = pair[2]
    before = len(link.sent)
    with pytest.raises(DeviceError, match="read-only"):
        device.write("deviceSN", "nope", INFO)
    assert len(link.sent) == before


def test_setting_one_bit_leaves_the_others_alone(device):
    device.write("switchStatus", 0x0011, SETTINGS)
    word = device.set_bit("switchStatus", 6, True, SETTINGS)
    assert word == 0x0051
    assert device.read("switchStatus", SETTINGS) == 0x0051
    assert device.set_bit("switchStatus", 0, False, SETTINGS) == 0x0050


def test_setting_a_bit_that_is_already_set_writes_nothing(device, pair):
    link = pair[2]
    device.write("switchStatus", 0x0010, SETTINGS)
    before = len([f for f in link.sent if f[1] == 0x10])
    device.set_bit("switchStatus", 4, True, SETTINGS)
    assert len([f for f in link.sent if f[1] == 0x10]) == before


def test_an_action_reaches_the_command_space(device, sim):
    device.action("lifepo4", 1)
    assert sim.actions == [(0x0C, 1)]


def test_presence_and_scanning(bus, catalog):
    assert Device(bus, 1, catalog).present()
    assert not Device(bus, 7, catalog).present()
    assert [slave for slave, _ in scan(bus, range(4), catalog)] == [1]


def test_scanning_does_not_burn_retries_on_empty_addresses(bus, catalog):
    bus.retries = 5
    scan(bus, range(4), catalog)
    assert bus.retries == 5  # restored afterwards


def test_a_board_the_sweep_found_is_read_with_the_retries_back(
    bus, catalog, monkeypatch
):
    """Reading what a sweep hands back must not inherit the sweep's no-retries.

    Turning retries off is right for the probe -- fifteen empty addresses at
    three attempts each is a minute of nothing -- and wrong for everything
    done with what came back.  The nameplate read is four transactions, three
    of them longer than the probe, so on a wire that loses the odd frame it is
    the read that needs the retries most, and it was the one running without
    them.  Modelled here as "the first attempt at any long read is lost".
    """
    real = Bus._read_registers_once
    lost: set[tuple[int, int, int]] = set()

    def flaky(self, slave: int, reg: int, count: int) -> bytes:
        if count > 8 and (slave, reg, count) not in lost:
            lost.add((slave, reg, count))
            raise ModbusError("no/short response")
        return real(self, slave, reg, count)

    monkeypatch.setattr(Bus, "_read_registers_once", flaky)
    bus.retries = 2
    models = [I.read(dev).model for _slave, dev in scan(bus, range(4), catalog)]
    assert models == ["JK_PB2A16S20P"]
    assert lost  # the link really did drop frames


def test_an_unknown_register_name_says_so(device):
    with pytest.raises(KeyError):
        device.read("noSuchThing")


def test_a_refused_read_still_raises_for_a_single_field(device, bus):
    with pytest.raises(ModbusError):
        bus.read_payload(1, "03", 292, 2)


def test_a_sub_word_field_does_not_clobber_its_neighbour(device):
    # uart1ProtoNo (byte 178) and canProtoNo (byte 179) are the two halves of
    # one Modbus register; a write of either must leave the other alone.
    before = device.read("canProtoNo", INFO)
    device.write("uart1ProtoNo", 13, INFO)
    assert device.read("uart1ProtoNo", INFO) == 13
    assert device.read("canProtoNo", INFO) == before


def test_the_other_half_of_a_shared_register_is_writable_too(device):
    device.write("canProtoNo", 2, INFO)
    device.write("uart1ProtoNo", 13, INFO)
    assert (device.read("canProtoNo", INFO), device.read("uart1ProtoNo", INFO)) == (
        2,
        13,
    )


def test_the_present_temperature_sensors_are_named(catalog):
    """Bit 0 is the MOS probe, so "sensor 1" pointed at the wrong wire."""
    from jkctl import runtime as R

    reading = R.Runtime(fields={"tempSensorAbsent": 0b001001}, catalog=catalog)
    assert reading.sensors_present == ["MOS", "Battery 3"]


def test_a_unit_that_did_not_answer_for_the_word_says_nothing_either_way(catalog):
    """An empty list is "this unit has no probes"; not answering is not that."""
    from jkctl import runtime as R

    assert R.Runtime(fields={}, catalog=catalog).sensors_present is None
    assert (
        R.Runtime(fields={"tempSensorAbsent": 0}, catalog=catalog).sensors_present == []
    )


def test_a_raised_bit_is_a_probe_that_is_there(catalog):
    """The bug this settles: six raised bits under six temperatures read as six
    missing probes, because the machine name was believed over the label."""
    from jkctl import doctor as D, runtime as R

    every = {"tempSensorAbsent": 0b111111}
    every.update(dict.fromkeys(R.TEMPERATURE_FIELDS, 24.0))
    reading = R.Runtime(fields=every, catalog=catalog)
    assert len(reading.sensors_present or []) == 6
    assert reading.sensors_silent == []
    report = D.Report()
    D._check_sensors(report, reading)
    assert report.findings == []


def test_the_doctor_names_the_probe_that_stopped_reading(catalog):
    from jkctl import doctor as D, runtime as R

    report = D.Report()
    D._check_sensors(
        report, R.Runtime(fields={"tempSensorAbsent": 0b1}, catalog=catalog)
    )
    assert "MOS" in report.findings[0].detail


def test_a_register_the_board_refuses_whatever_it_is_sent(device, sim, monkeypatch):
    # Firmware 15.41 refuses the discharge under-temperature pair at 0x1122
    # (jkctl-trace-20260926-074705).  Writing back what the register holds is
    # how jkctl tells that from a refused value, and it is refused too.
    with pytest.raises(WriteRefused) as caught:
        device.write("tmpBatDCHUT", -5, SETTINGS)
    assert caught.value.unwritable
    assert not caught.value.traceable  # diagnosed: a recording adds nothing
    assert "does not take Discharge UTP (tmpBatDCHUT)" in str(caught.value)
    assert set(device.unwritable) == {"tmpBatDCHUT", "tmpBatDCHUTPR"}
    assert device.read("tmpBatDCHUT", SETTINGS) == -20
    # Known now, so the next attempt is turned down without a frame.
    writes = []
    monkeypatch.setattr(sim, "_handle_write", lambda *a: writes.append(a))
    with pytest.raises(DeviceError, match="does not take"):
        device.write("tmpBatDCHUTPR", -10, SETTINGS)
    assert writes == []


def test_a_value_the_board_refuses_is_told_from_a_register_it_refuses(
    device, sim, monkeypatch
):
    # The same exception 2 for one value only: the board takes its own value
    # back, so the register is writable and it is the value that is wrong.
    sim.unwritable = ()
    real = sim._handle_write

    def picky(slave, fc, reg, cnt, data):
        if data[:1] == bytes([0xFB]):
            return sim._refuse(slave, fc)
        return real(slave, fc, reg, cnt, data)

    monkeypatch.setattr(sim, "_handle_write", picky)
    with pytest.raises(WriteRefused) as caught:
        device.write("tmpBatDCHUT", -5, SETTINGS)
    assert not caught.value.unwritable
    assert "refused this value of Discharge UTP" in str(caught.value)
    assert device.unwritable == {}
    device.write("tmpBatDCHUT", -6, SETTINGS)
    assert device.read("tmpBatDCHUT", SETTINGS) == -6
