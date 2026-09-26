"""The configuration table: reading it, planning a change, and writing it back.

A change is always a *plan* first -- a list of ``(register, old, new)`` -- and
the plan is what gets shown, what ``--dry-run`` stops at, and what gets
written.  Values already at the wanted setting drop out of it, so importing the
same file twice reports nothing to do rather than rewriting sixty registers.

The multiplexed switch word is handled apart from the rest: it holds sixteen
unrelated settings in one register, so changing one of them is a
read-modify-write of all of them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jkctl import values as V
from jkctl.device import Device, DeviceError
from jkctl.errors import JkError
from jkctl.registers import SETTINGS, Catalog, Register

# The register holding the ten or so on/off settings the vendor's UI shows as
# checkboxes.  frame/02 has a register of the same name that is a three-bit
# read-only status byte, which is not this one -- hence the explicit table
# everywhere it is looked up.
SWITCH_REGISTER = "switchStatus"

# The three settings the charge/discharge/balance commands drive.  They are
# ordinary settings registers, not action slots.
TOGGLES = {
    "charge": "batChargeEn",
    "discharge": "batDischargeEn",
    "balance": "balanEn",
}


# Settings that are held in an order: each one below the next, as a
# protection sits below its recovery or above it.  A plan moving several of
# one chain in one direction writes them so that no step crosses two of them
# -- the bottom one first on the way down, the top one first on the way up.
# The board may refuse a value that would cross its neighbour, and a preset
# moving a pack from one chemistry to another moves all five voltages at once.
CHAINS = (
    ("volSysPwrOff", "volCellUV", "volCellUVPR", "volCellOVPR", "volCellOV"),
    ("tmpBatCUT", "tmpBatCUTPR"),
    ("tmpBatCOTPR", "tmpBatCOT"),
    ("tmpBatDcOTPR", "tmpBatDcOT"),
    ("tmpMosOTPR", "tmpMosOT"),
)


class SettingsError(JkError):
    """A settings file or assignment that cannot be applied."""


# ``cellConWireRes[3]`` -- one element of an array setting, which is how JK's
# own application edits the thirty-two connection-wire resistances.
_INDEXED = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*(\d+)\s*\]$")


def resolve_key(
    catalog: Catalog, key: str, table: str = SETTINGS
) -> tuple[Register, int | None]:
    """Resolve a register name, with or without an element index.

    Returns the register and the element wanted, or ``None`` for the whole
    field.  Raises :class:`SettingsError` naming what went wrong, because
    every caller of this would otherwise write the same three messages.

    ``table`` is the settings table for everything the CLI does with this.
    The web UI's Ports tab edits the writable subset of the device-info table
    through the same plan-and-diff path, which is what the parameter is for:
    one editing model for every register JK marks RW, rather than a second
    one for the four that happen to live elsewhere.
    """
    key = key.strip()
    index: int | None = None
    match = _INDEXED.match(key)
    if match:
        key, index = match.group(1), int(match.group(2))
    reg = catalog.get(key, table)
    if reg is None:
        raise SettingsError(f"no setting named {key!r} (try: jkctl settings list)")
    if index is None:
        return reg, None
    if not reg.is_array:
        raise SettingsError(f"{key} is a single value, so {key}[{index}] means nothing")
    if not 0 <= index < reg.field.count:
        raise SettingsError(
            f"{key} has elements 0..{reg.field.count - 1}, so there is no [{index}]"
        )
    return reg, index


@dataclass(frozen=True)
class Change:
    """One planned write: a whole register, or one element of an array one."""

    register: Register
    old: Any
    new: Any
    index: int | None = None

    @property
    def name(self) -> str:
        """What to call this change: the register, with its index if it has one."""
        if self.index is None:
            return self.register.key
        return f"{self.register.key}[{self.index}]"

    def describe(self) -> str:
        """Render the change the way the plan prints it."""
        show = V.format_value if self.index is None else V.format_element
        return (
            f"{self.register.title} ({self.name}): {show(self.register, self.old)}"
            f" -> {show(self.register, self.new)}"
        )


def read(device: Device, table: str = SETTINGS) -> dict[str, Any]:
    """Read a whole table (the settings one unless told otherwise)."""
    return device.snapshot(table)


def plan(
    device: Device,
    wanted: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
    table: str = SETTINGS,
) -> list[Change]:
    """Work out which of ``wanted`` differs from the device, in table order.

    ``wanted`` holds already-coerced values.  Registers the device did not
    answer for are skipped rather than written blind: a value read back as
    None is a register this board does not map, and writing it would be
    writing into somewhere unknown.
    """
    # Silently dropping a register the caller asked for would turn a typo, or
    # a name taken from the wrong table, into a change that never happened and
    # was never reported.  resolve_key raises on anything it cannot place.
    targets = {key: resolve_key(device.catalog, key, table) for key in wanted}
    current = read(device, table) if current is None else current
    order = {reg.key: n for n, reg in enumerate(device.catalog.table(table))}
    changes = []
    for key in sorted(
        wanted, key=lambda k: (order[targets[k][0].key], targets[k][1] or 0)
    ):
        reg, index = targets[key]
        if not reg.writable:
            raise SettingsError(f"{reg.key} is read-only")
        if reg.key in device.unwritable:
            raise SettingsError(device.unwritable[reg.key])
        held = current.get(reg.key)
        if held is None:
            raise SettingsError(
                f"{reg.key} is not mapped on this device; refusing to write it"
            )
        new = wanted[key]
        if index is None:
            if not V.values_equal(reg, held, new):
                changes.append(Change(reg, held, new))
            continue
        old = held[index] if isinstance(held, (list, tuple)) else None
        if old is None or not V.values_equal(reg, [old], [new]):
            changes.append(Change(reg, old, new, index))
    return _in_safe_order(changes)


def _in_safe_order(changes: list[Change]) -> list[Change]:
    """Reorder each chain's changes within the slots they already hold (CHAINS)."""
    for chain in CHAINS:
        slots = [
            n for n, c in enumerate(changes) if c.index is None and c.name in chain
        ]
        if len(slots) <= 1:  # one alone has nothing to cross
            continue
        mine = [changes[n] for n in slots]
        falling = sum(float(c.new) - float(c.old) for c in mine) < 0
        mine.sort(key=lambda c: chain.index(c.name), reverse=not falling)
        for n, change in zip(slots, mine, strict=True):
            changes[n] = change
    return changes


def apply(device: Device, changes: list[Change]) -> None:
    """Write a plan, one register per Modbus transaction.

    A refusal part-way stops the plan there, and its message says which of
    the changes before it did go in: a plan is several writes, not one, and
    "refused" alone would leave the reader to assume none of it happened.
    """
    for done, change in enumerate(changes):
        try:
            if change.index is None:
                device.write_register(change.register, change.new)
            else:
                device.write_element(change.register, change.index, change.new)
        except DeviceError as exc:
            if done:
                written = ", ".join(c.name for c in changes[:done])
                exc.args = (f"{exc}. Written before it: {written}; nothing after",)
            raise


def parse_assignments(
    catalog: Catalog, pairs: list[str], table: str = SETTINGS
) -> dict[str, Any]:
    """Turn ``KEY=VALUE`` words into coerced values, or say why they will not do."""
    wanted: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise SettingsError(f"{pair!r} is not KEY=VALUE")
        key = key.strip()
        reg, index = resolve_key(catalog, key, table)
        wanted[key] = (
            V.coerce_input(reg, raw) if index is None else V.coerce_element(reg, raw)
        )
    return wanted


def export(device: Device, *, current: dict[str, Any] | None = None) -> str:
    """Render the writable settings as JSON, ready to be imported elsewhere.

    Read-only fields are left out on purpose: a file that carries them invites
    an import that fails halfway, and the serial number of one battery has no
    business in the configuration of another.
    """
    values = read(device) if current is None else current
    doc = {
        "model": device.model,
        "software_version": device.version,
        "settings": {
            reg.key: values[reg.key]
            for reg in device.catalog.table(SETTINGS)
            if reg.writable and values.get(reg.key) is not None
        },
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def load(catalog: Catalog, text: str) -> dict[str, Any]:
    """Parse an exported settings file back into coerced values."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"not a settings file: {exc}") from exc
    raw = doc.get("settings") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        raise SettingsError("settings file has no 'settings' object")
    wanted: dict[str, Any] = {}
    for key, value in raw.items():
        reg = catalog.get(key, SETTINGS)
        if reg is None:
            continue  # a field this build does not know; not an error
        if not reg.writable:
            continue
        wanted[key] = value
    return wanted


def switch_bits(catalog: Catalog) -> list[tuple[int, str]]:
    """Return ``(bit, name)`` for every switch the multiplexed word carries."""
    reg = catalog.find(SWITCH_REGISTER, SETTINGS)
    return [(bit, name) for bit, name, _, _ in V.decode_bits(reg, 0)]


def switch_state(
    catalog: Catalog, word: int | None
) -> list[tuple[int, str, bool, str]]:
    """Decode the multiplexed switch word into named on/off rows."""
    return V.decode_bits(catalog.find(SWITCH_REGISTER, SETTINGS), word)


def find_switch(catalog: Catalog, name: str) -> int:
    """Return the bit number of a switch named on the command line."""
    wanted = name.strip().lower().replace("_", " ").replace("-", " ")
    for bit, label in switch_bits(catalog):
        if label.lower().replace("-", " ") == wanted:
            return bit
    raise SettingsError(f"no switch named {name!r} (try: jkctl switches show)")


__all__ = [
    "SWITCH_REGISTER",
    "TOGGLES",
    "Change",
    "SettingsError",
    "apply",
    "export",
    "find_switch",
    "load",
    "parse_assignments",
    "plan",
    "read",
    "resolve_key",
    "switch_bits",
    "switch_state",
]
