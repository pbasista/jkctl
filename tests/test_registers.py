"""The register catalog: names, addresses, access and the action table."""

from __future__ import annotations

import re

import pytest
from devicectl.fields import FLAG, NUMBER, TEXT, FieldSpec

from jkctl import protocol as P
from jkctl.registers import (
    ACTIONS,
    ARRAY,
    BITS,
    INFO,
    READ_ONLY,
    READ_WRITE,
    RUNTIME,
    SETTINGS,
    UPGRADE_SLOT,
    protocol_names,
)


def test_every_named_datasource_field_becomes_a_register(catalog):
    proto = P.Protocol()
    for table in (SETTINGS, RUNTIME, INFO):
        named = [f.key for f in proto.tables[table] if f.key]
        assert [r.key for r in catalog.table(table)] == named


def test_a_registers_address_is_its_byte_offset(catalog):
    assert catalog.find("manuDeviceID").register(0x1000) == 0x1400
    assert catalog.find("softwareVersion").register(0x1000) == 0x1418
    assert catalog.find("volCellUV", SETTINGS).register(0x1000) == 0x1004
    assert catalog.find("batWatt", RUNTIME).register(0x1000) == 0x1294


def test_a_register_is_the_shared_field_description(catalog):
    # The shared one was modelled on this class; this is the direction of that
    # arrow made real, so a change to either is a change to both.
    reg = catalog.find("volCellUV", SETTINGS)
    assert isinstance(reg, FieldSpec)
    assert reg.name == reg.key == "volCellUV"
    assert reg.address == reg.byte_off == 0x04
    assert reg.json == "volCellUV"
    assert reg.subject == reg.label


def test_a_register_says_once_which_kind_of_thing_it_is(catalog):
    # Both front ends ask this: the browser to pick an editor, the command
    # line to decide how to read what was typed.  One answer, on the register.
    assert catalog.find("cellConWireRes", SETTINGS).kind == ARRAY
    assert catalog.find("sysAlarm", RUNTIME).kind == BITS
    assert catalog.find("deviceSN", INFO).kind == TEXT
    assert catalog.find("balanEn", SETTINGS).kind == FLAG
    assert catalog.find("volCellUV", SETTINGS).kind == NUMBER


def test_a_stray_two_entry_option_table_does_not_make_a_number_a_drop_down(catalog):
    # devAddr ranges over 0..65535 and is labelled with the single word "on";
    # as an enum it would be unsettable.
    assert catalog.find("devAddr", SETTINGS).kind == NUMBER
    assert catalog.find("dischrgPreChrgT", SETTINGS).kind == NUMBER


def test_the_whole_settings_block_is_writable(catalog):
    assert all(r.writable for r in catalog.table(SETTINGS))


def test_the_runtime_table_is_read_only(catalog):
    assert all(r.access == READ_ONLY for r in catalog.table(RUNTIME))


def test_only_the_documented_device_info_fields_are_writable(catalog):
    writable = {r.key for r in catalog.table(INFO) if r.writable}
    # From JK's register map: the protocol selectors, the dry-contact and
    # buzzer triggers, and the charge-stage timers.  Not the identity fields.
    assert "uart1ProtoNo" in writable and "canProtoNo" in writable
    assert "dry1TriggerVal" in writable and "rcvTime" in writable
    for identity in (
        "manuDeviceID",
        "deviceSN",
        "softwareVersion",
        "settingPassword",
        "bluetoothPwd",
    ):
        assert catalog.find(identity, INFO).access == READ_ONLY


def test_a_key_in_two_tables_resolves_by_table_order(catalog):
    # switchStatus is a 16-bit configuration word in frame/01 and a 3-bit
    # read-only status byte in frame/02.  They must not be confused.
    settings = catalog.find("switchStatus", SETTINGS)
    runtime = catalog.find("switchStatus", RUNTIME)
    assert settings.size == 2 and runtime.size == 1
    assert settings.writable and not runtime.writable
    assert len(settings.options) == 16


def test_bounds_and_defaults_come_from_the_datasource(catalog):
    reg = catalog.find("volCellUV", SETTINGS)
    assert (reg.minimum, reg.maximum, reg.default) == (1.2, 4.4, 1.2)
    assert reg.unit == "V" and reg.decimals == 3


def test_match_finds_by_name_and_by_label(catalog):
    assert catalog.find("volCellOV", SETTINGS) in catalog.match("volcell*")
    assert catalog.find("volCellOV", SETTINGS) in catalog.match("cell ovp")


def test_the_action_slots_match_the_vendor_map():
    documented = [
        ("voltage-calibration", 0x00),
        ("shutdown", 0x04),
        ("current-calibration", 0x06),
        ("li-ion", 0x0A),
        ("lifepo4", 0x0C),
        ("lto", 0x0E),
        ("emergency", 0x10),
        ("time-calibration", 0x12),
    ]
    slots = [(name, a.slot) for name, a in ACTIONS.items()]
    assert slots[: len(documented)] == documented
    # The upgrade slot appears in neither revision of the vendor document, so
    # it is deliberately not one of the actions a person can invoke.
    assert UPGRADE_SLOT not in {a.slot for a in ACTIONS.values()}


def test_the_three_slots_recovered_from_the_application():
    # Not in either revision of JK's register map, whose action list stops at
    # 0x12; read out of the vendor application's own buttons instead, a
    # reading whose control is that it gives 0x04 for "Shutdown Board", which
    # the document *does* list at 0x04.  Findings §36.
    assert [(name, a.slot) for name, a in ACTIONS.items()][8:] == [
        ("restart", 0x16),
        ("factory-restore", 0x18),
        ("erase-data", 0x1A),
    ]
    # Each is irreversible, so each has to have a warning to read out.
    for name in ("restart", "factory-restore", "erase-data"):
        assert ACTIONS[name].warning


def test_the_destructive_actions_carry_a_warning():
    for name in (
        "lifepo4",
        "li-ion",
        "lto",
        "shutdown",
        "voltage-calibration",
        "current-calibration",
    ):
        assert ACTIONS[name].warning


def test_the_protocol_lists_are_shipped_and_indexable():
    names = protocol_names()
    assert names["uart"][1].startswith("001 - ")
    assert 0 in names["can"] and "trigger" in names


def test_an_unknown_name_is_an_error(catalog):
    with pytest.raises(KeyError):
        catalog.find("noSuchRegister")


def test_access_is_only_ever_r_or_rw(catalog):
    assert {r.access for r in catalog} <= {READ_ONLY, READ_WRITE}


# --- what the two languages leave behind ------------------------------------------------

CJK = re.compile(r"[⺀-鿿豈-﫿︰-﹏＀-￯]")


def test_nothing_the_page_shows_is_still_in_chinese(catalog):
    """JK ships one datasource for en_US and zh_CN, and did not finish it.

    Fifteen runtime labels, the six temperature-sensor bits and three Off/On
    pairs arrive in Chinese, which is what an English-speaking installer read
    on the Runtime tab.  The datasource is shipped verbatim, so the English is
    supplied by the catalog; this holds it to covering all of it.
    """
    left = []
    for reg in catalog:
        if CJK.search(reg.label or ""):
            left.append(f"{reg.table} {reg.key} label")
        left += [
            f"{reg.table} {reg.key} bit {bit}"
            for bit, text in (reg.options or {}).items()
            if CJK.search(str(text))
        ]
    assert left == []


def test_the_untranslated_labels_are_named_after_their_neighbours(catalog):
    # The runtime table's release-time fields are the settings table's delays
    # read back, so they carry the names the settings table already uses.
    assert catalog.find("timeUVPR", RUNTIME).label == "Cell UVPR Time"
    assert catalog.find("timBatDcOCPRDly", SETTINGS).label == "Discharge OCPR Time"
    assert catalog.find("runtime", RUNTIME).label == "Run Time"


def test_the_temperature_sensor_bits_are_named_probes(catalog):
    # Numbering them from one calls the MOS probe "sensor 1" and sends
    # somebody looking at the wrong wire.
    bits = catalog.find("tempSensorAbsent", RUNTIME).options
    assert bits["0"] == "MOS"
    assert bits["5"] == "Battery 5"


def test_an_alarm_bit_reads_out_its_own_condition(catalog):
    """Six bits of the system alarm word carried another bit's state word.

    A raised MOS over-temperature bit displayed "Over Voltage", which is the
    one thing on the page that has to be right.
    """
    bits = catalog.find("sysAlarm", RUNTIME).options
    for bit, expected in (
        ("1", "Over Temp."),
        ("5", "Over Voltage"),
        ("6", "Over Current"),
        ("7", "Short Circuit"),
    ):
        assert bits[bit].rsplit(";", 1)[1] == expected
    assert "short circuit" in bits["7"].lower()


def test_the_vendors_own_english_is_left_alone(catalog):
    # Only what JK left in Chinese, or got wrong, is replaced: a label here
    # should still be findable on JK's own screen.
    assert catalog.find("volCellUV", SETTINGS).label == "Cell UVP"
    assert catalog.find("socRelativeStateOfCharge", RUNTIME).label == "Remain Battery"


def test_the_protocol_lists_name_the_companies_they_mean():
    """`维克多` is Victron Energy, not "Victor".

    JK's own English list names it "CAN-BUS_BMS_Protocol_201707" against
    Victron Energy, which is the file the Chinese entry dates 20170717.  The
    other two were transliterations of company names that have English ones:
    日月元 is Voltronic Power and 鹏城 is Luxpowertek.
    """
    names = protocol_names()
    assert names["can"][4].startswith("004 - Victron Energy ")
    assert names["uart"][7].startswith("007 - Voltronic Power ")
    assert names["can"][11].startswith("011 - Luxpowertek ")
    assert not any("Victor " in n for n in names["can"].values())
