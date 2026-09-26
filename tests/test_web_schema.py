"""Which editor the browser is told to draw for a register.

These rules were written against defects that only a rendered page showed:
three fields arrived as drop-downs with no usable entries, and the main
switches arrived as spin boxes.  Neither is visible to a type checker or to a
test that only asserts the JSON has the right keys, so the rules that fixed
them are pinned here by name -- a change to :func:`jkctl.web.schema._kind`
that reopens either defect fails on the field that showed it.
"""

from __future__ import annotations

import pytest

from jkctl.registers import RUNTIME, SETTINGS
from jkctl.web import schema


def kind_of(catalog, name, table=SETTINGS):
    """What editor the browser is told to draw for this register."""
    return schema.register_json(catalog.find(name, table))["kind"]


@pytest.mark.parametrize(
    "name",
    [
        "devAddr",  # 0..65535, labelled with the single word "on"
        "currentRange",  # a current in amps, labelled "off"/"on"
        "dischrgPreChrgT",  # 0..300 seconds, labelled "off"/"on"
    ],
)
def test_a_stray_option_table_does_not_make_a_field_a_drop_down(catalog, name):
    """A field whose labels do not cover its range is edited as a number.

    Each of these carries a two-entry table that has nothing to do with it.
    Offering those as a drop-down makes the bus address unsettable and turns a
    timer into a switch -- which is exactly what the page did before.
    """
    reg = catalog.find(name, SETTINGS)
    assert reg.options, f"{name} no longer carries the stray table this guards"
    assert kind_of(catalog, name) == "number"


@pytest.mark.parametrize("name", ["batChargeEn", "batDischargeEn", "balanEn"])
def test_the_main_switches_are_switches(catalog, name):
    """The three switches read the same way as each other.

    ``balanEn`` carries Off/On labels and its two neighbours carry none, so a
    rule that keyed on the labels would draw one drop-down and two spin boxes.
    """
    assert kind_of(catalog, name) == "flag"


def test_an_option_table_that_covers_its_range_is_a_drop_down(catalog):
    """A real enumeration still gets the drop-down it deserves.

    Cell Type names all three of the chemistries it can hold and has no range
    to contradict them.
    """
    assert kind_of(catalog, "batteryType", RUNTIME) == "enum"


def test_a_bitmap_is_offered_as_its_bits(catalog):
    """A sixteen-bit word is not a number anybody can usefully type."""
    assert kind_of(catalog, "switchStatus") == "bits"


def test_an_array_says_how_many_elements_it_has(catalog):
    """The Cells tab edits the wire resistances one element at a time."""
    doc = schema.register_json(catalog.find("cellConWireRes", SETTINGS))
    assert doc["kind"] == "array"
    assert doc["count"] == 32


def test_a_long_value_is_abbreviated_and_says_so(catalog):
    """An array renders to hundreds of characters; a table cell cannot hold it.

    The full value stays in ``value``, so the page can put it in a tooltip.
    """
    reg = catalog.find("cellVol", RUNTIME)
    doc = schema.register_json(reg, [3.301] * 32)
    assert doc["long"] is True
    assert len(doc["text"]) == schema.TEXT_MAX
    assert doc["text"].endswith("…")
    assert doc["value"] == [3.301] * 32


def test_a_short_value_is_left_alone(catalog):
    """Nothing is abbreviated that fits."""
    doc = schema.register_json(catalog.find("batVol", RUNTIME), 52.8)
    assert doc["long"] is False
    assert doc["text"] == "52.80 V"


def test_a_register_the_board_did_not_answer_for_keeps_its_shape(catalog):
    """An unanswered field stays in the document so the page does not resize."""
    doc = schema.register_json(catalog.find("batVol", RUNTIME), None, answered=False)
    assert doc["answered"] is False
    assert doc["text"] is None
    assert doc["label"] and doc["unit"]
