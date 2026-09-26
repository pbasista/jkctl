"""Planning, applying, exporting and importing the configuration."""

from __future__ import annotations

import json

import pytest

from jkctl import settings as S
from jkctl.registers import SETTINGS


def test_reading_the_table_gives_the_configured_values(device):
    values = S.read(device)
    assert values["volCellOV"] == 3.65
    assert values["cellCount"] == 16


def test_a_plan_holds_only_what_differs(device):
    current = S.read(device)
    wanted = {"volCellUV": 2.9, "volCellOV": current["volCellOV"]}
    changes = S.plan(device, wanted)
    assert [c.register.key for c in changes] == ["volCellUV"]
    assert changes[0].old == 2.8 and changes[0].new == 2.9


def test_a_plan_is_in_table_order_not_argument_order(device):
    changes = S.plan(device, {"balanEn": 0, "volCellUV": 2.9})
    assert [c.register.key for c in changes] == ["volCellUV", "balanEn"]


def test_applying_a_plan_writes_it(device):
    changes = S.plan(device, {"volCellUV": 2.9})
    S.apply(device, changes)
    assert device.read("volCellUV", SETTINGS) == 2.9
    assert S.plan(device, {"volCellUV": 2.9}) == []


def test_assignments_are_parsed_and_checked(catalog):
    assert S.parse_assignments(catalog, ["volCellUV=2.9", "balanEn=off"]) == {
        "volCellUV": 2.9,
        "balanEn": 0,
    }
    with pytest.raises(S.SettingsError, match="not KEY=VALUE"):
        S.parse_assignments(catalog, ["volCellUV"])
    with pytest.raises(S.SettingsError, match="no setting named"):
        S.parse_assignments(catalog, ["nonsense=1"])


def test_a_register_from_another_table_cannot_be_planned(device):
    # batVol is a runtime reading, not a setting; asking for it must fail
    # loudly rather than be dropped from the plan.
    with pytest.raises(S.SettingsError, match="no setting named"):
        S.plan(device, {"batVol": 1.0})


def test_export_carries_only_the_writable_fields(device):
    doc = json.loads(S.export(device))
    assert doc["model"] == "JK_PB2A16S20P"
    assert "volCellUV" in doc["settings"]
    assert "deviceSN" not in doc["settings"]
    assert "batVol" not in doc["settings"]


def test_an_exported_file_round_trips_to_no_change(device):
    wanted = S.load(device.catalog, S.export(device))
    assert S.plan(device, wanted) == []


def test_import_ignores_a_field_this_build_does_not_know(device):
    text = json.dumps({"settings": {"volCellUV": 2.9, "somethingNewer": 1}})
    assert S.load(device.catalog, text) == {"volCellUV": 2.9}


def test_import_rejects_a_file_that_is_not_one(catalog):
    with pytest.raises(S.SettingsError, match="not a settings file"):
        S.load(catalog, "{{{")
    with pytest.raises(S.SettingsError, match="no 'settings' object"):
        S.load(catalog, "{}")


def test_the_switch_word_is_the_one_from_the_settings_table(catalog):
    bits = dict(S.switch_bits(catalog))
    assert bits[6] == "Smart Sleep"
    assert bits[3] == "Multiplexed Port Switching"
    assert len(bits) == 16


def test_a_switch_is_found_by_the_name_shown(catalog):
    assert S.find_switch(catalog, "Smart Sleep") == 6
    assert S.find_switch(catalog, "smart-sleep") == 6
    with pytest.raises(S.SettingsError, match="no switch named"):
        S.find_switch(catalog, "nope")


def test_the_switch_word_decodes_to_the_vendors_own_words(catalog):
    rows = S.switch_state(catalog, 0x0008)
    port = next(r for r in rows if r[0] == 3)
    assert port[3] == "RS485"
    assert next(r for r in rows if r[0] == 0)[3] == "OFF"


def test_a_chain_of_setpoints_is_written_without_crossing(device):
    from jkctl import controls as C

    down = [c.name for c in S.plan(device, C.preset_values("lto"))]
    volts = [n for n in down if n in S.CHAINS[0]]
    assert volts == list(S.CHAINS[0])  # lowest first on the way down
    S.apply(device, S.plan(device, C.preset_values("lto")))
    up = [c.name for c in S.plan(device, C.preset_values("li-ion"))]
    assert [n for n in up if n in S.CHAINS[0]] == list(reversed(S.CHAINS[0]))
