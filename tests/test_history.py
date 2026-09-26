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
    # is exactly the case this has to report honestly.
    with pytest.raises(H.HistoryError, match="does not answer at register"):
        H.read(device)
