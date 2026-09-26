"""The datasource-derived frame layout, and encode/decode round trips."""

from __future__ import annotations

import pytest

from jkctl import protocol as P

# The real frame/03 bytes the live unit returned on 2026-09-07, as recorded in
# the findings.  Everything about the addressing and the byte order was settled
# by decoding exactly these bytes, so they stay as the regression fixture.
LIVE_INFO_HEAD = (
    b"JK_PB2A16S20P".ljust(15, b"\x00")  # 0x000 manuDeviceID, 15 bytes
    + bytes([16])  # 0x00F maxCells
    + b"15A".ljust(8, b"\x00")  # 0x010 hardwareVersion
    + b"15.41".ljust(8, b"\x00")  # 0x018 softwareVersion
)


@pytest.fixture(scope="module")
def proto() -> P.Protocol:
    return P.Protocol()


def test_every_table_measures_out_to_the_frame_end(proto):
    # The size model is validated by all six tables ending at offset 299.
    for code in ("01", "02", "03", "04", "05"):
        last = proto.tables[code][-1]
        assert last.off + last.size == P.CHECK_OFF, code


def test_known_offsets_match_the_vendor_register_map(proto):
    # JK's own Modbus document: the "address" column is the payload byte
    # offset, and doubles as the register offset from the table base.
    for table, key, offset in (
        ("03", "manuDeviceID", 0),
        ("03", "hardwareVersion", 16),
        ("03", "softwareVersion", 24),
        ("03", "oddRunTime", 32),
        ("03", "pwrOnTimes", 36),
        ("03", "uart1ProtoNo", 178),
        ("02", "cellVol", 0),
        ("02", "batVol", 144),
        ("02", "batWatt", 148),
        ("01", "volCellUV", 4),
        ("01", "devAddr", 264),
        ("01", "switchStatus", 276),
    ):
        assert proto.field(table, key).off - P.DATA_OFF == offset, key


def test_modbus_numbers_are_big_endian(proto):
    # Live proof: cell bytes 0d 01 are 3.329 V big-endian, 0.269 V little.
    f = proto.field("02", "cellVol")
    frame = bytearray(P.FRAME_LEN)
    frame[f.off : f.off + 2] = bytes.fromhex("0d01")
    big = P.decode_field(f, bytes(frame), P.MODBUS_BYTEORDER)
    little = P.decode_field(f, bytes(frame), P.NATIVE_BYTEORDER)
    assert big[0] == 3.329
    assert little[0] == 0.269


def test_bitmaps_follow_the_transport_byte_order(proto):
    # sysAlarm is a UINT32 in the vendor map, so over Modbus it arrives
    # big-endian like every other number.  Bit 11 is cell under-voltage.
    f = proto.field("02", "sysAlarm")
    frame = bytearray(P.FRAME_LEN)
    frame[f.off : f.off + 4] = (1 << 11).to_bytes(4, "big")
    assert P.decode_field(f, bytes(frame), P.MODBUS_BYTEORDER) == 1 << 11
    assert P.decode_field(f, bytes(frame), P.NATIVE_BYTEORDER) != 1 << 11


def test_the_live_device_info_decodes(proto):
    frame = bytearray(P.FRAME_LEN)
    frame[P.DATA_OFF : P.DATA_OFF + len(LIVE_INFO_HEAD)] = LIVE_INFO_HEAD
    decoded = proto.decode("03", bytes(frame), P.MODBUS_BYTEORDER)
    assert decoded["manuDeviceID"] == "JK_PB2A16S20P"
    assert decoded["maxCells"] == 16
    assert decoded["hardwareVersion"] == "15A"
    assert decoded["softwareVersion"] == "15.41"


@pytest.mark.parametrize("byteorder", [P.NATIVE_BYTEORDER, P.MODBUS_BYTEORDER])
@pytest.mark.parametrize(
    "table,key,value",
    [
        ("01", "volCellUV", 2.9),
        ("01", "tmpBatCOT", -12.5),
        ("01", "cellCount", 16),
        ("01", "switchStatus", 0x0241),
        ("02", "cellVol", [3.3] * 32),
        ("03", "manuDeviceID", "JK_PB2A16S20P"),
    ],
)
def test_encode_round_trips_through_decode(proto, table, key, value, byteorder):
    f = proto.field(table, key)
    frame = bytearray(P.FRAME_LEN)
    frame[f.off : f.off + f.size] = P.encode_field(f, value, byteorder)
    assert P.decode_field(f, bytes(frame), byteorder) == value


def test_scaling_keeps_the_device_resolution_without_float_noise(proto):
    f = proto.field("02", "batCurrent")  # milliamps, scale 0.001
    frame = bytearray(P.FRAME_LEN)
    frame[f.off : f.off + 4] = (-12345 & 0xFFFFFFFF).to_bytes(4, "big")
    assert P.decode_field(f, bytes(frame), P.MODBUS_BYTEORDER) == -12.345


def test_a_string_too_long_for_its_field_is_refused(proto):
    with pytest.raises(ValueError):
        P.encode_field(proto.field("03", "hardwareVersion"), "much too long")


def test_the_datasource_carries_bounds_for_the_settable_fields(proto):
    f = proto.field("01", "volCellUV")
    assert (f.minimum, f.maximum) == (1.2, 4.4)


@pytest.mark.parametrize(
    "key", ["tmpStartHeating", "tmpStopHeating", "tmpBatDCHUT", "tmpBatDCHUTPR"]
)
def test_an_untyped_byte_with_a_negative_range_is_signed(proto, key):
    # The datasource gives these four no "nt" and a minimum of -40 degC; they
    # go out two's complement, which is what a C++ program writing -5 into a
    # byte sends whatever it calls the byte.
    f = proto.field("01", key)
    assert f.ntype == "i8"
    assert P.encode_field(f, -5, P.MODBUS_BYTEORDER) == b"\xfb"
    frame = bytearray(P.FRAME_LEN)
    frame[f.off] = 0xFB
    assert P.decode_field(f, bytes(frame), P.MODBUS_BYTEORDER) == -5


def test_an_untyped_byte_with_no_negative_range_stays_unsigned(proto):
    assert all(
        f.ntype != "i8"
        for f in proto.tables["01"]
        if f.kind == "n" and (f.minimum or 0) >= 0
    )
