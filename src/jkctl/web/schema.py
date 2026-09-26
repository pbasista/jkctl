"""How the backend's values reach the browser.

One place for the JSON the UI consumes, kept apart from the CLI's own
``--json`` output: that one is a user-facing format we should not break, this
one is an internal contract between two halves of the same program.  Keys are
camelCase because they are read as JavaScript.

A register the board did not answer for stays in the document as ``null``
rather than being dropped, so the page can draw a steady row of fields with an
em dash in it instead of changing shape between polls.

Every register carries its own description with it -- unit, decimals, range,
factory default, enum labels, R/W -- because the browser has no catalog of its
own and should not grow one: the datasource is the single description of what
a field is, and shipping it as part of each field's JSON is what keeps the two
front ends saying the same thing about the same register.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from devicectl.doctor import finding_json

from jkctl import (
    doctor as D,
    history as History,
    identity as I,
    logcodes,
    runtime as R,
    settings as S,
    values as V,
)
from jkctl.firmware import Candidate, Check, Firmware
from jkctl.registers import Register
from jkctl.web.session import Unit

# How long a rendered value may be before the browser is given an
# abbreviation instead.  An array field renders as all of its elements in one
# line -- 32 cell voltages is over 200 characters -- and in a table whose
# value column does not wrap, one of those sets the width of the column for
# every row.  The full value is still in ``value``, so nothing is lost.
TEXT_MAX = 48


def register_json(
    reg: Register,
    value: Any = None,
    *,
    answered: bool = True,
    refused: str | None = None,
) -> dict:
    """Describe one register, and what it currently reads.

    ``refused`` is the sentence a board gave for not taking a write to it at
    all (Device.unwritable): the page then shows the value and says why, in
    place of an editor whose every change would be turned down.
    """
    text = V.format_value(reg, value) if answered else None
    return {
        "name": reg.key,
        "table": reg.table,
        # The page's name for it, the same on every card (jkctl.names), and
        # JK's own beside it for the tooltip that says where to find it.
        "label": reg.title,
        "vendor": reg.label,
        "description": reg.description,
        "value": value,
        "text": _short(text),
        "long": bool(text and len(text) > TEXT_MAX),
        "unit": reg.unit or None,
        "decimals": reg.decimals,
        "minimum": reg.minimum,
        "maximum": reg.maximum,
        "default": reg.default,
        "access": reg.access,
        "writable": reg.writable,
        "kind": reg.kind,
        "count": reg.field.count if reg.is_array else None,
        "options": _options(reg),
        "answered": answered,
        "refused": refused,
    }


def _short(text: str | None) -> str | None:
    """Abbreviate a rendered value that is too long for a table cell."""
    if text is None or len(text) <= TEXT_MAX:
        return text
    return text[: TEXT_MAX - 1] + "\u2026"


def _options(reg: Register) -> Any:
    """Return the datasource's own labels: enum values, or a bitmap's bits."""
    if not reg.options:
        return None
    if reg.is_bitmap:
        return [
            {"bit": bit, "name": name, "on": on, "state": state}
            for bit, name, on, state in V.decode_bits(reg, 0)
        ]
    return [
        {"value": int(key), "label": text.split(":")[0].strip()}
        for key, text in sorted(reg.options.items(), key=lambda kv: int(kv[0]))
        if key.lstrip("-").isdigit()
    ]


def registers_json(
    regs: list[Register],
    values: dict[str, Any],
    refused: dict[str, str] | None = None,
) -> list[dict]:
    """Describe a list of registers with the values one read brought back."""
    refused = refused or {}
    return [
        register_json(
            reg,
            values.get(reg.key),
            answered=reg.key in values,
            refused=refused.get(reg.key),
        )
        for reg in regs
    ]


def unit_json(unit: Unit) -> dict:
    """One board as the header's picker knows it."""
    return unit.json()


def identity_json(ident: I.Identity) -> dict:
    """Render a unit's nameplate."""
    return {
        "model": ident.model,
        "hardwareVersion": ident.hardware_version,
        "softwareVersion": ident.software_version,
        "serialNumber": ident.serial_number,
        "manufactureDate": ident.first_power_on,
        "maxCells": ident.max_cells,
        "powerOnTimes": ident.power_on_times,
        "totalRunTimeS": ident.total_run_time_s,
        "totalRunTime": ident.total_run_time,
        "protocolVersion": ident.protocol_version,
        "ports": [
            {"port": label, "number": number, "protocol": name}
            for label, number, name in ident.ports
        ],
    }


# What the dashboard's own cards read, beyond the raw field dictionary.  Named
# here rather than in the browser so the two front ends agree about which
# reading is "the pack voltage".
SUMMARY_KEYS = (
    "batVol",
    "batCurrent",
    "batWatt",
    "socRelativeStateOfCharge",
    "sOCSOH",
    "socCapabilityRemain",
    "socFullChargeCapacity",
    "socCycleCount",
    "socCycleCapacity",
    "cellVolAve",
    "maxVoltDelta",
    "celMaxVol",
    "celMinVol",
    "equCurrent",
    "equStatus",
    "chargeStatus",
    "dischargeStatus",
    "heatingStatus",
    "chargePlugged",
    "tempMos",
    "batTemp1",
    "batTemp2",
    "batTemp3",
    "batTemp4",
    "batTemp5",
    "runtime",
    "rtcCounter",
    "tempSensorAbsent",
)


def runtime_json(reading: R.Runtime) -> dict:
    """One reading of the pack: the summary, the cells, the alarms."""
    catalog = reading.catalog
    fields: dict[str, Any] = {}
    for key in SUMMARY_KEYS:
        reg = catalog.get(key, "02")
        if reg is None or key not in reading.fields:
            fields[key] = None
            continue
        value = reading.fields[key]
        fields[key] = {
            "value": value,
            "text": V.format_value(reg, value),
            "label": reg.label,
            "unit": reg.unit or None,
        }
    volts = reading.cell_voltages
    res = reading.cell_resistances
    balance = reading.balance
    roles = (
        {balance["from"] - 1: "giving", balance["to"] - 1: "taking"} if balance else {}
    )
    return {
        "fields": fields,
        "cells": [
            {
                "cell": index + 1,
                "voltage": volt,
                "resistance": res[index] if index < len(res) else None,
                "balancing": index in roles,
                "balance": roles.get(index),
            }
            for index, volt in enumerate(volts)
        ],
        "balance": balance,
        "cellCount": reading.cell_count,
        "sensorsPresent": reading.sensors_present,
        "sensorsSilent": reading.sensors_silent,
        "alarms": [
            {"register": a.register, "bit": a.bit, "name": a.name, "state": a.state}
            for a in reading.alarms
        ],
        "read": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def bank_row_json(unit: Unit, reading: R.Runtime | None, error: str = "") -> dict:
    """One tile of the bank view: enough to judge a pack without opening it.

    It carries the whole nameplate -- the serial number and the hardware
    version as well as the model -- because the header's picker is drawn
    from these rows as well as from the link event's units, and a board is
    named by its serial number in both places.  A row that answered with
    none of it is a board that was scanned before its nameplate was read,
    not a board without one.
    """
    if reading is None:
        return {
            "id": unit.slave,
            "model": unit.model,
            "version": unit.version,
            "serial": unit.serial,
            "hardware": unit.hardware,
            "error": error,
            "ok": False,
        }
    volts = reading.cell_voltages
    return {
        "id": unit.slave,
        "model": unit.model,
        "version": unit.version,
        "serial": unit.serial,
        "hardware": unit.hardware,
        "ok": True,
        "error": "",
        "voltage": reading.get("batVol"),
        "current": reading.get("batCurrent"),
        "power": reading.get("batWatt"),
        "soc": reading.get("socRelativeStateOfCharge"),
        "soh": reading.get("sOCSOH"),
        "remaining": reading.get("socCapabilityRemain"),
        "full": reading.get("socFullChargeCapacity"),
        "cellCount": reading.cell_count,
        "cellHigh": max(volts) if volts else None,
        "cellLow": min(volts) if volts else None,
        "delta": reading.get("maxVoltDelta"),
        "temperature": _hottest(reading),
        "charging": bool(reading.get("chargeStatus")),
        "discharging": bool(reading.get("dischargeStatus")),
        "balancing": bool(reading.get("equStatus")),
        "heating": bool(reading.get("heatingStatus")),
        "alarms": [a.name for a in reading.alarms],
    }


def _hottest(reading: R.Runtime) -> float | None:
    """Return the warmest sensor the board answered for, MOS included."""
    seen = [
        reading.get(key)
        for key in (
            "tempMos",
            "batTemp1",
            "batTemp2",
            "batTemp3",
            "batTemp4",
            "batTemp5",
        )
    ]
    real = [t for t in seen if isinstance(t, (int, float))]
    return max(real) if real else None


def change_json(change: S.Change) -> dict:
    """One planned write, as the Apply dialog shows it."""
    show = V.format_value if change.index is None else V.format_element
    return {
        "name": change.name,
        "register": change.register.key,
        "index": change.index,
        "label": change.register.title,
        "old": change.old,
        "new": change.new,
        "oldText": show(change.register, change.old),
        "newText": show(change.register, change.new),
    }


def record_json(record: History.Record) -> dict:
    """One stored fault record: what happened, and the pack at that moment."""
    return {
        "index": record.index,
        "code": record.code,
        "name": record.name,
        "known": logcodes.known(record.code),
        "when": record.when.isoformat() if record.when else None,
        "closed": record.closed,
        "packV": record.pack_v,
        "packA": record.pack_a,
        "cellMaxV": record.cell_max_v,
        "cellMinV": record.cell_min_v,
        "cellMaxNo": record.max_cell_no,
        "cellMinNo": record.min_cell_no,
        "remainingAh": record.remaining_ah,
        "fullAh": record.full_ah,
        "maxTempC": record.max_temp_c,
        "minTempC": record.min_temp_c,
        "mosTempC": record.mos_temp_c,
        "heatA": record.heat_a,
    }


def report_json(report: D.Report) -> dict:
    """Render the doctor's whole pass."""
    return {
        "findings": [finding_json(f) for f in report.sorted()],
        "unavailable": report.unavailable,
        "ok": report.ok,
        "ran": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def firmware_json(fw: Firmware) -> dict:
    """Describe what a .jkbms file contains."""
    from jkctl import upgrade as U

    return {
        "path": fw.path,
        "model": fw.model,
        "version": fw.version,
        "major": fw.major,
        "minor": fw.minor,
        "deviceCode": fw.device_code,
        "built": f"{fw.build_date} {fw.build_time}".strip(),
        "imageBytes": len(fw.image),
        "blocks": U.block_count(fw.image),
        "timeLimited": fw.valid_hours > 0,
        "validHours": fw.valid_hours,
        "sp": f"0x{fw.sp:08x}",
        "reset": f"0x{fw.reset:08x}",
    }


def check_json(check: Check) -> dict:
    """One step of the compatibility gate."""
    return {
        "name": check.name,
        "ok": check.ok,
        "waived": check.waived,
        "blocking": check.blocking,
        "detail": check.detail,
        "kind": check.kind,
    }


def candidate_json(item: Candidate, checks: list[Check] | None = None) -> dict:
    """One file in the firmware library, with how it would be judged."""
    doc: dict[str, Any] = {"path": item.path, "error": item.error}
    if item.firmware is not None:
        doc.update(firmware_json(item.firmware))
    if checks is not None:
        doc["checks"] = [check_json(c) for c in checks]
        doc["compatible"] = not any(c.blocking for c in checks)
    return doc


__all__ = [
    "SUMMARY_KEYS",
    "bank_row_json",
    "candidate_json",
    "change_json",
    "check_json",
    "finding_json",
    "firmware_json",
    "identity_json",
    "record_json",
    "register_json",
    "registers_json",
    "report_json",
    "runtime_json",
    "unit_json",
]
