"""The parser, the dispatch table, and the exit codes."""

from __future__ import annotations

import pytest
from devicectl.cli.command import Need
from devicectl.cli.output import display_width, print_table

from jkctl import cli
from jkctl.cli.commands import COMMANDS, GROUPS
from jkctl.cli.parser import (
    DEFAULT_ACTIONS,
    insert_default_action,
    insert_default_command,
)


def test_no_command_serves_the_ui():
    # `jkctl` on its own is `jkctl ui`: that is how most people use it, and a
    # page of usage text is not what they came for.  Tested on the argv
    # rewrite rather than through main(), which would bind a socket.
    assert insert_default_command([]) == ["ui"]


def test_options_with_no_command_are_handed_to_the_ui():
    assert insert_default_command(["--port", "/dev/ttyUSB1"]) == [
        "ui",
        "--port",
        "/dev/ttyUSB1",
    ]


def test_help_and_version_still_reach_the_root_parser():
    for word in ("-h", "--help", "--version"):
        assert insert_default_command([word]) == [word]


def test_a_named_command_is_left_alone():
    assert insert_default_command(["status", "--json"]) == ["status", "--json"]


def test_the_version_is_the_packages_own(capsys):
    from jkctl import __version__

    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_every_group_registers_its_commands():
    named = set()
    for group in GROUPS:
        named |= set(group.COMMANDS)
    assert named == set(COMMANDS)


def test_no_two_groups_claim_the_same_command():
    seen = []
    for group in GROUPS:
        seen += list(group.COMMANDS)
    assert len(seen) == len(set(seen))


def test_every_command_is_reachable_from_the_parser():
    parser = cli.build_parser()
    action = next(a for a in parser._actions if a.dest == "command")
    assert set(action.choices) == set(COMMANDS)


def test_the_commands_that_need_no_port_say_so():
    assert COMMANDS["config"].needs is Need.NOTHING
    assert COMMANDS["simulate"].needs is Need.NOTHING
    assert COMMANDS["scan"].needs is Need.LINK
    assert COMMANDS["probe"].needs is Need.LINK
    assert COMMANDS["status"].needs is Need.READY
    # An action that reads a file needs nothing; one that reads a unit does.
    assert COMMANDS["firmware"].need("info") is Need.NOTHING
    assert COMMANDS["firmware"].need("flash") is Need.READY
    assert COMMANDS["protocols"].need("list") is Need.NOTHING


def test_a_default_action_is_filled_in():
    assert insert_default_action(["settings"]) == ["settings", "show"]
    assert insert_default_action(["settings", "--json"]) == [
        "settings",
        "show",
        "--json",
    ]


def test_a_named_action_and_help_are_left_alone():
    assert insert_default_action(["settings", "set", "a=1"]) == [
        "settings",
        "set",
        "a=1",
    ]
    assert insert_default_action(["settings", "-h"]) == ["settings", "-h"]
    assert insert_default_action(["status"]) == ["status"]
    assert insert_default_action([]) == []


def test_only_read_only_actions_have_a_default():
    # Nothing that changes the battery may happen because a word was omitted.
    for command in ("preset", "calibrate", "shutdown", "firmware"):
        assert command not in DEFAULT_ACTIONS
    for command, action in DEFAULT_ACTIONS.items():
        assert action in ("show", "list", "status")


def test_a_bad_port_is_reported_not_raised(capsys):
    assert cli.main(["status", "--port", "/dev/definitely-not-here"]) == cli.EXIT_ERROR
    assert "error: cannot open" in capsys.readouterr().err


def test_an_unknown_command_is_argparses_own_error(capsys):
    with pytest.raises(SystemExit):
        cli.main(["frobnicate"])
    assert "invalid choice" in capsys.readouterr().err


def test_tables_are_padded_by_terminal_width_not_character_count(capsys):
    # The vendor's protocol names are Chinese, and a wide character takes two
    # columns; padding by len() would leave the table visibly ragged.
    assert display_width("极空BMS") == 7
    print_table(["A", "B"], [["极空", "x"], ["ab", "y"]])
    lines = capsys.readouterr().out.splitlines()
    # The second column starts in the same terminal column on both rows, which
    # is not the same as the same character index.
    assert display_width(lines[1].split("x")[0]) == display_width(
        lines[2].split("y")[0]
    )


def _walk_subparsers(parser):
    """Yield every parser reachable from the root, including action parsers."""
    import argparse

    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from _walk_subparsers(child)


def test_no_positional_shadows_a_shared_option():
    # Every subparser inherits the connection options, so a positional named
    # `port` would silently overwrite --port and the command would address the
    # wrong thing.  This caught exactly that in `protocols set`.
    from jkctl.cli.parser import common_options

    shared = {a.dest for a in common_options()._actions if a.dest != "help"}
    for parser in _walk_subparsers(cli.build_parser()):
        positionals = {
            a.dest for a in parser._actions if not a.option_strings and a.dest != "help"
        }
        assert not positionals & shared, f"{parser.prog}: {positionals & shared}"


def test_every_leaf_that_needs_a_unit_accepts_the_connection_options():
    # A command that opens the bus but forgot parents=[common] would take no
    # --port and always talk to the default one.
    import argparse

    from devicectl.cli.command import Need

    from jkctl.cli.commands import COMMANDS

    for parser in _walk_subparsers(cli.build_parser()):
        words = parser.prog.split()[1:]  # drop "jkctl"
        if not words or words[0] not in COMMANDS:
            continue
        if any(isinstance(a, argparse._SubParsersAction) for a in parser._actions):
            continue  # a group; its actions carry the options
        action = words[1] if len(words) > 1 else None
        if COMMANDS[words[0]].need(action) is Need.NOTHING:
            continue
        options = {o for a in parser._actions for o in a.option_strings}
        assert "--port" in options, parser.prog


def test_parse_ids_takes_a_list_a_range_and_all():
    from jkctl.cli.fanout import parse_ids

    assert parse_ids("1") == [1]
    assert parse_ids("1,2,5") == [1, 2, 5]
    assert parse_ids("1-4") == [1, 2, 3, 4]
    assert parse_ids(" 3 , 3 , 1 ") == [3, 1]  # order kept, repeats dropped
    assert parse_ids("all") is None
    for bad in ("", "16", "1-99", "x"):
        with pytest.raises(ValueError):
            parse_ids(bad)


def _ui_args(**over):
    import argparse

    ns = argparse.Namespace(port=None, device=None)
    for key, value in over.items():
        setattr(ns, key, value)
    return ns


def test_a_lone_default_port_is_taken_as_the_chosen_one(monkeypatch):
    # One adapter plugged in and nothing to choose between: opening the page
    # on a picker with a single row in it would be a question with one answer.
    from jkctl.cli.commands import ui
    from jkctl.config import Config

    monkeypatch.setattr(ui.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(ui, "_adapter_count", lambda: 1)
    assert ui._chosen_port(_ui_args(), Config()) is True


def test_several_adapters_open_the_picker(monkeypatch):
    # The Raspberry Pi case: /dev/ttyUSB0 exists, but so do ttyUSB1 and
    # ttyUSB2, and which of the three the battery is on is not ours to guess.
    from jkctl.cli.commands import ui
    from jkctl.config import Config

    monkeypatch.setattr(ui.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(ui, "_adapter_count", lambda: 3)
    assert ui._chosen_port(_ui_args(), Config()) is False


def test_a_named_port_beats_however_many_adapters_there_are(monkeypatch):
    from jkctl.cli.commands import ui
    from jkctl.config import Config

    monkeypatch.setattr(ui, "_adapter_count", lambda: 3)
    assert ui._chosen_port(_ui_args(port="/dev/ttyUSB2"), Config()) is True
    assert ui._chosen_port(_ui_args(), Config(port="/dev/ttyUSB2")) is True


def test_no_default_port_opens_the_picker(monkeypatch):
    from jkctl.cli.commands import ui
    from jkctl.config import Config

    monkeypatch.setattr(ui.os.path, "exists", lambda _p: False)
    assert ui._chosen_port(_ui_args(), Config()) is False


def test_adapters_are_counted_the_way_the_picker_marks_them(monkeypatch):
    # An onboard /dev/ttyS0 alongside the one USB adapter is not a choice.
    from jkctl.cli.commands import ui

    listed = [
        {"device": "/dev/ttyUSB0", "likely": True},
        {"device": "/dev/ttyS0", "likely": False},
    ]
    monkeypatch.setattr("jkctl.web.api.list_serial_ports", lambda: listed)
    assert ui._adapter_count() == 1
    monkeypatch.setattr("jkctl.web.api.list_serial_ports", lambda: [])
    assert ui._adapter_count() == 1  # nothing enumerable: keep the old default
