"""The target-specific flash-dump frame and host-side reassembly."""

from __future__ import annotations

import hashlib
import struct

import pytest
from conftest import make_pair

from jkctl import flashdump as D


def test_full_flash_is_reassembled_from_the_patched_simulator(monkeypatch):
    flash = bytes(range(256)) * (128 * 1024 // 256)
    bus, sim, link = make_pair(flash_image=flash)

    def read_now(_timeout, max_bytes=4096):
        return bus.ser.read(min(bus.ser.in_waiting, max_bytes))

    monkeypatch.setattr(bus, "read_raw", read_now)
    try:
        assert D.dump_flash(bus, 1, timeout=0.1) == flash
    finally:
        bus.close()
    assert sim.actions[0] == (D.DUMP_SLOT, 0)
    assert sim.actions[-1] == (D.DUMP_SLOT, 511)
    assert link.sent[1][7:9] == b"\x01\x00"


def test_a_corrupt_dump_frame_is_refused():
    frame = bytearray(D.encode_frame(bytes(128 * 1024), 0))
    frame[100] ^= 1
    with pytest.raises(D.FlashDumpError, match="checksum|CRC32"):
        D.decode_frame(bytes(frame), 0)


def test_vector_sanity_checks_both_images():
    target = next(target for target in D.TARGETS.values() if target.version == "15.10")
    image = bytearray(128 * 1024)
    struct.pack_into("<II", image, 0, 0x20001000, 0x08000101)
    struct.pack_into("<II", image, D.APP_BASE - D.FLASH_BASE, target.sp, target.reset)
    assert D.vector_sanity(bytes(image), target.model, target.version) == []

    struct.pack_into("<I", image, 4, 0x08002001)
    assert D.vector_sanity(bytes(image), target.model, target.version) == [
        "bootloader reset vector is outside pre-app flash: 0x08002001"
    ]


@pytest.mark.parametrize(
    ("layout_name", "size", "buffer_load", "length_store"),
    (
        ("14.03", 67783, bytes.fromhex("65 68"), bytes.fromhex("20 80")),
        ("15.41", 92152, bytes.fromhex("65 68"), bytes.fromhex("20 80")),
        ("19.21-b2a8s20p", 100017, bytes.fromhex("a5 68"), bytes.fromhex("60 80")),
    ),
)
def test_patcher_handles_each_table_and_response_abi(
    monkeypatch, layout_name, size, buffer_load, length_store
):
    layout = D._LAYOUTS[layout_name]
    image = bytearray(b"\xff" * size)
    start = layout.dispatch_addr - D.APP_BASE
    image[start : start + 4] = D._DISPATCH_PROLOGUE
    for address in layout.lookup_limit_addrs:
        start = address - D.APP_BASE
        image[start : start + 2] = layout.lookup_limit
    if layout.table_pointer_addr is not None:
        struct.pack_into(
            "<I",
            image,
            layout.table_pointer_addr - D.APP_BASE,
            layout.table_addr,
        )
    for reference in layout.table_references:
        start = reference.address - D.APP_BASE
        image[start : start + 4] = reference.expected
    table_start = layout.table_addr - D.APP_BASE
    table_size = layout.table_entries * 4
    image[table_start : table_start + table_size] = bytes(table_size)

    digest = hashlib.sha256(image).hexdigest()
    target = D.FirmwareTarget(
        "TEST", "15.00", size, digest, 0x20001000, 0x08010001, layout_name
    )
    monkeypatch.setitem(D.TARGETS, digest, target)
    patched = D.patch_image(bytes(image))

    hook_offset = D._hook_address(target) - D.APP_BASE
    buffer_start = hook_offset + D._RESPONSE_BUFFER_LOAD_OFFSET
    length_start = hook_offset + D._RESPONSE_LENGTH_STORE_OFFSET
    assert patched[buffer_start : buffer_start + 2] == buffer_load
    assert patched[length_start : length_start + 2] == length_store

    table_address = D._table_address(target)
    relocated = table_address - D.APP_BASE
    assert patched[relocated : relocated + table_size] == bytes(table_size)
    assert struct.unpack_from("<HBB", patched, relocated + table_size) == (
        0x162E,
        2,
        D.DUMP_COMMAND,
    )


def test_supported_device_matrix_is_exact():
    assert D.supports_device("JK_PB2A16S20P", "14.03")
    assert D.supports_device("JK_PB2A16S20P", "15.32")
    assert D.supports_device("JK_B2A8S30P", "17.06")
    assert D.supports_device("JK-B2A8S20P", "19.21")
    assert not D.supports_device("JK_PB1A16S10P", "15.32")
    assert not D.supports_device("JK_PB2A16S20P", "15.42")


def test_patcher_rejects_every_unrecognised_image():
    with pytest.raises(D.FlashDumpError, match="exact audited vendor images"):
        D.patch_image(b"not audited firmware")
