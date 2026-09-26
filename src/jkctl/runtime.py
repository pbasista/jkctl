"""What the pack is doing right now: cells, current, temperatures, alarms.

The alarm names are not written out here.  The datasource carries a
description for every bit of every bitmap -- ``"11:Protection - Cell under
voltage:Normal;Abnormal"`` -- so :func:`jkctl.values.decode_bits` reads them
straight out of the vendor's own table, and a firmware that adds a bit adds it
here for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jkctl import values as V
from jkctl.device import Device
from jkctl.identity import format_duration
from jkctl.registers import RUNTIME, Catalog

# The bitmap registers worth reporting as named state rather than a number.
ALARM_FIELDS = ("sysAlarm", "userAlarm2")

# Which reading each of the six temperature-probe bits is about, in bit order:
# bit 0 is the MOS probe and bits 1..5 the five battery probes.
TEMPERATURE_FIELDS = (
    "tempMos",
    "batTemp1",
    "batTemp2",
    "batTemp3",
    "batTemp4",
    "batTemp5",
)

# The one-screen summary, in the web dashboard's cards and under their names
# for each reading, so `jkctl status` and the page call a reading the same.
# The names are short because the heading says the rest: "Average" is under
# Cells.  The last group is not on the page; its names say which switch.
SUMMARY: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Pack",
        (
            ("socRelativeStateOfCharge", "State of charge"),
            ("batVol", "Voltage"),
            ("batCurrent", "Current"),
            ("batWatt", "Power"),
            ("socCapabilityRemain", "Remaining"),
            ("socFullChargeCapacity", "Full charge"),
            ("sOCSOH", "State of health"),
            ("socCycleCount", "Cycles"),
            ("socCycleCapacity", "Cycle capacity"),
            ("runtime", "Running for"),
        ),
    ),
    (
        "Cells",
        (
            ("cellVolAve", "Average"),
            ("maxVoltDelta", "Spread"),
            ("equCurrent", "Balance current"),
            ("equStatus", "Balancing"),
            ("celMaxVol", "Highest cell"),
            ("celMinVol", "Lowest cell"),
        ),
    ),
    (
        "Temperatures",
        (
            ("tempMos", "MOS"),
            ("batTemp1", "Battery 1"),
            ("batTemp2", "Battery 2"),
            ("batTemp3", "Battery 3"),
            ("batTemp4", "Battery 4"),
            ("batTemp5", "Battery 5"),
        ),
    ),
    (
        "State",
        (
            ("chargeStatus", "Charge MOSFET"),
            ("dischargeStatus", "Discharge MOSFET"),
            ("heatingStatus", "Heater"),
            ("chargePlugged", "Charger"),
        ),
    ),
)

# The same fields, flat: what `status --json` carries.
SUMMARY_FIELDS = tuple(pair for _, rows in SUMMARY for pair in rows)

# The runtime table counts cells from zero; people, and the page, from one.
CELL_INDEX_FIELDS = ("celMaxVol", "celMinVol")


@dataclass(frozen=True)
class Alarm:
    """One raised bit of one alarm word."""

    register: str
    bit: int
    name: str
    state: str


@dataclass
class Runtime:
    """One reading of the runtime table."""

    fields: dict[str, Any] = field(default_factory=dict)
    catalog: Catalog = field(default_factory=Catalog)

    def get(self, key: str) -> Any:
        """Return one decoded field, or None if the device did not answer for it."""
        return self.fields.get(key)

    @property
    def cell_count(self) -> int:
        """How many cells the device reports as present, from the cell-status bits."""
        status = self.fields.get("cellStatus")
        if status is None:
            return len([v for v in self.cell_voltages if v])
        return bin(int(status)).count("1")

    @property
    def cell_voltages(self) -> list[float]:
        """Per-cell voltages, trimmed to the cells the device says are present."""
        volts = list(self.fields.get("cellVol") or [])
        status = self.fields.get("cellStatus")
        if status is None:
            return [v for v in volts if v]
        return [v for i, v in enumerate(volts) if int(status) >> i & 1]

    @property
    def cell_resistances(self) -> list[float]:
        """Per-cell balance-wire resistances, over the same cells."""
        res = list(self.fields.get("cellWireRes") or [])
        status = self.fields.get("cellStatus")
        if status is None:
            return [r for r in res if r]
        return [r for i, r in enumerate(res) if int(status) >> i & 1]

    def _sensor_bits(self) -> list[tuple[int, str, bool, str]]:
        """Return the six temperature-probe bits, or nothing if the unit did not answer."""
        reg = self.catalog.get("tempSensorAbsent", RUNTIME)
        if reg is None:
            return []
        return V.decode_bits(reg, self.fields.get("tempSensorAbsent"))

    @property
    def sensors_present(self) -> list[str] | None:
        """The temperature probes this unit says are connected, by name.

        The word is a bag of six named bits -- the MOS probe and five battery
        probes -- and naming them is the whole point: "sensor 1" is the MOS
        probe, not the first battery probe, and numbering them from one is how
        a probe gets looked for on the wrong wire.

        A raised bit is a probe that is *there*.  JK's two names for the word
        disagreed -- the machine name ``tempSensorAbsent`` against the Chinese
        label 传感器存在标志, "sensor present flag" -- and this read it as the
        machine name did, which put a warning about six missing probes under a
        card listing six probes and their temperatures.  The label is right:
        the datasource's factory default for the word is 255, every bit
        raised, which is a sane default for "show them all" and nonsense for
        "none of them is connected", and a unit with all six probes wired
        reports all six bits raised.  See docs/reference.md.

        ``None`` when the unit did not answer for the word at all, which is
        not the same answer as "this unit has no probes".
        """
        rows = self._sensor_bits()
        if not rows:
            return None
        return [name for _bit, name, is_set, _state in rows if is_set]

    @property
    def sensors_silent(self) -> list[str]:
        """Probes the unit lists as connected that it publishes no reading for.

        This is the fault worth reporting.  A bit that is simply clear is a
        probe this model does not have -- most packs wire two of the five --
        and warning about those is warning about every unit ever made; a probe
        the unit still counts as connected and has no temperature for is one
        that has come off its wire.
        """
        return [
            name
            for bit, name, is_set, _state in self._sensor_bits()
            if is_set
            and bit < len(TEMPERATURE_FIELDS)
            and self.get(TEMPERATURE_FIELDS[bit]) is None
        ]

    @property
    def alarms(self) -> list[Alarm]:
        """Every alarm bit currently raised, named as the vendor names it."""
        raised = []
        for key in ALARM_FIELDS:
            reg = self.catalog.get(key, RUNTIME)
            if reg is None:
                continue
            for bit, name, is_set, state in V.decode_bits(reg, self.fields.get(key)):
                if is_set:
                    raised.append(Alarm(key, bit, name, state))
        return raised

    @property
    def balance(self) -> dict[str, Any] | None:
        """Say what the balancer is doing: from which cell, to which, how hard.

        ``equStatus`` says whether balancing is happening at all; the
        datasource has no per-cell balancing bitmap (``cellStatus`` is which
        cells are *wired*).  A JK board's balancer is an active one, which
        moves charge from the highest cell to the lowest -- the two the
        runtime table names by index (``celMaxVol``, ``celMinVol``, counted
        from zero) -- so those are the two named here, and only while the
        balancer is running.  Cells are counted from one, as people count
        them.  None when it is not balancing.
        """
        if not self.get("equStatus"):
            return None
        high, low = self.get("celMaxVol"), self.get("celMinVol")
        cells = len(self.cell_voltages)
        if high is None or low is None or high == low:
            return None
        if not (0 <= int(high) < cells and 0 <= int(low) < cells):
            return None
        return {
            "from": int(high) + 1,
            "to": int(low) + 1,
            "current": self.get("equCurrent"),
        }

    def summary(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """Return the summary as ``(heading, [(name, text)])``, as the page groups it.

        Only what the device answered is included.  A temperature probe the
        unit says is not connected is left out, as the page leaves it out;
        cells are counted from one; the run time reads as days and hours.
        """
        present = self.sensors_present
        balance = self.balance
        sections = []
        for heading, fields in SUMMARY:
            rows = []
            for key, name in fields:
                if key not in self.fields:
                    continue
                if heading == "Temperatures" and (
                    name not in present
                    if present is not None
                    else self.get(key) is None
                ):
                    continue
                rows.append((name, self._text(key, balance)))
            sections.append((heading, rows))
        return sections

    def _text(self, key: str, balance: dict[str, Any] | None) -> str:
        """Render one summary field the way the page does."""
        value = self.fields[key]
        if value is None:
            return "-"
        if key in CELL_INDEX_FIELDS:
            return f"cell {int(value) + 1}"
        if key == "runtime":
            return format_duration(int(value))
        if key == "equStatus" and balance:
            return f"cell {balance['from']} → cell {balance['to']}"
        reg = self.catalog.get(key, RUNTIME)
        return V.format_value(reg, value) if reg else str(value)


def read(device: Device) -> Runtime:
    """Read the whole runtime table from a device."""
    return Runtime(fields=device.snapshot(RUNTIME), catalog=device.catalog)


__all__ = ["ALARM_FIELDS", "SUMMARY", "SUMMARY_FIELDS", "Alarm", "Runtime", "read"]
