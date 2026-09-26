"""Coercing typed input, formatting output, and refusing what will not fit."""

from __future__ import annotations

import pytest

from jkctl import values as V
from jkctl.registers import INFO, RUNTIME, SETTINGS


def test_a_scaled_number_is_taken_in_display_units(catalog):
    reg = catalog.find("volCellUV", SETTINGS)
    assert V.coerce_input(reg, "2.9") == 2.9
    assert V.encode(reg, 2.9) == (2900).to_bytes(4, "big")


def test_a_value_outside_the_datasource_bounds_is_refused(catalog):
    reg = catalog.find("volCellUV", SETTINGS)
    with pytest.raises(V.RegisterValueError, match="above the maximum"):
        V.coerce_input(reg, "9")
    with pytest.raises(V.RegisterValueError, match="below the minimum"):
        V.coerce_input(reg, "0.5")


def test_a_flag_takes_an_on_off_word(catalog):
    reg = catalog.find("balanEn", SETTINGS)
    assert V.coerce_input(reg, "on") == 1
    assert V.coerce_input(reg, "OFF") == 0
    assert V.coerce_input(reg, "1") == 1


def test_an_enum_label_resolves_to_its_stored_value(catalog):
    reg = catalog.find("balanEn", SETTINGS)
    assert V.coerce_input(reg, "On") == 1
    assert V.format_value(reg, 1) == "On"


def test_nonsense_is_refused_rather_than_coerced_to_zero(catalog):
    reg = catalog.find("volCellUV", SETTINGS)
    with pytest.raises(V.RegisterValueError, match="not a number"):
        V.coerce_input(reg, "banana")


def test_a_value_that_does_not_fit_the_register_is_refused(catalog):
    # uart1ProtoNo is a u8 the datasource gives no bounds for, so the only
    # thing standing between 300 and a silently truncated write is the
    # register's own width.
    reg = catalog.find("uart1ProtoNo", INFO)
    with pytest.raises(V.RegisterValueError, match="does not fit"):
        V.coerce_input(reg, "300")


def test_a_bounded_value_is_refused_by_its_bounds_first(catalog):
    reg = catalog.find("timeSmartSleep", SETTINGS)  # u8 hours, max 100
    with pytest.raises(V.RegisterValueError, match="above the maximum"):
        V.coerce_input(reg, "300")


def test_a_bitmap_takes_hex_and_is_width_checked(catalog):
    reg = catalog.find("switchStatus", SETTINGS)
    assert V.coerce_input(reg, "0x0241") == 0x0241
    with pytest.raises(V.RegisterValueError, match="does not fit"):
        V.coerce_input(reg, "0x1FFFF")


def test_a_protocol_selector_takes_a_plain_number(catalog):
    assert V.coerce_input(catalog.find("uart1ProtoNo", INFO), "3") == 3


def test_an_array_needs_every_element(catalog):
    reg = catalog.find("cellConWireRes", SETTINGS)
    assert len(V.coerce_input(reg, ",".join(["0.1"] * 32))) == 32
    with pytest.raises(V.RegisterValueError, match="32 comma-separated"):
        V.coerce_input(reg, "0.1,0.2")


def test_formatting_uses_the_vendors_own_decimals_and_unit(catalog):
    assert V.format_value(catalog.find("volCellUV", SETTINGS), 2.9) == "2.900 V"
    assert V.format_value(catalog.find("batCurrent", RUNTIME), -12.345) == "-12.35 A"
    assert V.format_value(catalog.find("cellCount", SETTINGS), 16) == "16"
    assert V.format_value(catalog.find("volCellUV", SETTINGS), None) == "-"


def test_equality_is_judged_at_wire_precision(catalog):
    reg = catalog.find("volCellUV", SETTINGS)
    # The same millivolt: writing this again would be a no-op, and a settings
    # import that reported it as a change would write on every run.
    assert V.values_equal(reg, 2.9, 2.9004)
    assert not V.values_equal(reg, 2.9, 2.901)


def test_alarm_bits_are_named_from_the_datasource(catalog):
    reg = catalog.find("sysAlarm", RUNTIME)
    rows = V.decode_bits(reg, 1 << 11)
    raised = [(bit, name, state) for bit, name, is_set, state in rows if is_set]
    assert raised == [(11, "Protection - Cell under voltage", "Abnormal")]


def test_decoding_bits_of_nothing_yields_nothing(catalog):
    assert V.decode_bits(catalog.find("sysAlarm", RUNTIME), None) == []
