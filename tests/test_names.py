"""The page's names for the settings (jkctl.names)."""

from __future__ import annotations

from jkctl import names as N
from jkctl.registers import INFO, SETTINGS


def test_every_name_is_for_a_register_that_exists(catalog):
    known = {reg.key for table in (SETTINGS, INFO) for reg in catalog.table(table)}
    assert set(N.NAMES) <= known


def test_every_setting_a_card_shows_has_a_name_and_a_description(catalog):
    """A writable register without one falls back to JK's label, which is
    the name this module exists to replace.  `enableFlags` is on no card."""
    missing = [
        reg.key
        for table in (SETTINGS, INFO)
        for reg in catalog.table(table)
        if reg.writable and reg.key not in N.NAMES and reg.key != "enableFlags"
    ]
    assert missing == []


def test_no_two_settings_share_a_name():
    titles = [title for title, _ in N.NAMES.values()]
    assert len(titles) == len(set(titles))


def test_a_name_leaves_the_unit_to_the_unit():
    """ "Vol. Cell RCV" said voltage twice; the unit beside it already does."""
    for title, _ in N.NAMES.values():
        assert not title.lower().startswith(("vol.", "volt", "tmp ", "tim "))


def test_an_unnamed_register_keeps_jks_label():
    assert N.title_of("enableFlags", "Enable Flags") == "Enable Flags"
    assert N.description_of("enableFlags") is None
