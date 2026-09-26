"""Read a unit over once and say what looks wrong.

Composition over what the other modules already read: one pass of the device
info, the runtime table and the settings table, and then everything that can
be concluded by holding two of those numbers up against each other.  Nothing
here talks to the bus itself, and nothing here writes.

A finding is one of three weights.  ``error`` is the unit telling us something
is wrong, or two of its own registers disagreeing; ``warning`` is a reading
outside the band this tool believes is healthy; ``note`` is a setting somebody
chose that is worth being reminded of -- a disabled charge switch is not a
fault, and a doctor that called it one would be ignored.

The checks are deliberately conservative.  A pack whose chemistry cannot be
told from its cell voltages gets no chemistry finding rather than a guess, and
a register the board does not map ends up in :attr:`Report.unavailable`, which
is a different sentence from "healthy".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from devicectl.clock import format_drift
from devicectl.doctor import ERROR, NOTE, SEVERITY_ORDER, WARNING, Finding, Report

from jkctl import identity as I, runtime as R, settings as S
from jkctl.device import Device
from jkctl.errors import JkError

# A cell spread wider than this is worth saying something about; JK's own
# balancer triggers well below it.
CELL_DELTA_WARN_V = 0.050

# Below this the pack has lost enough capacity to be worth a word.
SOH_WARN_PERCENT = 80

# A balance wire this many times the median is either a bad crimp or a bad
# measurement, and either is worth looking at.  Chosen wide: the readings sit
# in the tenths of a milliohm and the noise on them is not small.
WIRE_RES_OUTLIER_FACTOR = 3.0

# Below this many cells there is no meaningful median to compare against.
WIRE_RES_MIN_CELLS = 4

# How far the unit's clock may be from this host's before it is worth saying.
# The RTC is what timestamps the device's own stored records, so a clock an
# hour out makes its history harder to read than no history at all.
CLOCK_DRIFT_WARN_S = 300

# How far the measured full-charge capacity may fall below the configured
# design capacity before it looks like the pack, or the setting, is wrong.
CAPACITY_WARN_FRACTION = 0.8

# The cell voltage bands the three chemistries actually sit in, and the
# per-cell over-voltage setpoint each wants.  Used only to recognise a pack
# whose protection setpoints belong to a different chemistry from the cells
# in front of them -- an LFP bank running Li-ion setpoints is a pack being
# charged to 4.2 V a cell.
CHEMISTRIES = (
    # name, resting cell band, plausible over-voltage protection band
    ("LTO", (2.0, 2.7), (2.6, 3.0)),
    ("LiFePO4", (3.0, 3.5), (3.4, 3.8)),
    ("Li-ion", (3.55, 4.0), (4.0, 4.3)),
)


class DoctorError(JkError):
    """The unit could not be read at all."""


@dataclass
class Reading(Report):
    """A report, plus the readings its findings were drawn from.

    The shape -- the findings, the weights, what could not be read -- is
    :class:`devicectl.doctor.Report`'s.  What is added here is the pass
    itself, so a caller that wants the numbers behind a finding does not
    have to read the unit a second time.
    """

    identity: I.Identity | None = None
    reading: R.Runtime | None = None
    settings: dict[str, Any] = field(default_factory=dict)


def check(device: Device, *, now: datetime | None = None) -> Reading:
    """Read the unit and return everything one pass can conclude."""
    report = Reading()
    try:
        report.identity = I.read(device)
    except JkError as exc:
        report.findings.append(
            Finding(
                ERROR,
                "link",
                f"nothing answered at address {device.slave} ({exc})",
                "jkctl probe",
            )
        )
        return report
    if not report.identity.model:
        report.findings.append(
            Finding(
                ERROR,
                "link",
                f"nothing answered at address {device.slave}",
                "jkctl probe",
            )
        )
        return report

    try:
        report.reading = R.read(device)
    except JkError:
        report.unavailable.append("runtime: the unit did not answer the runtime table")
    try:
        report.settings = S.read(device)
    except JkError:
        report.unavailable.append(
            "settings: the unit did not answer the configuration table"
        )

    reading, values = report.reading, report.settings
    if reading is not None and not reading.fields:
        report.unavailable.append("runtime: the unit did not answer the runtime table")
        reading = None

    if reading is not None:
        _check_alarms(report, reading)
        _check_cells(report, reading)
        _check_sensors(report, reading)
        _check_wires(report, reading)
        _check_clock(report, device, reading, now)
    if values:
        _check_switches(report, values)
        _check_address(report, device, values)
    if reading is not None and values:
        _check_cell_count(report, reading, values)
        _check_capacity(report, reading, values)
        _check_chemistry(report, reading, values)
    return report


# --- the checks -------------------------------------------------------------------------


def _check_alarms(report: Report, reading: R.Runtime) -> None:
    """Report every protection bit the unit is currently raising."""
    for alarm in reading.alarms:
        report.findings.append(
            Finding(ERROR, "alarm", f"{alarm.name}: {alarm.state}", "jkctl alarms")
        )


def _check_cells(report: Report, reading: R.Runtime) -> None:
    """Check the cell spread and the state of health."""
    delta = reading.get("maxVoltDelta")
    if delta is not None and delta > CELL_DELTA_WARN_V:
        report.findings.append(
            Finding(
                WARNING,
                "cells",
                f"cell spread is {delta:.3f} V across {reading.cell_count} cells",
                "jkctl cells",
            )
        )
    soh = reading.get("sOCSOH")
    if soh is not None and 0 < int(soh) < SOH_WARN_PERCENT:
        report.findings.append(
            Finding(WARNING, "capacity", f"state of health is {int(soh)}%")
        )


def _check_sensors(report: Report, reading: R.Runtime) -> None:
    """Report a temperature probe the board counts but has no reading for.

    ``tempSensorAbsent`` is the unit's own answer to "which probes are
    connected", and a pack protected by a sensor that fell off is a pack with
    no over-temperature protection on that end of it.  What that word says is
    which probes are *there* -- the finding used to read it the other way up
    and told a healthy six-probe pack that all six of its probes were gone.
    A bit that is simply clear is a probe the model does not have, so the
    warning is for the contradiction instead: a probe still counted as
    connected that is reporting nothing.  The bits are named -- the MOS probe
    and five battery probes -- so the finding names the probe rather than
    numbering it, which numbered the MOS probe as "sensor 1".
    """
    silent = reading.sensors_silent
    if not silent:
        return
    report.findings.append(
        Finding(
            WARNING,
            "sensors",
            f"the unit counts {len(silent)} temperature sensor(s) as connected "
            "that report no temperature: " + ", ".join(silent),
            "check the probe wiring",
        )
    )


def _check_wires(report: Report, reading: R.Runtime) -> None:
    """Report a balance wire whose resistance is far off its neighbours'."""
    res = [r for r in reading.cell_resistances if r]
    if len(res) < WIRE_RES_MIN_CELLS:
        return
    ordered = sorted(res)
    median = ordered[len(ordered) // 2]
    if median <= 0:
        return
    for index, value in enumerate(reading.cell_resistances):
        if value and value > median * WIRE_RES_OUTLIER_FACTOR:
            report.findings.append(
                Finding(
                    WARNING,
                    "wiring",
                    f"cell {index + 1}'s balance wire reads {value:.3f} against a "
                    f"median of {median:.3f}",
                    "jkctl cells",
                )
            )


def _check_clock(
    report: Report, device: Device, reading: R.Runtime, now: datetime | None
) -> None:
    """Compare the unit's real-time clock with this host's."""
    when = I.rtc_to_datetime(reading.get("rtcCounter"))
    if when is None:
        return
    drift = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if abs(drift) > CLOCK_DRIFT_WARN_S:
        report.findings.append(
            Finding(
                NOTE,
                "clock",
                f"the unit's clock is {format_drift(drift)}",
                "jkctl time sync",
            )
        )


def _check_switches(report: Report, values: dict[str, Any]) -> None:
    """Note each of the three main switches that is off."""
    for name, key, command in (
        ("charging", "batChargeEn", "charge"),
        ("discharging", "batDischargeEn", "discharge"),
        ("balancing", "balanEn", "balance"),
    ):
        if values.get(key) == 0:
            report.findings.append(
                Finding(NOTE, "switches", f"{name} is disabled", f"jkctl {command} on")
            )


def _check_address(report: Report, device: Device, values: dict[str, Any]) -> None:
    """Compare the stored device address with the one that answered.

    ``devAddr`` is a writable setting and the DIP switches are physical, so
    the two can disagree -- and when they do, the next person to move a switch
    loses the unit.
    """
    stored = values.get("devAddr")
    if stored is None:
        return
    if int(stored) != device.slave:
        report.findings.append(
            Finding(
                NOTE,
                "address",
                f"it answered at address {device.slave} but holds devAddr "
                f"{int(stored)}",
                "jkctl address show",
            )
        )


def _check_cell_count(
    report: Report, reading: R.Runtime, values: dict[str, Any]
) -> None:
    """Compare the configured cell count with the cells actually present."""
    configured = values.get("cellCount")
    if configured is None or not reading.cell_count:
        return
    if int(configured) != reading.cell_count:
        report.findings.append(
            Finding(
                ERROR,
                "cells",
                f"configured for {int(configured)} cells but "
                f"{reading.cell_count} are present",
                "jkctl settings set cellCount=<n>",
            )
        )


def _check_capacity(report: Report, reading: R.Runtime, values: dict[str, Any]) -> None:
    """Check remaining against full, and full against the design capacity."""
    remain = reading.get("socCapabilityRemain")
    full = reading.get("socFullChargeCapacity")
    design = values.get("capBatCell")
    if remain is not None and full and remain > full * 1.02:
        report.findings.append(
            Finding(
                ERROR,
                "capacity",
                f"remaining capacity ({remain:.1f} Ah) is above the full charge "
                f"capacity ({full:.1f} Ah)",
                "jkctl doctor after a full charge",
            )
        )
    if full and design and full < design * CAPACITY_WARN_FRACTION:
        report.findings.append(
            Finding(
                WARNING,
                "capacity",
                f"full charge capacity is {full:.1f} Ah against a design capacity "
                f"of {design:.1f} Ah",
            )
        )


def _check_chemistry(
    report: Report, reading: R.Runtime, values: dict[str, Any]
) -> None:
    """Report protection setpoints belonging to a chemistry other than the cells'.

    Recognising the chemistry from a resting cell voltage is not something to
    be confident about, so this only fires when the cells sit squarely inside
    one band and the over-voltage setpoint sits squarely inside another's.
    """
    average = reading.get("cellVolAve")
    over = values.get("volCellOV")
    if average is None or over is None:
        return
    looks_like = [n for n, (lo, hi), _ in CHEMISTRIES if lo <= average <= hi]
    set_for = [n for n, _, (lo, hi) in CHEMISTRIES if lo <= over <= hi]
    if len(looks_like) != 1 or len(set_for) != 1 or looks_like == set_for:
        return
    report.findings.append(
        Finding(
            WARNING,
            "setpoints",
            f"cells rest at {average:.3f} V, which looks like {looks_like[0]}, "
            f"but the over-voltage protection is {over:.3f} V, which is "
            f"{set_for[0]}",
            f"jkctl preset {looks_like[0].lower()}",
        )
    )


__all__ = [
    "CELL_DELTA_WARN_V",
    "ERROR",
    "NOTE",
    "SEVERITY_ORDER",
    "SOH_WARN_PERCENT",
    "WARNING",
    "DoctorError",
    "Finding",
    "Report",
    "check",
]
