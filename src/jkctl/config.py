"""Optional TOML configuration for serial ports and device addresses.

So the port and the DIP-switch address do not have to be retyped on every
invocation, the CLI reads ``jk.toml`` (override with ``--config``).  Top-level
keys are defaults; ``[devices.<name>]`` tables override them per unit, and
``--device <name>`` picks one by name.  Command-line flags override everything.

See :data:`EXAMPLE_CONFIG` for the file ``jkctl config init`` writes, which
doubles as the reference for what may appear in one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10, via the tomli dependency
    import tomli as tomllib

from devicectl import paths

# Where the file lives when --config is not given; see default_config_path.
CONFIG_DIR_NAME = "jk"
CONFIG_FILE_NAME = "jk.toml"

# What ``jkctl config init`` writes.  It is a working file, all of it commented
# out, so someone can uncomment the two lines they need instead of getting the
# syntax wrong in an empty one.
EXAMPLE_CONFIG = """\
# jkctl configuration.  Every setting is optional, and a command-line flag
# beats anything written here.  Full reference: `jkctl --help`.

# The adapter and the address every command uses unless a device below
# overrides them.  The address is the DIP-switch id on the BMS (SW1 = bit 0
# .. SW4 = bit 3, ON = 1), which is also its Modbus slave address.
# port = "/dev/ttyUSB0"
# id = 1

# JK ships every board at 115200 8N1.  Protocol 013 is the same protocol at
# 9600; set this only if the unit has been switched to it.
# baud = 115200

# Raise these on a slow host -- a Raspberry Pi with a USB-RS485 adapter can
# need a whole second for the first reply.
# timeout = 0.35
# retries = 2

# A unit gets a name here, and `--device shed` then finds it.
# [devices.shed]
# id = 2
#
# [devices.garage]
# port = "/dev/ttyUSB1"
# id = 1
"""

# Keys accepted in the file, per device table and at the top level.  Unknown
# keys are rejected so a typo does not silently no-op.
_TOP_KEYS = frozenset(
    {"port", "baud", "id", "timeout", "retries", "addr_offset", "devices"}
)
_DEVICE_KEYS = frozenset({"port", "baud", "id", "timeout", "retries", "addr_offset"})


def default_config_dir() -> Path:
    """Return the directory the configuration lives in, per platform."""
    return paths.config_dir(CONFIG_DIR_NAME)


def default_config_path() -> Path:
    """Return the configuration file used when ``--config`` is not given."""
    return default_config_dir() / CONFIG_FILE_NAME


@dataclass
class DeviceConfig:
    """One ``[devices.<name>]`` table."""

    name: str
    port: str | None = None
    baud: int | None = None
    id: int | None = None
    timeout: float | None = None
    retries: int | None = None
    addr_offset: int | None = None


@dataclass
class Config:
    """The parsed configuration: defaults plus named devices."""

    path: Path | None = None
    port: str | None = None
    baud: int | None = None
    id: int | None = None
    timeout: float | None = None
    retries: int | None = None
    addr_offset: int | None = None
    devices: dict[str, DeviceConfig] = field(default_factory=dict)

    def device(self, name: str) -> DeviceConfig | None:
        """Return the device table for ``name`` (case-insensitive), or None."""
        exact = self.devices.get(name)
        if exact is not None:
            return exact
        lowered = name.lower()
        for key, dev in self.devices.items():
            if key.lower() == lowered:
                return dev
        return None


def _validate_keys(table: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    """Reject unknown keys so a typo cannot be silently ignored."""
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown key(s) in {where}: {', '.join(unknown)}")


def _int(table: dict[str, Any], key: str, where: str) -> int | None:
    """Read an integer key, rejecting a bool (which is an int in Python)."""
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{where} '{key}' must be an integer")
    return value


def _float(table: dict[str, Any], key: str, where: str) -> float | None:
    """Read a number key as a float."""
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{where} '{key}' must be a number")
    return float(value)


def _str(table: dict[str, Any], key: str, where: str) -> str | None:
    """Read a string key."""
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{where} '{key}' must be a string")
    return value


def _parse_device(name: str, table: dict[str, Any]) -> DeviceConfig:
    """Validate and convert one ``[devices.<name>]`` TOML table."""
    where = f"[devices.{name}]"
    _validate_keys(table, _DEVICE_KEYS, where)
    return DeviceConfig(
        name=name,
        port=_str(table, "port", where),
        baud=_int(table, "baud", where),
        id=_int(table, "id", where),
        timeout=_float(table, "timeout", where),
        retries=_int(table, "retries", where),
        addr_offset=_int(table, "addr_offset", where),
    )


def load_config(path: Path | None = None) -> Config:
    """Load the configuration; a missing file yields an empty :class:`Config`.

    Raises ``ValueError`` on malformed content (bad keys, wrong types), and
    lets ``tomllib``'s own error propagate for broken TOML syntax.
    """
    if path is None:
        path = default_config_path()
    if not path.is_file():
        return Config(path=path)
    data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    where = str(path)
    _validate_keys(data, _TOP_KEYS, where)
    raw_devices = data.get("devices", {})
    if not isinstance(raw_devices, dict):
        raise ValueError("'devices' must be a table of tables")
    devices = {}
    for name, table in raw_devices.items():
        if not isinstance(table, dict):
            raise ValueError(f"[devices.{name}] must be a table")
        devices[name] = _parse_device(name, table)
    return Config(
        path=path,
        port=_str(data, "port", where),
        baud=_int(data, "baud", where),
        id=_int(data, "id", where),
        timeout=_float(data, "timeout", where),
        retries=_int(data, "retries", where),
        addr_offset=_int(data, "addr_offset", where),
        devices=devices,
    )


__all__ = [
    "CONFIG_FILE_NAME",
    "EXAMPLE_CONFIG",
    "Config",
    "DeviceConfig",
    "default_config_dir",
    "default_config_path",
    "load_config",
]
