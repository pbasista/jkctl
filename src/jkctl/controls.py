"""The write-only action registers, and the switches that behave like them.

The action space at ``base + 0x600`` is the one part of the register map with
no read side: a slot is written and something happens.  Three of them replace
every protection setpoint at once, one powers the board off, and two
recalibrate a measurement -- so each carries the sentence a confirmation
prompt reads out (:data:`jkctl.registers.ACTIONS`).

None of this has been exercised on hardware.  The addresses come from JK's own
register-map document, which lists them as write-only 16- and 32-bit values,
and the arming write for the (undocumented) upgrade slot is the one of these
that has been seen on the wire.
"""

from __future__ import annotations

from datetime import datetime

from jkctl import identity, settings
from jkctl.device import Device
from jkctl.errors import JkError
from jkctl.registers import ACTIONS, Action

# The chemistry presets, whose names are also their command-line words.
PRESETS = ("lifepo4", "li-ion", "lto")

# What each preset sets, as settings: JK's own table, "Default parameters of
# One-key Lithium Iron, One-key Ternary and One-key Lithium Titanate", the
# appendix of the BD-series manual (research/, page 26), row by row.  It is
# the only list of what a one-key preset writes that JK has published; the
# action slot leaves the choice to the firmware and cannot be asked what it
# chose.  So the page and ``jkctl preset`` write these as ordinary settings:
# the plan shows every one before it goes out, and a board refusing one says
# which.  The slot itself is still there, as ``jkctl preset --one-key``.
#
# Rows 16/17 are JK's too, and are the same for all three chemistries:
# charging is stopped at -20 degC and resumed at -10 degC, far colder than a
# LiFePO4 maker lets its cells be charged.  They are kept as published --
# this is JK's preset, not a recommendation -- and the plan shows them.
PRESET_VALUES: dict[str, dict[str, float]] = {
    name: {
        "volCellUV": uv,  # 1 cell undervoltage protection
        "volCellUVPR": uvpr,  # 2 ... recovery
        "volCellOV": ov,  # 3 cell overcharge voltage
        "volCellOVPR": ovpr,  # 4 ... recovery
        "volBalanTrig": 0.01,  # 5 trigger equalizing pressure difference
        "volSysPwrOff": off,  # 6 automatic shutdown voltage
        "timBatCOCPDly": 30,  # 7 charge overcurrent protection delay
        "timBatCOCPRDly": 60,  # 8 ... release time
        "timBatDcOCPDly": 30,  # 9 discharge overcurrent protection delay
        "timBatDcOCPRDly": 60,  # 10 ... release time
        "timBatSCPRDly": 60,  # 11 short-circuit protection release time
        "tmpBatCOT": 60,  # 12 charging over-temperature protection
        "tmpBatCOTPR": 55,  # 13 ... recovery
        "tmpBatDcOT": 60,  # 14 discharge over-temperature protection
        "tmpBatDcOTPR": 55,  # 15 ... recovery
        "tmpBatCUT": -20,  # 16 charging low-temperature protection
        "tmpBatCUTPR": -10,  # 17 ... recovery
        "tmpMosOT": 75,  # 18 MOS over-temperature protection
        "tmpMosOTPR": 70,  # 19 ... recovery
    }
    for name, (uv, uvpr, ov, ovpr, off) in {
        "li-ion": (2.9, 3.2, 4.2, 4.1, 2.8),
        "lifepo4": (2.6, 3.0, 3.6, 3.4, 2.5),
        "lto": (1.8, 2.0, 2.7, 2.4, 1.7),
    }.items()
}

# What the vendor writes to a one-key preset or emergency slot.  The register
# map gives these as a written value with no stated meaning; the app writes a
# plain 1 to trigger, which is what the slot's presence encodes.
TRIGGER = 1


class ControlError(JkError):
    """An action the device refused, or one that cannot be built."""


def describe(name: str) -> Action:
    """Return the action's entry, or say which names exist."""
    try:
        return ACTIONS[name]
    except KeyError:
        raise ControlError(
            f"no action named {name!r}; known: {', '.join(sorted(ACTIONS))}"
        ) from None


def preset_values(name: str) -> dict[str, float]:
    """Return the settings one preset sets (:data:`PRESET_VALUES`)."""
    if name not in PRESETS:
        raise ControlError(f"no preset named {name!r}; known: {', '.join(PRESETS)}")
    return dict(PRESET_VALUES[name])


def preset(device: Device, name: str) -> None:
    """Fire a one-key preset slot: the firmware picks the values, unseen."""
    preset_values(name)
    device.action(name, TRIGGER)


def emergency(device: Device) -> None:
    """Fire the emergency-start action."""
    device.action("emergency", TRIGGER)


def shutdown(device: Device) -> None:
    """Power the protection board down; it stops answering on the bus."""
    device.action("shutdown", TRIGGER)


# The three the vendor's register-map document does not list, recovered from
# its application's own buttons instead (findings §36).  Each is a plain write
# to one action slot, exactly like the documented ones beside it.
BOARD_ACTIONS = ("restart", "factory-restore", "erase-data")


def board_action(device: Device, name: str) -> None:
    """Fire one of the board actions the vendor document does not list.

    ``restart`` restarts the protection board; ``factory-restore`` returns
    every setting to the factory configuration; ``erase-data`` erases what the
    board has stored.  None of the three has been fired at real hardware, so
    each carries the warning its :class:`~jkctl.registers.Action` names and
    every caller asks first.
    """
    if name not in BOARD_ACTIONS:
        raise ControlError(
            f"no board action named {name!r}; known: {', '.join(BOARD_ACTIONS)}"
        )
    device.action(name, TRIGGER)


def sync_clock(device: Device, when: datetime | None = None) -> datetime:
    """Set the device's real-time clock, returning the timestamp written."""
    when = when or datetime.now().astimezone()
    device.action("time-calibration", identity.datetime_to_rtc(when))
    return when


def read_clock(device: Device) -> datetime | None:
    """Read the device's real-time clock, or None if this board has no RTC field."""
    return identity.rtc_to_datetime(
        device.read_many(["rtcCounter"], "02").get("rtcCounter")
    )


def calibrate_voltage(device: Device, millivolts: int) -> None:
    """Tell the BMS what the pack voltage really is, in millivolts."""
    device.action("voltage-calibration", int(millivolts))


def calibrate_current(device: Device, milliamps: int) -> None:
    """Tell the BMS what the pack current really is, in milliamps."""
    device.action("current-calibration", int(milliamps))


def toggle(device: Device, what: str, on: bool) -> None:
    """Turn charging, discharging or balancing on or off.

    These are settings registers rather than action slots -- ``batChargeEn``
    and friends in the 0x1000 block -- so they read back, unlike everything
    else in this module.
    """
    try:
        key = settings.TOGGLES[what]
    except KeyError:
        raise ControlError(
            f"no switch named {what!r}; known: {', '.join(sorted(settings.TOGGLES))}"
        ) from None
    device.write(key, 1 if on else 0, settings.SETTINGS)


def toggle_state(device: Device) -> dict[str, bool | None]:
    """Read the charge/discharge/balance switches."""
    values = device.read_many(list(settings.TOGGLES.values()), settings.SETTINGS)
    return {
        name: (None if values.get(key) is None else bool(values[key]))
        for name, key in settings.TOGGLES.items()
    }


__all__ = [
    "BOARD_ACTIONS",
    "PRESETS",
    "PRESET_VALUES",
    "TRIGGER",
    "ControlError",
    "board_action",
    "calibrate_current",
    "calibrate_voltage",
    "describe",
    "emergency",
    "preset",
    "preset_values",
    "read_clock",
    "shutdown",
    "sync_clock",
    "toggle",
    "toggle_state",
]
