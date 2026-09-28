"""
JK BMS ``.jkbms`` firmware container.

Reverse engineered from jk-bms-monitor.exe 3.11.0 (Chengdu Jikong Technology):

  FUN_1400454a0 -- decrypt + inflate + sanity check
  FUN_1400456b0 -- file level checks (exists / open / size)
  FUN_140045d90 -- header extraction and device compatibility checks

Container layout
----------------
  file            = AES-256-CBC(key=KEY, iv=0)  of  payload_blob
  payload_blob    = uint32_le raw_len || zlib_deflate_stream
  payload         = zlib.decompress(...)          (len == raw_len)
  payload[-12:]   = int64_le build_ms || int32_le valid_hours   (trailer)
  image           = payload[:-12]                 (what is actually flashed)

The image is a raw ARM Cortex-M application image. Its link address is not
stored explicitly in the container; all 67 audited images start at
0x08002000. A fixed 6-field metadata header lives at image offset 0x200.
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from jkctl import aes
from jkctl.errors import JkError

# String literal at .rdata 0x1401fb730, passed to J::Aes::Aes() in FUN_1400454a0.
# Used verbatim as a 32-byte key -> AES-256. IV is all zero.
KEY = b"A39FF3F613F94FDD957EC22EF642ADA9"

MAX_ENCRYPTED = 20 * 1024 * 1024  # FUN_1400456b0: size < 0x1400001
MAX_RAW = 0x1400000  # FUN_1400454a0: raw_len <= 0x1400000
MIN_PAYLOAD = 0x260  # FUN_140045d90: requires len > 0x25f
TRAILER = 12

# The container's own framing: a uint32 length prefix, a two-part version
# string, and the sentinel a build with no device code carries.
LENGTH_PREFIX = 4
VERSION_PARTS = 2
NO_DEVICE_CODE = 0xFFFFFFFF

HDR_BASE = 0x200
_FIELDS = {
    "version": 0x200,
    "build_date": 0x210,
    "reserved1": 0x220,
    "build_time": 0x230,
    "reserved2": 0x240,
    "model": 0x250,
}
DEVICE_CODE_OFF = 0x260  # uint32_le; 0xFFFFFFFF when absent (HW V14/V15)


class FirmwareError(JkError):
    """Raised when a .jkbms file fails one of the app's validation steps."""


@dataclass
class Firmware:
    """One parsed .jkbms file: its metadata, and the image it carries."""

    path: str
    image: bytes  # payload[:-12] -- the bytes that get flashed
    version: str  # e.g. "19.02"
    major: int
    minor: int
    model: str  # e.g. "JK_PB2A16S20P" / "JK-B2A8S20P"
    build_date: str
    build_time: str
    device_code: int | None
    build_ms: int
    valid_hours: int

    @property
    def sp(self) -> int:
        """The initial stack pointer, from word 0 of the image's vector table."""
        return struct.unpack_from("<I", self.image, 0)[0]

    @property
    def reset(self) -> int:
        """The reset handler address, from word 1 of the vector table."""
        return struct.unpack_from("<I", self.image, 4)[0]

    def describe(self) -> str:
        """Render the file's metadata as one block of label/value lines."""
        code = "n/a" if self.device_code is None else str(self.device_code)
        return (
            f"model        : {self.model}\n"
            f"version      : {self.version}  (major={self.major} minor={self.minor})\n"
            f"device code  : {code}\n"
            f"built        : {self.build_date} {self.build_time}\n"
            f"image size   : {len(self.image)} bytes\n"
            f"vector table : SP=0x{self.sp:08x} RESET=0x{self.reset:08x}"
        )


def _cstr(buf: bytes, off: int, size: int = 16) -> str:
    return buf[off : off + size].split(b"\0", 1)[0].decode("ascii", "replace").strip()


def load(path: str | Path) -> Firmware:
    """Parse and validate a .jkbms file exactly the way the Windows app does.

    Takes a path of either kind: a spooled browser upload arrives as a
    ``Path``, a filename typed on the command line as a ``str``.  What the
    :class:`Firmware` carries is the text of it, which is what gets printed.
    """
    with open(path, "rb") as fh:
        data = fh.read()

    # FUN_1400456b0
    if not data:
        raise FirmwareError("Encrypted firmware is empty")
    if len(data) > MAX_ENCRYPTED:
        raise FirmwareError(
            f"Encrypted firmware is too large (>{MAX_ENCRYPTED // 1024 // 1024}MB)"
        )
    if len(data) % 16:
        raise FirmwareError(
            f"Encrypted firmware is not a multiple of the AES block size ({len(data)})"
        )

    # FUN_1400454a0
    plain = aes.decrypt_cbc(KEY, b"\0" * 16, data)
    if len(plain) < LENGTH_PREFIX:
        raise FirmwareError("Firmware is invalid (truncated)")
    raw_len = struct.unpack_from("<I", plain, 0)[0]
    if raw_len > MAX_RAW:
        raise FirmwareError(
            f"Firmware is invalid (declared size {raw_len} > {MAX_RAW})"
        )
    try:
        payload = zlib.decompress(plain[4:])
    except zlib.error as exc:
        raise FirmwareError(f"Firmware is invalid (inflate failed: {exc})") from exc
    if len(payload) != raw_len:
        raise FirmwareError(
            f"Firmware is invalid (size {len(payload)} != declared {raw_len})"
        )

    # FUN_140045d90
    if len(payload) <= MIN_PAYLOAD - 1:
        raise FirmwareError("Binary file is invalid (no metadata header)")

    build_ms, valid_hours = struct.unpack_from("<qi", payload, len(payload) - TRAILER)
    image = payload[:-TRAILER]

    version = _cstr(payload, _FIELDS["version"])
    parts = version.split(".")
    if len(parts) != VERSION_PARTS or not all(p.isdigit() for p in parts):
        raise FirmwareError(f"Software version is invalid! ({version})")

    code = struct.unpack_from("<I", payload, DEVICE_CODE_OFF)[0]

    return Firmware(
        path=str(path),
        image=image,
        version=version,
        major=int(parts[0]),
        minor=int(parts[1]),
        model=_cstr(payload, _FIELDS["model"]),
        build_date=_cstr(payload, _FIELDS["build_date"]),
        build_time=_cstr(payload, _FIELDS["build_time"]),
        device_code=None if code == NO_DEVICE_CODE else code,
        build_ms=build_ms,
        valid_hours=valid_hours,
    )


HDR_FIELD_SIZE = 16
AES_BLOCK = 16


def set_header_field(
    image: bytes, off: int, text: str, size: int = HDR_FIELD_SIZE
) -> bytes:
    """Return ``image`` with the metadata field at ``off`` set to ``text``.

    The 6-field metadata header lives inside the image at offset 0x200 (see
    :data:`_FIELDS`), so updating a field -- bumping the version to clear the
    vendor's minor-version gate, say -- is an edit of the image bytes, not of
    the container around them.  The field is a fixed ``size``-byte NUL-padded
    ASCII slot; ``text`` must leave room for at least one terminator.
    """
    raw = text.encode("ascii")
    if len(raw) >= size:
        raise FirmwareError(
            f"metadata field value {text!r} is too long for its {size}-byte slot"
        )
    if off + size > len(image):
        raise FirmwareError("image is too small to hold that metadata field")
    out = bytearray(image)
    out[off : off + size] = raw + b"\0" * (size - len(raw))
    return bytes(out)


def build(image: bytes, *, build_ms: int = 0, valid_hours: int = 0) -> bytes:
    """Encode ``image`` into a ``.jkbms`` container -- the inverse of :func:`load`.

    Produces a file JK's application (and :func:`load`) accepts:

        payload = image || int64_le build_ms || int32_le valid_hours
        blob    = uint32_le len(payload) || zlib_deflate(payload)
        file    = AES-256-CBC(KEY, iv=0, PKCS#7(blob))

    The 6-field metadata header the app reads (model, version, ...) is part of
    ``image`` at offset 0x200, so it is carried through as-is; use
    :func:`set_header_field` or :func:`repack` to change it first.

    This is not byte-identical to JK's own file -- zlib's output depends on the
    encoder -- but it round-trips exactly: ``load(build(fw.image, ...))``
    reproduces the same image and metadata.  ``valid_hours <= 0`` means no
    expiry, which is what an unmodified research build should carry.
    """
    if len(image) < DEVICE_CODE_OFF + 4:
        raise FirmwareError(
            f"image is too small to carry a metadata header ({len(image)} bytes)"
        )
    payload = image + struct.pack("<qi", int(build_ms), int(valid_hours))
    if len(payload) > MAX_RAW:
        raise FirmwareError(f"payload is too large ({len(payload)} > {MAX_RAW})")
    blob = struct.pack("<I", len(payload)) + zlib.compress(payload, 9)
    padlen = AES_BLOCK - (len(blob) % AES_BLOCK)  # PKCS#7 (a full block if aligned)
    blob += bytes([padlen]) * padlen
    data = aes.encrypt_cbc(KEY, b"\0" * 16, blob)
    if len(data) > MAX_ENCRYPTED:
        raise FirmwareError(
            f"encrypted file is too large ({len(data)} > {MAX_ENCRYPTED})"
        )
    return data


def repack(
    fw: Firmware,
    *,
    image: bytes | None = None,
    version: str | None = None,
    model: str | None = None,
    build_ms: int | None = None,
    valid_hours: int | None = None,
) -> bytes:
    """Re-emit ``fw`` as a ``.jkbms``, optionally with a new image or metadata.

    The metadata that is not overridden is taken from ``fw``.  ``version`` must
    be the ``major.minor`` form the app parses; bumping the minor above the
    device's is how a repacked image clears the vendor's "must be newer" gate.
    """
    img = fw.image if image is None else image
    if version is not None:
        parts = version.split(".")
        if len(parts) != VERSION_PARTS or not all(p.isdigit() for p in parts):
            raise FirmwareError(f"version must be major.minor, got {version!r}")
        img = set_header_field(img, _FIELDS["version"], version)
    if model is not None:
        img = set_header_field(img, _FIELDS["model"], model)
    return build(
        img,
        build_ms=fw.build_ms if build_ms is None else build_ms,
        valid_hours=fw.valid_hours if valid_hours is None else valid_hours,
    )


@dataclass(frozen=True)
class Check:
    """One step of the vendor's compatibility gate, and how it went."""

    name: str
    ok: bool
    detail: str
    waived: bool = False
    """True when the step failed and ``--force`` is allowed to let it past."""
    kind: str = "compatibility"
    """``expiry`` for the time-window step, which has its own command path."""

    @property
    def blocking(self) -> bool:
        """Whether this step stops the flash."""
        return not self.ok and not self.waived


def gate(
    fw: Firmware,
    dev_model: str,
    dev_version: str,
    *,
    force: bool = False,
    now_ms: int | None = None,
) -> list[Check]:
    """Run the whole gate and return every step, passed or not.

    :func:`check_compatible` answers "may this be flashed" by raising on the
    first refusal, which is the right shape for a command and the wrong one
    for anything that wants to *show* the reasoning: a file refused on its
    minor version says nothing about whether the model matched, and "no"
    without the four checks that passed is the vendor dialog's least helpful
    habit.  This runs all of them.

    ``force`` marks the two steps JK's own "Force Updating" waives -- the
    expiry window and the minor version -- as waived rather than failed.  The
    model and the major version are never waived, here or there.
    """
    checks: list[Check] = []
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms

    if fw.valid_hours <= 0:
        checks.append(Check("expiry", True, "not a time-limited build", kind="expiry"))
    else:
        end = fw.build_ms + fw.valid_hours * 3600_000
        inside = fw.build_ms - 7200_000 <= now_ms <= end
        checks.append(
            Check(
                "expiry",
                inside,
                "within its validity window"
                if inside
                else "this is a time-limited build and is past its validity window",
                waived=force and not inside,
                kind="expiry",
            )
        )

    parts = dev_version.split(".")
    readable = len(parts) == VERSION_PARTS and all(p.isdigit() for p in parts)
    checks.append(
        Check(
            "device version",
            readable,
            f"the unit is at version {dev_version}"
            if readable
            else f"the unit's version could not be read (got {dev_version!r})",
        )
    )
    if not readable:
        return checks
    dev_major, dev_minor = int(parts[0]), int(parts[1])

    same_major = fw.major == dev_major
    checks.append(
        Check(
            "major version",
            same_major,
            f"the file and the unit are both major version {fw.major}"
            if same_major
            else f"the file is for major version {fw.major}, "
            f"but this unit is major version {dev_major}",
        )
    )
    newer = dev_minor < fw.minor
    checks.append(
        Check(
            "minor version",
            newer,
            f"the file (minor {fw.minor}) is newer than the unit (minor {dev_minor})"
            if newer
            else f"the file's minor version ({fw.minor}) is not higher than "
            f"the unit's ({dev_minor}); flashing requires a higher minor version",
            waived=force and not newer,
        )
    )
    same_model = fw.model == dev_model
    checks.append(
        Check(
            "model",
            same_model,
            f"the file and the unit are both {fw.model}"
            if same_model
            else f"the file is for model {fw.model!r}, but this unit is {dev_model!r}",
        )
    )
    return checks


@dataclass(frozen=True)
class Candidate:
    """One file found on disk, parsed if it could be."""

    path: str
    firmware: Firmware | None = None
    error: str = ""


def scan(directory: str, *, suffix: str = ".jkbms") -> list[Candidate]:
    """Parse every firmware file under ``directory``, worst news last.

    JK ships firmware as a tree of one directory per hardware version, each
    holding one file per model, and the vendor's dialog opens them one at a
    time and tells you afterwards that it was the wrong one.  Reading the lot
    costs one AES-CBC decrypt and one inflate each, which is fast enough on
    anything with OpenSSL present to be worth doing before choosing.

    A file that will not parse is a :class:`Candidate` carrying the reason
    rather than an exception: one damaged download should not hide the other
    sixty images beside it.
    """
    found: list[Candidate] = []
    for root, _dirs, names in os.walk(directory):
        for name in sorted(names):
            if not name.lower().endswith(suffix):
                continue
            path = os.path.join(root, name)
            try:
                found.append(Candidate(path, load(path)))
            except (FirmwareError, OSError) as exc:
                found.append(Candidate(path, None, str(exc)))
    return sorted(found, key=_candidate_order)


def _candidate_order(item: Candidate) -> tuple:
    """Sort by model, then by version descending, with unreadable files last."""
    fw = item.firmware
    if fw is None:
        return (1, "", 0, 0, item.path)
    return (0, fw.model, -fw.major, -fw.minor, item.path)


def check_expiry(fw: Firmware, now_ms: int) -> None:
    """FUN_140045d90 time window check. Only enforced when valid_hours > 0."""
    if fw.valid_hours <= 0:
        return
    if (
        now_ms > fw.build_ms + fw.valid_hours * 3600_000
        or now_ms < fw.build_ms - 7200_000
    ):
        raise FirmwareError(
            "Binary file is invalid (time-limited build outside its validity window)"
        )


def check_compatible(
    fw: Firmware, dev_model: str, dev_version: str, force: bool = False
) -> None:
    """
    Replicates the device compatibility gate in FUN_140045d90.

    dev_model   <- frame/03 manuDeviceID
    dev_version <- frame/03 softwareVersion
    """
    for check in gate(fw, dev_model, dev_version, force=force):
        if check.kind == "expiry":
            continue  # the time window is check_expiry's to enforce
        if check.blocking:
            raise FirmwareError(check.detail)
