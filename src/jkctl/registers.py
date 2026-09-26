"""The register catalog: which field lives where, and whether it may be written.

:mod:`jkctl.protocol` says what a field *is* -- its offset inside a frame
payload, its type, scale, unit, bounds and enum labels -- because the vendor's
own datasource says so.  It does not say whether the BMS will let you write it.
That comes from JK's RS485 Modbus register-map document
(``research/windows/docs/JK-BMS-RS485-Modbus-V1.1.txt``), whose R/W column is
transcribed into :data:`ACCESS` below.

Joining the two gives a :class:`Register`: a named, typed, bounded, addressable
thing that a command can read or write without knowing any offsets.  That is
:class:`devicectl.fields.FieldSpec` -- one description of one setting, for the
terminal, the browser and the writer at once -- and a :class:`Register` is one,
with the two things a Modbus field has that a setting in general does not: the
table it belongs to, and the datasource item it was read out of, which is what
knows its scale and its element type when a value has to be encoded.

The shared description was modelled on this class, and until now this was the
one program not using it, which meant the abstraction was proved against one
consumer rather than two.  What it took to bring this one over was three fields
(``decimals``, ``default``, and options keyed by the vendor's own decimal
strings) and two more kinds -- :data:`BITS` and :data:`ARRAY`, which are what a
protocol recovered from a datasource has and a hand-written settings table does
not.

Addressing, established on the live device (findings §28):

    register = frame_addr_offset + TABLE_BASE[table] + payload_byte_offset

so the vendor document's "address" column doubles as the register offset -- no
halving.  A read of C registers returns 2C bytes.

The action space at ``base + 0x600`` is different in kind: those offsets are
slot numbers, not byte offsets, and every one of them is write-only.  They live
in :data:`ACTIONS`.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from devicectl.fields import ENUM, FLAG, NUMBER, READ_ONLY, READ_WRITE, TEXT, FieldSpec

from jkctl import names as N, protocol as P

# The three tables the Modbus interface maps, in the order a person reads them.
SETTINGS = "01"
RUNTIME = "02"
INFO = "03"
TABLES = (INFO, RUNTIME, SETTINGS)

TABLE_LABELS = {
    INFO: "device info",
    RUNTIME: "runtime data",
    SETTINGS: "settings",
}

# The two kinds a datasource-described field has that the shared five do not.
# Both are one register holding many things: a bitmap is a bag of named bits,
# an array is the same measurement repeated once per cell.
BITS = "bits"
ARRAY = "array"


# Every field JK's document marks RW.  A field absent from this table is
# read-only: a register that reconfigures a battery is not something to guess
# at from a name that merely sounds settable.
#
# The whole 0x1000 settings block is RW, so it is listed by table rather than
# field by field.  0x1200 (runtime) is R throughout and appears nowhere here.
_RW_TABLES = frozenset({SETTINGS})

# The RW subset of 0x1400 (device info), from the same document.  Everything
# else in that table -- the model, versions, serial number, run-time counters,
# the BLE name and the settings password -- reads only.
_RW_INFO_FIELDS = frozenset(
    {
        "uart1ProtoNo",  # 0x00B2  serial-port 1 protocol selection
        "canProtoNo",  # 0x00B3  CAN protocol selection
        "uart2ProtoNo",  # 0x00D4  serial-port 2 protocol selection
        "lcdBuzzerTrigger",  # 0x00E4  buzzer trigger source
        "dry1Trigger",  # 0x00E5  dry-contact 1 trigger source
        "dry2Trigger",  # 0x00E6  dry-contact 2 trigger source
        "lcdBuzzerTriggerVal",  # 0x00E8  and their thresholds
        "lcdBuzzerReleaseVal",  # 0x00EC
        "dry1TriggerVal",  # 0x00F0
        "dry1ReleaseVal",  # 0x00F4
        "dry2TriggerVal",  # 0x00F8
        "dry2ReleaseVal",  # 0x00FC
        "dataStoredPeriod",  # 0x0100  logging period
        "rcvTime",  # 0x0104  absorption time
        "rfvTime",  # 0x0105  float time
    }
)


@dataclass(frozen=True, kw_only=True)
class Register(FieldSpec):
    """One named field of one table, with its address and its access.

    A :class:`~devicectl.fields.FieldSpec` whose :attr:`~.FieldSpec.address`
    is the field's byte offset inside its table's payload, and which adds the
    two things that offset means nothing without: which table it is an offset
    into, and the datasource item it came from.

    Keyword-only, because the shared description's fields all have defaults
    and the two added here do not.  Everything builds these in one place
    anyway -- :class:`Catalog`, out of the datasource.
    """

    label: str = ""
    """What a person calls this register.  Every one of them has one."""

    options: Mapping[str, str] = dataclasses.field(default_factory=dict)
    """The ``sps`` table: enum labels, or bit descriptions for a bitmap.

    Narrowed from the shared description's "whatever the device calls a code"
    to what this datasource actually uses -- decimal strings -- and never
    None, because half this module's readers ask it what it holds.
    """

    table: str
    field: P.Field
    """The datasource item: scale, element type, count, and the frame offset.

    Kept whole rather than copied out field by field, because encoding a
    write goes through :func:`jkctl.protocol.encode_field`, which wants the
    item itself.
    """

    @property
    def key(self) -> str:
        """The machine name.  What the rest of this program calls a register."""
        return self.name

    @property
    def title(self) -> str:
        """The name to show a person: :mod:`jkctl.names`' where it has one.

        The web page and the command line both use this, so a setting is
        called the same wherever it is printed; :attr:`label` is JK's, and is
        shown beside it so the setting can still be found in JK's application.
        """
        return N.title_of(self.key, self.label)

    @property
    def description(self) -> str | None:
        """What the setting is, spelled out, where :mod:`jkctl.names` says."""
        return N.description_of(self.key)

    @property
    def byte_off(self) -> int:
        """Byte offset of the field inside the table's 293-byte payload."""
        return int(self.address)

    @property
    def size(self) -> int:
        """Width of the field in bytes."""
        return self.field.size

    @property
    def is_bitmap(self) -> bool:
        """Whether this field is a bag of named bits rather than one value."""
        return self.kind == BITS

    @property
    def is_array(self) -> bool:
        """Whether this field is a repeated numeric element (per-cell values)."""
        return self.kind == ARRAY

    @property
    def is_text(self) -> bool:
        """Whether this field is an ASCII string."""
        return self.kind == TEXT

    def register(self, addr_offset: int) -> int:
        """Return the Modbus register this field starts at."""
        from jkctl.modbus import TABLE_BASE

        return addr_offset + TABLE_BASE[self.table] + self.byte_off


@dataclass(frozen=True)
class Action:
    """One slot in the write-only action space at ``base + 0x600``."""

    slot: int
    width: int  # bytes to write: the vendor map says 2 or 4
    label: str
    warning: str | None  # what it does to a live battery, or None if benign


# The action list from JK's register map, in its own order.  `warning` is what
# the confirmation prompt reads out; an action with none is not asked about.
ACTIONS: dict[str, Action] = {
    "voltage-calibration": Action(
        0x00,
        4,
        "Voltage calibration",
        "recalibrates the pack-voltage measurement against the value you give",
    ),
    "shutdown": Action(
        0x04,
        2,
        "Shutdown",
        "powers the protection board down; it will stop answering on this bus",
    ),
    "current-calibration": Action(
        0x06,
        4,
        "Current calibration",
        "recalibrates the current shunt against the value you give",
    ),
    "li-ion": Action(
        0x0A,
        2,
        "Li-ion one-key preset",
        "overwrites every voltage and temperature setpoint with Li-ion defaults",
    ),
    "lifepo4": Action(
        0x0C,
        2,
        "LiFePO4 one-key preset",
        "overwrites every voltage and temperature setpoint with LiFePO4 defaults",
    ),
    "lto": Action(
        0x0E,
        2,
        "LTO one-key preset",
        "overwrites every voltage and temperature setpoint with LTO defaults",
    ),
    "emergency": Action(0x10, 2, "Emergency start", None),
    "time-calibration": Action(0x12, 4, "RTC synchronisation", None),
    # The three below are in neither revision of JK's register-map document,
    # whose action list stops at 0x12.  They were recovered from the vendor
    # application's own buttons: each of its "Send" buttons is connected to a
    # handler that fires one slot, and the same reading gives 0x04 for
    # "Shutdown Board" -- which the document *does* list, at 0x04, which is
    # what says the reading is right.  See findings §36.  None of them has
    # been fired at a real board.
    "restart": Action(
        0x16,
        2,
        "Restart board",
        "restarts the protection board; it stops answering until it is back",
    ),
    "factory-restore": Action(
        0x18,
        2,
        "Factory restore",
        "returns every setting to the factory configuration, losing yours",
    ),
    "erase-data": Action(
        0x1A,
        2,
        "Erase all data",
        "erases the board's stored data, including its history and its counters",
    ),
}

# Firmware upgrade arming.  It appears in neither revision of JK's register-map
# document -- the vendor action list stops at 0x12 -- and was recovered from
# jk-bms-monitor.exe (FUN_140017260) and corroborated against a capture of the
# real application on the wire.  It is not in ACTIONS because it is not an
# action a person invokes; jkctl.upgrade owns it.
UPGRADE_SLOT = 0x26


# --- where the vendor's English is not English -------------------------------------------
#
# JK ships one datasource for en_US and zh_CN, and parts of it were never
# translated: fifteen items of the runtime table, the six temperature-sensor
# bits and three option pairs are Chinese in the file the English application
# reads.  ``protocol_en.json`` is shipped exactly as the application decrypts
# it, so the English belongs here rather than in the data.
#
# A translation is only supplied where the vendor left Chinese.  Awkward
# English is the vendor's own and is kept, so a line here can still be found
# on JK's own screen.
_ENGLISH_LABELS: dict[tuple[str, str], str] = {
    (RUNTIME, "celMaxVol"): "Max Cell Volt.",  # 最高单体
    (RUNTIME, "celMinVol"): "Min Cell Volt.",  # 最低单体
    (RUNTIME, "cellWireRes"): "Balance Wire Res.",  # 均衡线电阻
    (RUNTIME, "cellWireResStat"): "Balance Wire Res. Status",  # 均衡线电阻状态
    (RUNTIME, "sysAlarm"): "System Alarm Flags",  # 系统报警标志
    (RUNTIME, "userAlarm"): "User Alarm Flags",  # 用户层报警
    (RUNTIME, "runtime"): "Run Time",  # 运行时间
    (RUNTIME, "userAlarm2"): "User Alarm Flags 2",  # 用户层报警2
    (RUNTIME, "timeDcOCPR"): "Discharge OCPR Time",  # 放电过流保护解除时间
    (RUNTIME, "timeDcSCPR"): "Discharge SCPR Time",  # 放电短路保护解除时间
    (RUNTIME, "timeCOCPR"): "Charge OCPR Time",  # 充电过流保护解除时间
    (RUNTIME, "timeCSCPR"): "Charge SCPR Time",  # 充电短路保护解除时间
    (RUNTIME, "timeUVPR"): "Cell UVPR Time",  # 单体欠压保护解除时间
    (RUNTIME, "timeOVPR"): "Cell OVPR Time",  # 单体过压保护解除时间
    # The vendor's own two names for this word disagree: the machine name says
    # a raised bit is a sensor that is *absent*, the Chinese label (传感器存在
    # 标志) says it is one that is *present*.  The label is the one that is
    # right -- the word's factory default is 255, and a unit with all six
    # probes wired raises all six bits -- so the English follows the label
    # while the machine name stays the vendor's, because that is what the
    # register is called on the wire.  See docs/reference.md.
    (RUNTIME, "tempSensorAbsent"): "Temp. Sensor Present",
}

# The names of the six temperature-sensor bits, and the Off/On pairs on three
# settings fields, in the same tables JK left in Chinese.
_ENGLISH_BITS: dict[tuple[str, str], dict[str, str]] = {
    (RUNTIME, "tempSensorAbsent"): {
        "0": "MOS",  # MOS温度
        "1": "Battery 1",  # 电池温度1
        "2": "Battery 2",
        "3": "Battery 3",
        "4": "Battery 4",
        "5": "Battery 5",
    },
    (SETTINGS, "devAddr"): {"1": "On"},  # 开启
    (SETTINGS, "dischrgPreChrgT"): {"0": "Off", "1": "On"},  # 关闭 / 开启
    (SETTINGS, "currentRange"): {"0": "Off", "1": "On"},
    # Six bits of the system alarm word carry a raised-state word belonging to
    # some other bit: the MOS over-temperature bit reads "Over Voltage" when
    # it trips, the battery over-voltage bit reads "Too Large", the charge
    # over-current bit reads "Error".  The bit names themselves are right, so
    # each raised state is set to what its own bit means.  Bit 7 is also a
    # short *circuit*, not a short "current"; bit 14 spells it correctly.
    (RUNTIME, "sysAlarm"): {
        "1": "Protection - MOS over temp.:Normal;Over Temp.",
        "2": "Cell count is not equal to settings:Normal;Mismatch",
        "3": "Protection - current sensor anomaly:Normal;Abnormal",
        "5": "Protection - Battery over voltage:Normal;Over Voltage",
        "6": "Protection - Charge over current:Normal;Over Current",
        "7": "Protection - Charge short circuit:Normal;Short Circuit",
    },
}


def _english(table: str, f: P.Field) -> dict[str, str]:
    """Return the field's option table with anything JK left untranslated in English."""
    return {**f.bits, **_ENGLISH_BITS.get((table, f.key), {})}


def _kind_of(f: P.Field, options: Mapping[str, str]) -> str:
    """Return which kind of thing this field is -- and so which editor it wants.

    Asked once here rather than by each front end, because the answer is a
    property of the field and the two would otherwise drift.

    :data:`ENUM` is claimed only when the datasource's option table covers
    every value the field's own range allows.  Several fields carry a stray
    two-entry table that has nothing to do with them -- ``devAddr`` ranges
    over 0..65535 and is labelled with the single word "on"; the
    pre-discharge time runs to 300 seconds and is labelled "off"/"on" -- and
    offering those as drop-downs makes an address unsettable and a timer a
    switch.  A field whose labels do not account for its range is a number
    with a misleading annotation, so it is edited as a number.

    :data:`FLAG` is the other half of that: a unitless field that ranges over
    0..1 is a switch whether or not the datasource troubled to name its two
    values, and showing somebody a spin box holding ``1`` for "is charging
    enabled" is the vendor's habit, not one to copy.  It is checked before
    :data:`ENUM` so that the three main switches read the same way as each
    other: ``balanEn`` carries Off/On labels and its two neighbours carry
    none, and a page that made one a drop-down and two checkboxes would be
    showing the datasource's inconsistency rather than the battery's state.
    """
    if f.kind == "bm":
        return BITS
    if f.kind == "a":
        return TEXT if f.atype in ("i8", "u8") else ARRAY
    if not f.unit and (f.minimum, f.maximum) == (0.0, 1.0):
        return FLAG
    if options and _cover_the_range(options, f):
        return ENUM
    return NUMBER


def _cover_the_range(options: Mapping[str, str], f: P.Field) -> bool:
    """Whether the option table names every value the field can hold."""
    keys = {int(k) for k in options if k.lstrip("-").isdigit()}
    if not keys:
        return False
    if f.minimum is None or f.maximum is None:
        return True  # no range to contradict them
    return keys >= set(range(int(f.minimum), int(f.maximum) + 1))


class Catalog:
    """Every register of every table, by name."""

    def __init__(self, proto: P.Protocol | None = None):
        """Build the catalog from a protocol datasource (the shipped one by default)."""
        self.protocol = proto or P.Protocol()
        self._by_key: dict[str, Register] = {}
        self._by_table: dict[str, list[Register]] = {}
        for table in TABLES:
            regs = []
            for f in self.protocol.tables[table]:
                if not f.key:
                    continue  # reserved padding: no name, no use
                options = _english(table, f)
                reg = Register(
                    table=table,
                    field=f,
                    name=f.key,
                    json=f.key,
                    kind=_kind_of(f, options),
                    address=f.off - P.DATA_OFF,
                    wire=f.ntype or f.atype,
                    label=_ENGLISH_LABELS.get((table, f.key), f.label),
                    unit=f.unit,
                    minimum=f.minimum,
                    maximum=f.maximum,
                    decimals=f.decimals,
                    default=f.default,
                    options=options,
                    access=_access_of(table, f.key),
                )
                regs.append(reg)
                # A key repeated across tables (switchStatus, enableFlags) is
                # resolved by table order: INFO, then RUNTIME, then SETTINGS.
                # Callers that mean a specific one pass the table explicitly.
                self._by_key.setdefault(reg.key, reg)
            self._by_table[table] = regs

    def __iter__(self) -> Iterator[Register]:
        """Iterate every register, table by table in TABLES order."""
        for table in TABLES:
            yield from self._by_table[table]

    def __len__(self) -> int:
        """Return how many named registers the catalog holds."""
        return sum(len(regs) for regs in self._by_table.values())

    def table(self, table: str) -> list[Register]:
        """Return every named register of one table, in wire order."""
        return list(self._by_table[table])

    def covering(self, table: str, byte_off: int, nbytes: int) -> list[Register]:
        """Return every register a run of a table's payload touches.

        The question a trace asks: a frame names a byte offset and a length,
        and what somebody reading it wants is the *names*.  A run that starts
        or ends inside a field still counts it -- a write that lands half
        inside a setpoint is exactly the kind of thing being looked for.
        """
        if table not in self._by_table:
            return []
        end = byte_off + nbytes
        return [
            reg
            for reg in self._by_table[table]
            if reg.byte_off < end and reg.byte_off + reg.size > byte_off
        ]

    def get(self, key: str, table: str | None = None) -> Register | None:
        """Return a register by name, optionally restricted to one table."""
        if table is None:
            return self._by_key.get(key)
        return next((r for r in self._by_table[table] if r.key == key), None)

    def find(self, key: str, table: str | None = None) -> Register:
        """Return a register by name, raising :class:`KeyError` if there is none."""
        reg = self.get(key, table)
        if reg is None:
            where = f" in table {table}" if table else ""
            raise KeyError(f"no register named {key!r}{where}")
        return reg

    def match(self, pattern: str) -> list[Register]:
        """Return every register whose key, name or label matches a glob, case-insensitively."""
        import fnmatch

        want = pattern.lower()
        return [
            r
            for r in self
            if fnmatch.fnmatch(r.key.lower(), want)
            or fnmatch.fnmatch(r.title.lower(), want)
            or fnmatch.fnmatch(r.label.lower(), want)
        ]


def _access_of(table: str, key: str) -> str:
    """Return "rw" if JK's register map marks this field writable, else "r"."""
    if table in _RW_TABLES:
        return READ_WRITE
    if table == INFO and key in _RW_INFO_FIELDS:
        return READ_WRITE
    return READ_ONLY


# --- the UART/CAN protocol tables ------------------------------------------------------

_PROTOCOLS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "protocols.json"
)


def protocol_names() -> dict[str, dict[int, str]]:
    """Return the id->name tables for the UART, CAN and trigger selectors.

    These are the lists the vendor's own application shows in its protocol
    drop-downs, extracted from its resources; the numbers written to
    ``uart1ProtoNo`` and friends index into them.  The name is the English
    one, because the vendor's own list is in Chinese in *both* the ``en_US``
    and the ``zh_CN`` datasource -- an English-speaking user of the vendor
    application is choosing between forty-two lines they cannot read.
    :func:`protocol_entries` keeps the original beside it.
    """
    return {
        kind: {number: en for number, (en, _) in table.items()}
        for kind, table in protocol_entries().items()
    }


def protocol_entries() -> dict[str, dict[int, tuple[str, str]]]:
    """Return ``id -> (english, vendor)`` for each selector list.

    The vendor string is kept because it is what JK's own application shows,
    and somebody comparing the two screens should be able to find the line.
    """
    with open(_PROTOCOLS_PATH, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return {
        kind: {
            item["id"]: (item.get("en") or item["name"], item["name"])
            for item in table.get("items", [])
        }
        for kind, table in doc.items()
    }


__all__ = [
    "ACTIONS",
    "ARRAY",
    "BITS",
    "INFO",
    "READ_ONLY",
    "READ_WRITE",
    "RUNTIME",
    "SETTINGS",
    "TABLES",
    "TABLE_LABELS",
    "UPGRADE_SLOT",
    "Action",
    "Catalog",
    "Register",
    "protocol_entries",
    "protocol_names",
]
