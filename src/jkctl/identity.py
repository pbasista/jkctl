"""What a unit *is*: model, versions, serial number, and its port protocols.

The device-info table also holds the UART and CAN protocol selectors, whose
values are indices into the vendor's own protocol lists.  Reporting
``uart1ProtoNo: 1`` is useless; reporting ``001 - JK BMS RS485 Modbus V1.0`` is
what the vendor's application shows, so the lists are shipped alongside and
resolved here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from devicectl.progress import (
    SECONDS_PER_DAY,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
)

from jkctl.device import Device
from jkctl.registers import protocol_names

# The BMS counts its RTC in seconds from midnight on 1 January 2020 -- in
# the local time of whatever computer set it.  See `rtc_epoch`.


# The manufacture date is stored as six ASCII digits, YYMMDD.
DATE_DIGITS = 6

# Which selector register feeds which of the shipped protocol lists.
PORT_PROTOCOLS = (
    ("uart1ProtoNo", "uart", "UART1"),
    ("uart2ProtoNo", "uart", "UART2"),
    ("uart3ProtoNo", "uart", "UART3"),
    ("uart4ProtoNo", "uart", "UART4"),
    ("canProtoNo", "can", "CAN"),
)


@dataclass(frozen=True)
class Identity:
    """One unit's nameplate."""

    model: str
    hardware_version: str
    software_version: str
    serial_number: str
    manufacture_date: str
    max_cells: int | None
    power_on_times: int | None
    total_run_time_s: int | None
    protocol_version: int | None
    ports: tuple[tuple[str, int | None, str], ...]  # label, number, name

    @property
    def first_power_on(self) -> str:
        """The manufacture date as ISO-8601, or the raw string if it is not YYMMDD."""
        raw = self.manufacture_date
        if len(raw) == DATE_DIGITS and raw.isdigit():
            return f"20{raw[0:2]}-{raw[2:4]}-{raw[4:6]}"
        return raw

    @property
    def total_run_time(self) -> str:
        """Total powered-on time, rendered in days and hours."""
        return format_duration(self.total_run_time_s)


def read(device: Device) -> Identity:
    """Read a unit's nameplate."""
    return from_fields(device.identity)


def from_fields(fields: dict[str, Any]) -> Identity:
    """Build an :class:`Identity` from an already-read device-info snapshot."""
    names = protocol_names()
    ports = []
    for key, kind, label in PORT_PROTOCOLS:
        number = fields.get(key)
        if number is None:
            continue
        ports.append(
            (label, int(number), names.get(kind, {}).get(int(number), "unknown"))
        )
    return Identity(
        model=fields.get("manuDeviceID") or "",
        hardware_version=fields.get("hardwareVersion") or "",
        software_version=fields.get("softwareVersion") or "",
        serial_number=fields.get("deviceSN") or "",
        manufacture_date=fields.get("manufactureDate") or "",
        max_cells=_int_or_none(fields.get("maxCells")),
        power_on_times=_int_or_none(fields.get("pwrOnTimes")),
        total_run_time_s=_int_or_none(fields.get("oddRunTime")),
        protocol_version=_int_or_none(fields.get("protocolVer")),
        ports=tuple(ports),
    )


def rtc_epoch() -> datetime:
    """Return the instant the board's clock counts from, on this computer.

    The board keeps a count of seconds and no time zone.  What the count is
    from is decided by whoever sets it, and JK BMS Monitor builds its origin
    as ``QDateTime(QDate(2020, 1, 1), QTime(0, 0))`` -- the default time spec,
    ``Qt::LocalTime`` -- and subtracts that from the current epoch time.  So
    the count starts at local midnight, in the offset that zone had on that
    winter's night, and the application reads it back the same way.

    jkctl used to count from midnight UTC, which on any computer not set to
    UTC is an hour or two away from what the vendor's application writes: a
    board set from one read as that far out in the other.  This is the
    vendor's origin, so the two agree -- and the board's time is local time,
    because that is what the origin makes it.
    """
    return datetime(2020, 1, 1).astimezone()


def rtc_to_datetime(ticks: int | None) -> datetime | None:
    """Turn the device's RTC counter into an instant, in this computer's zone."""
    if ticks is None:
        return None
    return (rtc_epoch() + timedelta(seconds=int(ticks))).astimezone()


def datetime_to_rtc(when: datetime) -> int:
    """Turn a timestamp into the counter value the RTC-sync action takes."""
    if when.tzinfo is None:
        when = when.astimezone()
    return int((when - rtc_epoch()).total_seconds())


def format_duration(seconds: int | None) -> str:
    """Render a run-time counter as ``12d 04h 33m``."""
    if seconds is None:
        return "-"
    total = int(seconds)
    days, rest = divmod(total, SECONDS_PER_DAY)
    hours, rest = divmod(rest, SECONDS_PER_HOUR)
    minutes = rest // SECONDS_PER_MINUTE
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    return f"{hours:02d}h {minutes:02d}m"


def _int_or_none(value: Any) -> int | None:
    """Coerce a decoded field to int, tolerating a field the device omitted."""
    return None if value is None else int(value)


__all__ = [
    "PORT_PROTOCOLS",
    "Identity",
    "datetime_to_rtc",
    "format_duration",
    "from_fields",
    "read",
    "rtc_epoch",
    "rtc_to_datetime",
]
