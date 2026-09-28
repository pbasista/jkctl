"""The stored fault records: their names, their layout, and reading them."""

from __future__ import annotations

import struct

import pytest

from jkctl import history as H, logcodes

# --- the names ------------------------------------------------------------------------


def test_the_extracted_table_covers_every_code_the_app_names():
    names = logcodes.all_names()
    # 73 fixed codes plus two ranges of 32 per-cell protections
    assert len(names) == 73 + 64
    assert names[1] == "Boot"
    assert names[39] == "Reset Watch-Dog"
    assert names[72] == "Discharge under temperature protection"


def test_the_fixed_codes_have_no_holes():
    names = logcodes.all_names()
    assert [c for c in range(1, 74) if c not in names] == []


def test_the_per_cell_ranges_are_expanded():
    assert logcodes.name(100) == "Cell 1 over charge protection"
    assert logcodes.name(131) == "Cell 32 over charge protection"
    assert logcodes.name(200) == "Cell 1 over discharge protection"
    assert logcodes.name(231) == "Cell 32 over discharge protection"


def test_a_code_nobody_named_is_not_guessed_at():
    assert not logcodes.known(300)
    assert logcodes.name(300) == "code 300"


# --- the record layout ----------------------------------------------------------------


def a_record(code=29, rtc=100_000, **over):
    """Build one record's bytes the way the device would serve them."""
    values = {
        "rtc": rtc,
        "code": code,
        "switches": 0b0011,  # charging and discharging closed
        "max_no": 5,
        "min_no": 2,
        "cell_max": 3450,
        "cell_min": 3120,
        "pack_v": 5327,  # centivolts
        "pack_a": 265,  # tenths of an amp
        "remaining": 2968,
        "full": 3120,
        "max_temp": 24,
        "min_temp": -7,
        "mos_temp": 31,
        "heat": 15,
    }
    values.update(over)
    return struct.pack(H.RECORD_FORMAT, *values.values())


def test_a_record_is_twenty_four_bytes_and_the_frame_measures_out():
    assert H.RECORD_BYTES == 24
    # header + record 1 + the eleven-record array + two reserved == one payload
    assert H.HEADER_BYTES + H.RECORDS_PER_FRAME * H.RECORD_BYTES + 2 == 293


def test_a_record_decodes_to_physical_values():
    record = H.decode_record(a_record())
    assert record.name == "Cell undervoltage protection"
    assert record.pack_v == 53.27
    assert record.pack_a == 26.5
    assert record.cell_min_v == 3.12 and record.cell_max_v == 3.45
    assert record.remaining_ah == 296.8
    assert record.closed == ["charging", "discharging"]
    assert record.when is not None and record.when.year == 2020


def test_a_below_freezing_reading_survives():
    # The whole point of a charge-under-temperature record.
    assert H.decode_record(a_record(min_temp=-20)).min_temp_c == -20


def test_a_short_record_is_refused_rather_than_padded():
    with pytest.raises(H.HistoryError):
        H.decode_record(b"\x00" * 10)


def test_a_frame_decodes_the_records_it_says_it_holds():
    payload = (3).to_bytes(2, "big") + bytes([2]) + a_record() + a_record(code=1)
    records = H.decode_frame(payload)
    assert [r.index for r in records] == [3, 4]
    assert [r.name for r in records] == ["Cell undervoltage protection", "Boot"]


def test_a_frame_claiming_more_than_it_carries_stops_at_what_it_has():
    payload = (0).to_bytes(2, "big") + bytes([12]) + a_record()
    assert len(H.decode_frame(payload)) == 1


def test_reading_a_board_that_does_not_map_them_says_so(device):
    # The simulator maps the three documented tables and nothing else, which
    # is exactly the case this has to report honestly: records are not on the
    # wire, and the message says so and points at the flash-dump route.
    with pytest.raises(
        H.HistoryError, match="does not serve the stored records over Modbus"
    ):
        H.read(device)


# --- the flash-stored records ---------------------------------------------------------


def a_stored_record(code=29, rtc=100_000, **over):
    """Build one record's bytes the way flash stores them: little-endian."""
    values = {
        "rtc": rtc,
        "code": code,
        "switches": 0b0011,
        "max_no": 5,
        "min_no": 2,
        "cell_max": 3450,
        "cell_min": 3120,
        "pack_v": 5327,
        "pack_a": 265,
        "remaining": 2968,
        "full": 3120,
        "max_temp": 24,
        "min_temp": -7,
        "mos_temp": 31,
        "heat": 15,
    }
    values.update(over)
    return struct.pack(H.STORED_RECORD_FORMAT, *values.values())


def _image_with(records, *, base=H.STORAGE_BASE, size=0x20000):
    """A flash image (erased everywhere) with records laid into the ring pages."""
    image = bytearray(b"\xff" * size)
    off = base - H.FLASH_BASE
    for page, page_records in enumerate(records):
        page_off = off + page * H.STORAGE_PAGE
        for slot, record in enumerate(page_records):
            at = page_off + slot * H.RECORD_BYTES
            image[at : at + H.RECORD_BYTES] = record
    return bytes(image)


def test_a_stored_record_decodes_little_endian():
    record = H.decode_stored_record(a_stored_record())
    assert record.name == "Cell undervoltage protection"
    assert record.pack_v == 53.27
    assert record.pack_a == 26.5
    assert record.cell_min_v == 3.12 and record.cell_max_v == 3.45
    assert record.remaining_ah == 296.8
    assert record.closed == ["charging", "discharging"]
    # The same bytes read big-endian (the wire layout) are not this record --
    # the byte order is the one thing that differs between flash and wire.
    assert H.decode_record(a_stored_record()).pack_v != 53.27


def test_stored_pack_current_is_signed():
    # A discharge record: the current is negative, not a huge unsigned number.
    assert H.decode_stored_record(a_stored_record(pack_a=-457)).pack_a == -45.7


def test_read_dump_recovers_records_across_pages_newest_first():
    older = [a_stored_record(code=1, rtc=1_000), a_stored_record(code=2, rtc=2_000)]
    newer = [a_stored_record(code=4, rtc=9_000)]
    records = H.read_dump(_image_with([older, newer]))
    assert [r.rtc for r in records] == [9_000, 2_000, 1_000]  # newest first
    assert [r.index for r in records] == [0, 1, 2]  # reindexed after sorting
    assert [r.code for r in records] == [4, 2, 1]


def test_read_dump_of_an_erased_region_is_empty():
    assert H.read_dump(b"\xff" * 0x20000) == []


def test_read_dump_skips_slots_that_are_not_records():
    # An all-zero slot (code 0, unnamed) is not a record and must not be read.
    image = _image_with([[bytes(H.RECORD_BYTES), a_stored_record(code=7, rtc=5_000)]])
    records = H.read_dump(image)
    assert [r.code for r in records] == [7]


def test_read_dump_of_a_short_image_is_empty():
    # A capture that does not reach the record region yields nothing, not an error.
    assert H.read_dump(b"\x00" * 0x1000) == []
