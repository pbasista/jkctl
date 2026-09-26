"""Turning what a person typed into what the wire takes, and back again.

Two rules run through this module.

The first is that input is checked, not coerced.  The vendor's application
accepts a setpoint outside its own stated range and quietly clamps or zeroes
it; ``jkctl settings set volCellUV=9`` says so and writes nothing.  The bounds
come from the datasource itself (``mi``/``mx``), so they are the vendor's, not
ours.

The second is that a field's display form is its scaled form.  The BMS stores
millivolts, milliamps and tenths of a degree; every number crossing this module
is in volts, amps and degrees, with the scale applied on the way out and undone
on the way in by :func:`jkctl.protocol.encode_field`.
"""

from __future__ import annotations

from typing import Any

from devicectl.fields import FieldError

from jkctl import protocol as P
from jkctl.errors import JkError
from jkctl.registers import Register

# The integer range of each datasource scalar type.  A value that survives the
# datasource's own mi/mx can still overflow the field it is stored in -- the
# bounds are absent on a third of the fields -- so both are checked.
_INT_RANGES: dict[str, tuple[int, int]] = {
    "u8": (0, 2**8 - 1),
    "i8": (-(2**7), 2**7 - 1),
    "u16": (0, 2**16 - 1),
    "i16": (-(2**15), 2**15 - 1),
    "u32": (0, 2**32 - 1),
    "i32": (-(2**31), 2**31 - 1),
    "u64": (0, 2**64 - 1),
    "i64": (-(2**63), 2**63 - 1),
}

# What counts as a boolean on the command line.  Several settings are a u32
# that only ever holds 0 or 1 (batChargeEn, balanEn), and the datasource labels
# them Off/On, so `jkctl settings set balanEn=on` should work.
_TRUE = frozenset({"1", "true", "yes", "on", "enable", "enabled"})
_FALSE = frozenset({"0", "false", "no", "off", "disable", "disabled"})


class RegisterValueError(JkError, FieldError):
    """A value that cannot be written to the register it was given for.

    Both at once, as the shared field module asks: a
    :class:`~jkctl.errors.JkError`, so the command line prints it as one line
    and the web server answers 400; and a
    :class:`~devicectl.fields.FieldError`, which is a ``ValueError``, because
    that is what a refused value is.
    """


def coerce_input(reg: Register, raw: str) -> Any:
    """Turn a command-line string into the value :func:`encode` expects.

    Accepts a number, an enum label from the register's own option table, or
    an on/off word for the flag-shaped settings.  Raises
    :class:`RegisterValueError` -- never a clamped value.
    """
    if reg.is_text:
        return _check_text(reg, raw)
    if reg.is_array:
        return _check_array(reg, raw)

    text = raw.strip()
    resolved = _resolve_option(reg, text)
    if resolved is not None:
        text = resolved

    if reg.is_bitmap:
        return _check_bounds_int(reg, _parse_int(reg, text))

    number = _parse_number(reg, text)
    _check_range(reg, number)
    _check_storable(reg, number)
    return number


def coerce_element(reg: Register, raw: str) -> float:
    """Turn one element of an array setting from a string into a checked value.

    The elements of an array share the field's scale, unit and bounds, so the
    same range check applies -- one element at a time is only a narrower way
    of saying the same thing.
    """
    if not reg.is_array:
        raise RegisterValueError(f"{reg.key}: not an array, so it has no elements")
    number = _parse_number(reg, raw.strip())
    _check_range(reg, number)
    return number


def format_element(reg: Register, value: Any) -> str:
    """Render one element of an array the way the whole array is rendered."""
    if value is None:
        return "-"
    text = format_value_bare(reg, value)
    return f"{text} {reg.unit}" if reg.unit else text


def encode(reg: Register, value: Any) -> bytes:
    """Encode a coerced value into the bytes the Modbus write carries."""
    try:
        return P.encode_field(reg.field, value, P.MODBUS_BYTEORDER)
    except (ValueError, OverflowError, TypeError) as exc:
        raise RegisterValueError(f"{reg.key}: {exc}") from exc


def format_value(reg: Register, value: Any) -> str:
    """Render a value the way the vendor's own UI would, unit included."""
    if value is None:
        return "-"
    if reg.is_bitmap:
        return f"0x{int(value):0{reg.size * 2}X}"
    if isinstance(value, (list, tuple)):
        return " ".join(format_value_bare(reg, v) for v in value)
    text = format_value_bare(reg, value)
    return f"{text} {reg.unit}" if reg.unit else text


def format_value_bare(reg: Register, value: Any) -> str:
    """Render one value with the register's decimals but no unit."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{reg.decimals if reg.decimals is not None else 3}f}"
    label = _label_for(reg, value)
    return label if label is not None else str(value)


def values_equal(reg: Register, a: Any, b: Any) -> bool:
    """Whether two values land on the same bytes, so a rewrite would be a no-op.

    Compared at wire precision rather than in Python: 2.9 V and 2.9004 V are
    the same millivolt, and a settings import that reports them as a change
    would write on every run.
    """
    if a is None or b is None:
        return a is b
    try:
        return encode(reg, a) == encode(reg, b)
    except RegisterValueError:
        return a == b


def decode_bits(reg: Register, value: int | None) -> list[tuple[int, str, bool, str]]:
    """Split a bitmap into ``(bit, name, set, state)`` rows.

    The datasource writes a bit's description as ``name`` or
    ``name:when-clear;when-set``, so a raised alarm bit can be reported by the
    word the vendor's own UI would show rather than as a number.
    """
    if value is None:
        return []
    rows = []
    for key, text in sorted(reg.options.items(), key=lambda kv: int(kv[0])):
        bit = int(key)
        name, _, states = text.partition(":")
        raised = bool(value >> bit & 1)
        clear, _, on = states.partition(";")
        state = (on if raised else clear) if states else ("yes" if raised else "no")
        rows.append((bit, name, raised, state.strip()))
    return rows


# --- the checks -------------------------------------------------------------------------


def _resolve_option(reg: Register, text: str) -> str | None:
    """Return the stored value for an enum *label*, or None if this is not one.

    Bitmaps are excluded: their option table describes bits, not values.
    """
    if reg.is_bitmap or not reg.options:
        return None
    lowered = text.lower()
    for value, label in reg.options.items():
        if label.split(":")[0].strip().lower() == lowered:
            return value
    if lowered in _TRUE:
        return "1"
    if lowered in _FALSE:
        return "0"
    return None


def _parse_number(reg: Register, text: str) -> float | int:
    """Parse a decimal number, keeping an integer field integral."""
    try:
        if reg.field.ntype in ("f32", "f64") or reg.field.scale != 1.0:
            return float(text)
        return _parse_int(reg, text)
    except ValueError as exc:
        raise RegisterValueError(f"{reg.key}: {text!r} is not a number") from exc


def _parse_int(reg: Register, text: str) -> int:
    """Parse an integer, accepting 0x/0b/0o prefixes for the bit fields."""
    try:
        return int(text, 0)
    except ValueError as exc:
        raise RegisterValueError(f"{reg.key}: {text!r} is not an integer") from exc


def _check_range(reg: Register, value: float) -> None:
    """Refuse a value outside the datasource's own bounds for this field."""
    if reg.minimum is not None and value < reg.minimum:
        raise RegisterValueError(
            f"{reg.key}: {format_value(reg, value)} is below the minimum "
            f"{format_value(reg, reg.minimum)}"
        )
    if reg.maximum is not None and value > reg.maximum:
        raise RegisterValueError(
            f"{reg.key}: {format_value(reg, value)} is above the maximum "
            f"{format_value(reg, reg.maximum)}"
        )


def _check_storable(reg: Register, value: float) -> None:
    """Refuse a value the field's own integer type cannot hold."""
    span = _INT_RANGES.get(reg.field.ntype or "")
    if span is None:
        return
    stored = round(value / reg.field.scale) if reg.field.scale != 1.0 else round(value)
    if not span[0] <= stored <= span[1]:
        raise RegisterValueError(
            f"{reg.key}: {format_value(reg, value)} does not fit a "
            f"{reg.field.ntype} register"
        )


def _check_bounds_int(reg: Register, value: int) -> int:
    """Refuse a bitmap word wider than the field."""
    if not 0 <= value < 1 << (reg.size * 8):
        raise RegisterValueError(
            f"{reg.key}: 0x{value:X} does not fit {reg.size} byte(s)"
        )
    return value


def _check_text(reg: Register, raw: str) -> str:
    """Refuse an ASCII value that will not fit, or that is not ASCII."""
    try:
        data = raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RegisterValueError(f"{reg.key}: must be ASCII") from exc
    if len(data) > reg.size:
        raise RegisterValueError(
            f"{reg.key}: {raw!r} is longer than {reg.size} characters"
        )
    return raw


def _check_array(reg: Register, raw: str) -> list[float]:
    """Parse a comma-separated array, checking its length and each element."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != reg.field.count:
        raise RegisterValueError(
            f"{reg.key}: takes {reg.field.count} comma-separated values, got {len(parts)}"
        )
    values = []
    for part in parts:
        value = _parse_number(reg, part)
        _check_range(reg, value)
        values.append(value)
    return values


def _label_for(reg: Register, value: Any) -> str | None:
    """Return the datasource's label for an enum value, if it has one."""
    if reg.is_bitmap or not reg.options:
        return None
    text = reg.options.get(str(value))
    return text.split(":")[0].strip() if text else None


__all__ = [
    "RegisterValueError",
    "coerce_element",
    "coerce_input",
    "decode_bits",
    "encode",
    "format_element",
    "format_value",
    "format_value_bare",
    "values_equal",
]
