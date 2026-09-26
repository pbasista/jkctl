"""The optional jk.toml file, and how it combines with the command line."""

from __future__ import annotations

import argparse

import pytest

from jkctl import modbus as M
from jkctl.cli.target import DEFAULT_ID, first_set, resolve_target
from jkctl.config import Config, default_config_dir, load_config

SAMPLE = """\
port = "/dev/ttyS9"
baud = 9600
id = 3

[devices.shed]
id = 2

[devices.garage]
port = "/dev/ttyUSB7"
timeout = 1.5
"""


def args(**overrides) -> argparse.Namespace:
    """Build the namespace the common options produce, all unset by default."""
    base = dict(
        device=None,
        port=None,
        baud=None,
        id=None,
        timeout=None,
        retries=None,
        addr_offset=None,
        config=None,
        trace=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def write(tmp_path, text: str):
    """Write a configuration file and return its path."""
    path = tmp_path / "jk.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_missing_file_is_not_an_error(tmp_path):
    config = load_config(tmp_path / "nothing.toml")
    assert config.port is None and config.devices == {}


def test_the_file_is_parsed(tmp_path):
    config = load_config(write(tmp_path, SAMPLE))
    assert (config.port, config.baud, config.id) == ("/dev/ttyS9", 9600, 3)
    assert config.device("shed").id == 2
    assert config.device("GARAGE").port == "/dev/ttyUSB7"
    assert config.device("nowhere") is None


def test_an_unknown_key_is_rejected_rather_than_ignored(tmp_path):
    with pytest.raises(ValueError, match="unknown key"):
        load_config(write(tmp_path, "prot = '/dev/ttyUSB0'\n"))
    with pytest.raises(ValueError, match=r"unknown key.*\[devices.a\]"):
        load_config(write(tmp_path, "[devices.a]\nspeed = 1\n"))


def test_a_wrong_type_says_which_key(tmp_path):
    with pytest.raises(ValueError, match="'baud' must be an integer"):
        load_config(write(tmp_path, "baud = 'fast'\n"))
    with pytest.raises(ValueError, match="'port' must be a string"):
        load_config(write(tmp_path, "port = 7\n"))
    # A bool is an int in Python; it is still not a baud rate.
    with pytest.raises(ValueError, match="'id' must be an integer"):
        load_config(write(tmp_path, "id = true\n"))


def test_first_set_takes_the_most_specific_value_given():
    assert first_set(None, None, 5, default=1) == 5
    assert first_set(None, None, None, default=1) == 1
    assert first_set(0, 9, default=1) == 0  # 0 was given, and 0 is not None


def test_the_command_line_beats_the_device_table_beats_the_defaults(tmp_path):
    config = load_config(write(tmp_path, SAMPLE))
    # Nothing on the command line: the file's own defaults.
    target = resolve_target(args(), config)
    assert (target.port, target.baud, target.slave) == ("/dev/ttyS9", 9600, 3)
    # A named device overrides what it names, and inherits the rest.
    target = resolve_target(args(device="garage"), config)
    assert (target.port, target.baud, target.slave, target.timeout) == (
        "/dev/ttyUSB7",
        9600,
        3,
        1.5,
    )
    # A flag beats both.
    target = resolve_target(args(device="garage", port="/dev/ttyUSB0", id=1), config)
    assert (target.port, target.slave) == ("/dev/ttyUSB0", 1)


def test_the_built_in_defaults_apply_with_no_file_at_all():
    target = resolve_target(args(), Config())
    assert target.port == M.DEFAULT_PORT
    assert target.baud == M.DEFAULT_BAUD
    assert target.slave == DEFAULT_ID
    assert target.addr_offset == M.FRAME_ADDR_OFFSET


def test_a_device_name_that_is_not_in_the_file_is_an_error(tmp_path):
    config = load_config(write(tmp_path, SAMPLE))
    with pytest.raises(ValueError, match="no device named 'attic'"):
        resolve_target(args(device="attic"), config)


def test_xdg_config_home_wins_over_the_platform_default(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_dir() == tmp_path / "jk"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert default_config_dir().name == "jk"
