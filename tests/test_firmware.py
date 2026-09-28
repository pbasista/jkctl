"""The .jkbms container, its validation chain, and the XMODEM sender."""

from __future__ import annotations

import struct
import time
import zlib

import pytest

from jkctl import firmware as F, upgrade as U

PAYLOAD_SIZE = 0x400
IMAGE_SIZE = PAYLOAD_SIZE - F.TRAILER


def make_payload(
    *,
    version="15.42",
    model="JK_PB2A16S20P",
    device_code=73,
    valid_hours=0,
    build_ms=None,
) -> bytes:
    """Build the decrypted payload of a .jkbms, laid out as the vendor's is."""
    payload = bytearray(PAYLOAD_SIZE)
    struct.pack_into("<I", payload, 0, 0x20004E00)  # stack pointer
    struct.pack_into("<I", payload, 4, 0x08018000)  # reset vector
    for offset, text in (
        (0x200, version),
        (0x210, "May 13 2025"),
        (0x230, "09:56:31"),
        (0x250, model),
    ):
        payload[offset : offset + len(text)] = text.encode()
    struct.pack_into("<I", payload, F.DEVICE_CODE_OFF, device_code)
    struct.pack_into(
        "<qi",
        payload,
        len(payload) - F.TRAILER,
        int(time.time() * 1000) if build_ms is None else build_ms,
        valid_hours,
    )
    return bytes(payload)


def make_blob(payload: bytes) -> bytes:
    """Wrap a payload the way the container does, before encryption."""
    blob = struct.pack("<I", len(payload)) + zlib.compress(payload)
    return blob + b"\x00" * (-len(blob) % 16)


@pytest.fixture
def jkbms(tmp_path, monkeypatch):
    """Return a factory writing a .jkbms whose decryption is stubbed out.

    The tool only ever decrypts, so there is no encryptor to build a fixture
    with -- and adding one to ship would be production code that only the
    tests use.  The cipher itself is covered by its own known-answer test;
    what these tests are about is everything the container does around it.
    """

    def _make(*, blob=None, **kwargs) -> str:
        blob = make_blob(make_payload(**kwargs)) if blob is None else blob
        path = tmp_path / "fw.jkbms"
        # The file's own bytes are never inspected -- only its length is, and
        # the container requires a multiple of the AES block size.
        path.write_bytes(b"\x00" * len(blob))
        monkeypatch.setattr(F.aes, "decrypt_cbc", lambda key, iv, data: blob)
        return str(path)

    return _make


def test_a_container_round_trips(jkbms):
    fw = F.load(jkbms())
    assert (fw.model, fw.version, fw.major, fw.minor) == (
        "JK_PB2A16S20P",
        "15.42",
        15,
        42,
    )
    assert fw.device_code == 73
    assert fw.build_date == "May 13 2025" and fw.build_time == "09:56:31"
    assert len(fw.image) == IMAGE_SIZE
    assert fw.sp == 0x20004E00 and fw.reset == 0x08018000


def test_a_v14_or_v15_build_reports_no_device_code(jkbms):
    assert F.load(jkbms(device_code=0xFFFFFFFF)).device_code is None


def test_a_file_that_is_not_a_multiple_of_the_block_size_is_refused(
    tmp_path, monkeypatch
):
    path = tmp_path / "fw.jkbms"
    path.write_bytes(b"\x00" * 33)
    with pytest.raises(F.FirmwareError, match="block size"):
        F.load(str(path))


def test_an_empty_file_is_refused(tmp_path):
    path = tmp_path / "fw.jkbms"
    path.write_bytes(b"")
    with pytest.raises(F.FirmwareError, match="empty"):
        F.load(str(path))


def test_a_blob_that_will_not_inflate_is_refused(jkbms):
    with pytest.raises(F.FirmwareError, match="inflate failed"):
        F.load(jkbms(blob=struct.pack("<I", 1024) + b"\x00" * 44))


def test_a_declared_length_that_does_not_match_is_refused(jkbms):
    blob = struct.pack("<I", 999) + zlib.compress(make_payload())
    blob += b"\x00" * (-len(blob) % 16)
    with pytest.raises(F.FirmwareError, match="!= declared"):
        F.load(jkbms(blob=blob))


def test_an_unparsable_version_is_refused(jkbms):
    with pytest.raises(F.FirmwareError, match="Software version is invalid"):
        F.load(jkbms(version="fifteen"))


def test_an_expired_time_limited_build_is_refused(jkbms):
    built = int(time.time() * 1000) - 10 * 3_600_000
    fw = F.load(jkbms(valid_hours=1, build_ms=built))
    with pytest.raises(F.FirmwareError, match="time-limited"):
        F.check_expiry(fw, int(time.time() * 1000))


def test_a_time_limited_build_inside_its_window_passes(jkbms):
    fw = F.load(jkbms(valid_hours=24))
    F.check_expiry(fw, int(time.time() * 1000))


def test_an_unlimited_build_never_expires(jkbms):
    F.check_expiry(F.load(jkbms()), 0)


def test_the_compatibility_gate_in_the_vendors_own_order(jkbms):
    fw = F.load(jkbms(version="15.42"))

    # A different major version is refused outright, --force or not.
    with pytest.raises(F.FirmwareError, match="major version"):
        F.check_compatible(fw, "JK_PB2A16S20P", "19.02")
    with pytest.raises(F.FirmwareError, match="major version"):
        F.check_compatible(fw, "JK_PB2A16S20P", "19.02", force=True)

    # The same or a newer minor is refused unless forced.
    with pytest.raises(F.FirmwareError, match="minor version"):
        F.check_compatible(fw, "JK_PB2A16S20P", "15.42")

    # The model is checked past the minor gate, and --force never waives it.
    with pytest.raises(F.FirmwareError, match="model"):
        F.check_compatible(fw, "JK_PB1A16S10P", "15.42", force=True)

    # An upgrade of the right model passes.
    F.check_compatible(fw, "JK_PB2A16S20P", "15.41")


def test_an_unreadable_device_version_is_refused(jkbms):
    fw = F.load(jkbms())
    with pytest.raises(F.FirmwareError, match="version could not be read"):
        F.check_compatible(fw, "JK_PB2A16S20P", "")


# --- the transfer -----------------------------------------------------------------------


def test_block_framing_matches_the_vendors_sender():
    image = bytes(range(200))
    assert U.block_count(image) == 2
    first = U.build_block(image, 0)
    assert first[0] == U.SOH and first[1] == 1 and first[2] == 0xFE
    assert first[3:131] == image[:128]
    assert first[131] == sum(first[3:131]) & 0xFF
    # The final short block is padded with 0xFF, not with zeroes.
    assert U.build_block(image, 1)[3:131] == image[128:] + b"\xff" * 56


def test_the_transfer_delivers_the_image_byte_for_byte(pair):
    bus, sim, link = pair
    image = bytes(range(256)) * 3
    U.upgrade(bus, 1, image, start_timeout=2.0, block_timeout=2.0)
    assert bytes(sim.received) == image + b"\xff" * (-len(image) % U.BLOCK)


def test_the_transfer_reports_every_block(pair):
    bus, sim, link = pair
    seen = []

    class Counting(U.Reporter):
        def sending(self, sent, total, elapsed_s):
            seen.append((sent, total))

    image = b"\x5a" * (U.BLOCK * 3)
    U.upgrade(bus, 1, image, report=Counting(), start_timeout=2.0)
    assert [s for s, _ in seen][:3] == [1, 2, 3]
    assert seen[-1] == (3, 3)


def test_the_transfer_gives_up_when_nothing_answers(pair, monkeypatch):
    bus, sim, link = pair
    monkeypatch.setattr(sim, "feed", lambda data: None)  # a silent bootloader
    with pytest.raises(U.UpgradeError, match="no ACK/NAK"):
        U.upgrade(bus, 1, b"\x00" * 128, start_timeout=0.2)


def test_a_cancel_aborts_the_transfer(pair, monkeypatch):
    bus, sim, link = pair
    monkeypatch.setattr(sim, "feed", lambda data: link.rx.append(U.CAN))
    with pytest.raises(U.UpgradeError, match="cancelled"):
        U.upgrade(bus, 1, b"\x00" * 128, start_timeout=1.0)


def test_a_nak_resends_the_same_block(pair, monkeypatch):
    bus, sim, link = pair
    answers = iter([U.NAK, U.NAK, U.ACK, U.ACK])

    def reply(data):
        link.rx.append(next(answers, U.ACK))

    monkeypatch.setattr(sim, "feed", reply)
    U.upgrade(bus, 1, b"\x00" * U.BLOCK, start_timeout=1.0, block_timeout=1.0)
    # The arming write also begins with 0x01 (the slave address), so blocks
    # are picked out by their fixed 132-byte length instead.
    blocks = [f for f in link.sent if len(f) >= 132 and f[0] == U.SOH]
    assert len(blocks) == 2 and blocks[0][:3] == blocks[1][:3]


def test_an_empty_image_is_refused(pair):
    with pytest.raises(U.UpgradeError, match="empty"):
        U.upgrade(pair[0], 1, b"")


# --- the gate as a checklist, and reading a directory ------------------------------


def a_firmware(model="JK_PB2A16S20P", version="15.41"):
    """A parsed image, without going through the container to get one."""
    from jkctl import firmware as F

    major, minor = (int(p) for p in version.split("."))
    return F.Firmware(
        path="fw.jkbms",
        image=b"\x00" * 128,
        version=version,
        major=major,
        minor=minor,
        model=model,
        build_date="May 13 2025",
        build_time="10:00:00",
        device_code=73,
        build_ms=0,
        valid_hours=0,
    )


def test_gate_reports_every_step_not_only_the_first_refusal():
    from jkctl import firmware as F

    fw = a_firmware()
    checks = F.gate(fw, "JK_OTHER_MODEL", "15.41")
    names = [c.name for c in checks]
    assert "major version" in names
    assert "minor version" in names
    assert "model" in names
    # two refusals, both reported: not newer, and the wrong board
    blocking = [c.name for c in checks if c.blocking]
    assert blocking == ["minor version", "model"]


def test_force_waives_the_minor_version_but_never_the_model():
    from jkctl import firmware as F

    fw = a_firmware()
    checks = {c.name: c for c in F.gate(fw, "JK_OTHER", "15.41", force=True)}
    assert checks["minor version"].waived
    assert not checks["minor version"].blocking
    assert checks["model"].blocking


def test_scan_reads_a_tree_and_keeps_a_bad_file_as_a_reason(tmp_path, monkeypatch):
    from jkctl import firmware as F

    good = make_blob(make_payload(model="JK_PB2A16S20P", version="15.41"))
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "good.jkbms").write_bytes(b"\x00" * len(good))
    (tmp_path / "bad.jkbms").write_bytes(b"\x00" * 64)

    def fake_decrypt(key, iv, data):
        if len(data) == len(good):
            return good
        return b"\xff" * len(data)

    monkeypatch.setattr(F.aes, "decrypt_cbc", fake_decrypt)
    found = F.scan(str(tmp_path))
    assert len(found) == 2
    assert found[0].firmware is not None and found[0].firmware.model == "JK_PB2A16S20P"
    assert found[1].firmware is None and found[1].error  # unreadable, sorted last


# --- the repack encoder (build / repack): real AES, no stubs ---------------- #
#
# These go end to end through the actual cipher -- build() encrypts, load()
# decrypts -- so they also exercise aes.encrypt_cbc against the same backend
# load() uses.  The metadata header lives inside the image at 0x200, so an
# image for build() is a payload without its 12-byte trailer.


def make_image(**kwargs) -> bytes:
    """A valid image (payload minus trailer) carrying a metadata header."""
    return make_payload(**kwargs)[: -F.TRAILER]


def test_build_round_trips_through_real_aes(tmp_path):
    image = make_image(version="15.41", model="JK_PB2A16S20P", device_code=73)
    path = tmp_path / "built.jkbms"
    path.write_bytes(F.build(image, build_ms=1_700_000_000_000, valid_hours=0))
    fw = F.load(str(path))
    assert fw.image == image
    assert (fw.model, fw.version, fw.device_code) == ("JK_PB2A16S20P", "15.41", 73)
    assert (fw.build_ms, fw.valid_hours) == (1_700_000_000_000, 0)


def test_build_output_is_a_block_multiple_and_reloads(tmp_path):
    blob = F.build(make_image())
    assert len(blob) % F.AES_BLOCK == 0
    path = tmp_path / "b.jkbms"
    path.write_bytes(blob)
    assert F.load(str(path)).image == make_image()


def test_build_refuses_an_image_too_small_for_a_header():
    with pytest.raises(F.FirmwareError, match="metadata header"):
        F.build(b"\x00" * (F.DEVICE_CODE_OFF))  # one field short


def test_repack_bumps_the_version_and_leaves_the_rest(tmp_path):
    src = tmp_path / "src.jkbms"
    src.write_bytes(F.build(make_image(version="15.41")))
    fw = F.load(str(src))

    out = tmp_path / "out.jkbms"
    out.write_bytes(F.repack(fw, version="15.99"))
    bumped = F.load(str(out))

    assert (bumped.major, bumped.minor) == (15, 99)
    # everything but the 16-byte version field at 0x200 is unchanged
    a, b = bytearray(fw.image), bytearray(bumped.image)
    a[0x200:0x210] = b[0x200:0x210] = b"\x00" * 16
    assert bytes(a) == bytes(b)


def test_repack_swaps_the_image(tmp_path):
    src = tmp_path / "src.jkbms"
    src.write_bytes(F.build(make_image()))
    fw = F.load(str(src))

    patched = make_image(model="JK_PB2A16S20P")
    patched = patched[:0x300] + b"\xa5" + patched[0x301:]  # a one-byte "patch"
    out = tmp_path / "out.jkbms"
    out.write_bytes(F.repack(fw, image=patched))
    assert F.load(str(out)).image == patched


def _valid_hours_of(blob: bytes, tmp_path) -> int:
    path = tmp_path / "vh.jkbms"
    path.write_bytes(blob)
    return F.load(str(path)).valid_hours


def test_repack_keeps_source_expiry_and_can_override_it(tmp_path):
    src = tmp_path / "src.jkbms"
    src.write_bytes(F.build(make_image(), build_ms=1_700_000_000_000, valid_hours=24))
    fw = F.load(str(src))
    assert _valid_hours_of(F.repack(fw), tmp_path) == 24  # kept by default
    assert _valid_hours_of(F.repack(fw, valid_hours=0), tmp_path) == 0  # overridden


def test_repack_rejects_a_bad_version(tmp_path):
    src = tmp_path / "src.jkbms"
    src.write_bytes(F.build(make_image()))
    fw = F.load(str(src))
    with pytest.raises(F.FirmwareError, match="major.minor"):
        F.repack(fw, version="15")


def test_set_header_field_rejects_an_overlong_value():
    with pytest.raises(F.FirmwareError, match="too long"):
        F.set_header_field(make_image(), F.HDR_BASE, "x" * 16)
