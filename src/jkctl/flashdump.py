"""Full-flash dumper for every exact firmware image in the local JK archive.

The patcher accepts only audited vendor-image SHA-256 values. It adds action
register 0x162e without replacing a stock action, hooks the stock command
dispatcher, and returns one 256-byte flash block per request. The application
regains control between every block.

The V15.41 patch has run successfully on one physical JK_PB2A16S20P and
returned a validated 128 KiB capture. That result does not establish hardware
support for any other allowlisted image.
"""

from __future__ import annotations

import hashlib
import struct
import time
import zlib
from dataclasses import dataclass

from devicectl.report import SILENT, Reporter

from jkctl import modbus as M
from jkctl.errors import JkError

FLASH_BASE = 0x08000000
APP_BASE = 0x08002000
PERSISTENT_BASE = 0x08001800
SRAM_BASE = 0x20000000
SRAM_END = 0x20010000
MIN_FLASH_SIZE = 128 * 1024

DUMP_SLOT = 0x2E
DUMP_COMMAND = 0x70
BLOCK_SIZE = 256
FRAME_SIZE = 300
FRAME_MAGIC = b"\x55\xaa\xeb\x90"
FRAME_TYPE = 0xD0
FRAME_VERSION = 1
CRC_OFFSET = 276
CHECKSUM_OFFSET = FRAME_SIZE - 1
SUPPORTED_FLASH_SIZES = frozenset((MIN_FLASH_SIZE, 256 * 1024))

_DISPATCH_PROLOGUE = bytes.fromhex("2d e9 f1 4f")
_INJECTION_ALIGNMENT = 0x100
_TABLE_DELTA = 0x400
_LOW_REGISTER_MAX = 7
_THUMB_WORD_OFFSET_MAX = 0x1F * 4
_THUMB_HALFWORD_OFFSET_MAX = 0x1F * 2


@dataclass(frozen=True)
class TableReference:
    """One PC-relative table materialization in legacy compiler output."""

    address: int
    register: int
    expected: bytes


@dataclass(frozen=True)
class FirmwareLayout:
    """Patch points shared by byte-compatible builds."""

    dispatch_addr: int
    lookup_limit_addrs: tuple[int, int]
    lookup_limit: bytes
    table_addr: int
    table_entries: int
    table_pointer_addr: int | None = None
    table_references: tuple[TableReference, ...] = ()
    response_buffer_offset: int = 4
    response_length_offset: int = 0


@dataclass(frozen=True)
class FirmwareTarget:
    """An exact vendor application accepted by the patcher."""

    model: str
    version: str
    image_size: int
    sha256: str
    sp: int
    reset: int
    layout: str


_DIRECT_REFERENCE_TEMPLATES = {
    3: (
        (3, bytes.fromhex("0f f2 b0 13")),
        (1, bytes.fromhex("0f f2 a0 11")),
        (2, bytes.fromhex("0f f2 54 12")),
    ),
    4: (
        (3, bytes.fromhex("0f f2 88 23")),
        (1, bytes.fromhex("0f f2 74 21")),
        (2, bytes.fromhex("0f f2 5c 22")),
        (0, bytes.fromhex("0f f2 f8 10")),
    ),
}


def _layout(
    dispatch: int,
    limits: tuple[int, int],
    limit: str,
    table: int,
    entries: int,
    *,
    pointer: int | None = None,
    references: tuple[int, ...] = (),
    response: tuple[int, int] = (4, 0),
) -> FirmwareLayout:
    """Build one explicit audited layout from compact declarative data."""
    templates = _DIRECT_REFERENCE_TEMPLATES[len(references)] if references else ()
    table_references = tuple(
        TableReference(address, register, expected)
        for address, (register, expected) in zip(references, templates, strict=True)
    )
    return FirmwareLayout(
        dispatch,
        limits,
        bytes.fromhex(limit),
        table,
        entries,
        pointer,
        table_references,
        *response,
    )


_LAYOUTS = {
    "14.03": _layout(
        0x08002684,
        (0x0800F43C, 0x0800F44C),
        "62 29",
        0x0800F5F4,
        0x62,
        references=(
            0x0800F440,
            0x0800F450,
            0x0800F49E,
        ),
    ),
    "14.07-pb1": _layout(
        0x08002684,
        (0x08010684, 0x08010694),
        "62 29",
        0x0801083C,
        0x62,
        references=(
            0x08010688,
            0x08010698,
            0x080106E6,
        ),
    ),
    "14.07-pb2": _layout(
        0x08002684,
        (0x08010688, 0x08010698),
        "62 29",
        0x08010840,
        0x62,
        references=(
            0x0801068C,
            0x0801069C,
            0x080106EA,
        ),
    ),
    "14.10-pb1": _layout(
        0x08002684,
        (0x08010DD8, 0x08010DE8),
        "62 29",
        0x08010F90,
        0x62,
        references=(
            0x08010DDC,
            0x08010DEC,
            0x08010E3A,
        ),
    ),
    "14.10-pb2": _layout(
        0x08002684,
        (0x08010DE0, 0x08010DF0),
        "62 29",
        0x08010F98,
        0x62,
        references=(
            0x08010DE4,
            0x08010DF4,
            0x08010E42,
        ),
    ),
    "14.12": _layout(
        0x080026F8,
        (0x08011B60, 0x08011B70),
        "62 29",
        0x08011D18,
        0x62,
        references=(
            0x08011B64,
            0x08011B74,
            0x08011BC2,
        ),
    ),
    "14.13": _layout(
        0x080026F8,
        (0x08011AEC, 0x08011AFC),
        "62 29",
        0x08011CA4,
        0x62,
        references=(
            0x08011AF0,
            0x08011B00,
            0x08011B4E,
        ),
    ),
    "14.14": _layout(
        0x080026F8,
        (0x08011B6C, 0x08011B7C),
        "62 29",
        0x08011D24,
        0x62,
        references=(
            0x08011B70,
            0x08011B80,
            0x08011BCE,
        ),
    ),
    "14.15": _layout(
        0x080026F8,
        (0x08011FC8, 0x08011FD8),
        "62 29",
        0x08012180,
        0x62,
        references=(
            0x08011FCC,
            0x08011FDC,
            0x0801202A,
        ),
    ),
    "14.17-pb1": _layout(
        0x080026F8,
        (0x0800DED2, 0x0800DEE2),
        "6c 29",
        0x0800E160,
        0x6C,
        references=(
            0x0800DED6,
            0x0800DEEA,
            0x0800DF02,
            0x0800DF64,
        ),
    ),
    "14.17-pb2": _layout(
        0x080026F8,
        (0x0800DED6, 0x0800DEE6),
        "6c 29",
        0x0800E164,
        0x6C,
        references=(
            0x0800DEDA,
            0x0800DEEE,
            0x0800DF06,
            0x0800DF68,
        ),
    ),
    "14.19-pb1": _layout(
        0x080026F8,
        (0x0800DEBE, 0x0800DECE),
        "6c 29",
        0x0800E14C,
        0x6C,
        references=(
            0x0800DEC2,
            0x0800DED6,
            0x0800DEEE,
            0x0800DF50,
        ),
    ),
    "14.19-pb2": _layout(
        0x080026F8,
        (0x0800DEC2, 0x0800DED2),
        "6c 29",
        0x0800E150,
        0x6C,
        references=(
            0x0800DEC6,
            0x0800DEDA,
            0x0800DEF2,
            0x0800DF54,
        ),
    ),
    "14.20-pb1": _layout(
        0x080026FC,
        (0x0800DF1A, 0x0800DF2A),
        "6c 29",
        0x0800E1A8,
        0x6C,
        references=(
            0x0800DF1E,
            0x0800DF32,
            0x0800DF4A,
            0x0800DFAC,
        ),
    ),
    "14.20-pb2": _layout(
        0x080026FC,
        (0x0800DF1E, 0x0800DF2E),
        "6c 29",
        0x0800E1AC,
        0x6C,
        references=(
            0x0800DF22,
            0x0800DF36,
            0x0800DF4E,
            0x0800DFB0,
        ),
    ),
    "15.10-pb1": _layout(
        0x080026FC,
        (0x0800DE9E, 0x0800DEAE),
        "6c 29",
        0x0800E12C,
        0x6C,
        references=(
            0x0800DEA2,
            0x0800DEB6,
            0x0800DECE,
            0x0800DF30,
        ),
    ),
    "15.10-pb2": _layout(
        0x080026FC,
        (0x0800DEA2, 0x0800DEB2),
        "6c 29",
        0x0800E130,
        0x6C,
        references=(
            0x0800DEA6,
            0x0800DEBA,
            0x0800DED2,
            0x0800DF34,
        ),
    ),
    "15.11-pb1": _layout(
        0x080026FC,
        (0x0800DF2A, 0x0800DF3A),
        "6c 29",
        0x0800E1B8,
        0x6C,
        references=(
            0x0800DF2E,
            0x0800DF42,
            0x0800DF5A,
            0x0800DFBC,
        ),
    ),
    "15.11-pb2": _layout(
        0x080026FC,
        (0x0800DF2E, 0x0800DF3E),
        "6c 29",
        0x0800E1BC,
        0x6C,
        references=(
            0x0800DF32,
            0x0800DF46,
            0x0800DF5E,
            0x0800DFC0,
        ),
    ),
    "15.17": _layout(
        0x0800248C,
        (0x0800DD40, 0x0800DD4E),
        "6c 2c",
        0x08015C44,
        0x6C,
        pointer=0x0800E000,
    ),
    "15.32": _layout(
        0x0800248C,
        (0x0800E64C, 0x0800E65A),
        "6c 2c",
        0x08017044,
        0x6C,
        pointer=0x0800E90C,
    ),
    "15.41": _layout(
        0x0800248C,
        (0x0800EAA0, 0x0800EAAE),
        "6e 2e",
        0x080176D0,
        0x6E,
        pointer=0x0800EDCC,
    ),
    "17.06-b2a8s30p": _layout(
        0x08002460,
        (0x0800E862, 0x0800E870),
        "70 2c",
        0x0801710C,
        0x70,
        pointer=0x0800EB78,
        response=(8, 2),
    ),
    "19.02-15a": _layout(
        0x08002490,
        (0x0800FF8A, 0x0800FF98),
        "70 2c",
        0x08018CF0,
        0x70,
        pointer=0x080102A0,
        response=(8, 2),
    ),
    "19.02-pb1a10": _layout(
        0x08002490,
        (0x0800FF52, 0x0800FF60),
        "70 2c",
        0x08018CBC,
        0x70,
        pointer=0x08010268,
        response=(8, 2),
    ),
    "19.02-pb2a20": _layout(
        0x08002490,
        (0x0800FF7A, 0x0800FF88),
        "70 2c",
        0x08018CE4,
        0x70,
        pointer=0x08010290,
        response=(8, 2),
    ),
    "19.04-b2a24s20p": _layout(
        0x08002460,
        (0x0800EE3C, 0x0800EE4C),
        "71 2e",
        0x08017870,
        0x71,
        pointer=0x0800F1B0,
        response=(8, 2),
    ),
    "19.04-b2a8s30p": _layout(
        0x08002460,
        (0x0800EE80, 0x0800EE90),
        "71 2e",
        0x080178B0,
        0x71,
        pointer=0x0800F1F4,
        response=(8, 2),
    ),
    "19.04-bd6a24s8p": _layout(
        0x08002460,
        (0x0800EE70, 0x0800EE80),
        "71 2e",
        0x080178A0,
        0x71,
        pointer=0x0800F1E4,
        response=(8, 2),
    ),
    "19.04-pb2a20": _layout(
        0x08002490,
        (0x080100A8, 0x080100B8),
        "71 2e",
        0x08018E74,
        0x71,
        pointer=0x0801041C,
        response=(8, 2),
    ),
    "19.20-b2a8s20p": _layout(
        0x08002960,
        (0x0801084E, 0x0801085E),
        "73 2e",
        0x080197C8,
        0x73,
        pointer=0x08010C00,
        response=(8, 2),
    ),
    "19.20-b2a8s30p": _layout(
        0x08002960,
        (0x08010302, 0x08010312),
        "73 2e",
        0x08016A08,
        0x73,
        pointer=0x080106B4,
        response=(8, 2),
    ),
    "19.21-b2a8s20p": _layout(
        0x08002960,
        (0x080107CA, 0x080107DA),
        "73 2e",
        0x08019990,
        0x73,
        pointer=0x08010C00,
        response=(8, 2),
    ),
}


def _targets(
    layout: str,
    version: str,
    size: int,
    sp: int,
    reset: int,
    *images: tuple[str, str],
) -> tuple[FirmwareTarget, ...]:
    """Build exact-image targets that share one audited binary layout."""
    return tuple(
        FirmwareTarget(model, version, size, digest, sp, reset, layout)
        for model, digest in images
    )


_TARGET_LIST = (
    *_targets(
        "14.03",
        "14.03",
        67783,
        0x20000DF8,
        0x080127A5,
        (
            "JK_PB2A16S15P",
            "1b3e5f09ca818a13eca273a2d861500048a3c117de948f2bb250fa6d2c9462be",
        ),
        (
            "JK_PB2A16S20P",
            "bc0a55179da400e2c58dbdf7e72f3fb4a1c39865e1faad91948fd6e4835407f8",
        ),
    ),
    *_targets(
        "14.07-pb1",
        "14.07",
        73327,
        0x20000DF8,
        0x08013D45,
        (
            "JK_PB1A16S10P",
            "d8001933ea9e15211303add082bc4e1e14f636e3fd744a8d262ef9753760fa2a",
        ),
        (
            "JK_PB1A16S15P",
            "e74397a073d618dd276c2918d2bacd32f4beef774b8a693a4ff2009cf9ca816c",
        ),
    ),
    *_targets(
        "14.07-pb2",
        "14.07",
        73331,
        0x20000DF8,
        0x08013D49,
        (
            "JK_PB2A16S15P",
            "c6cb8ffb032bb00e9b575a5daf530b5b432cd9f6dc5624eac21e146aa0cb1d31",
        ),
        (
            "JK_PB2A16S20P",
            "add415bfe1fc44863c2b9cf4e15a65889614a0670ceac8eac1b51102b9592cde",
        ),
    ),
    *_targets(
        "14.10-pb1",
        "14.10",
        74035,
        0x20000DF8,
        0x08014009,
        (
            "JK_PB1A16S10P",
            "c321ddf63973da1406692c94a60ad749a62c0fdb50bacf9138aafa175a57b69b",
        ),
        (
            "JK_PB1A16S15P",
            "3221abbc02bfce07ba6424df26e33111fa11129a2935a7b637fb91e5e994be0b",
        ),
    ),
    *_targets(
        "14.10-pb2",
        "14.10",
        74043,
        0x20000DF8,
        0x08014011,
        (
            "JK_PB2A16S15P",
            "1d8080dbcfe347272b84ed28c969869914a75dd0ac9fb51ade2956168bb29270",
        ),
        (
            "JK_PB2A16S20P",
            "1b23666adff5a3f62090c095567ca2e3f221ad17165ed9857043602165b05fb5",
        ),
    ),
    *_targets(
        "14.12",
        "14.12",
        78955,
        0x20000DF8,
        0x08015341,
        (
            "JK_PB1A16S10P",
            "4e651e75d822590ef546d5a5d908ee7a75315e76f1bd77ad4ffa6ae1131d31a8",
        ),
        (
            "JK_PB1A16S15P",
            "f438c7b19dd00d4e54a86e883a16de06a1ebee0bd39fe7fba75be32195b3c7d0",
        ),
        (
            "JK_PB2A16S15P",
            "bd5407dfe3a68235bc116855dff7f040021f644debed956f5efbaa77fc37a58a",
        ),
        (
            "JK_PB2A16S20P",
            "5142708eeb397b1c8fcca45664f4d2f4f9a5cf3c6f6a2f6c2b967ecd1c544887",
        ),
    ),
    *_targets(
        "14.13",
        "14.13",
        78839,
        0x20000DF8,
        0x080152CD,
        (
            "JK_PB1A16S10P",
            "eaae91cffce26139f6aa577f9603844aabc4cf58f48f672bb881e8ea75c3f15e",
        ),
        (
            "JK_PB1A16S15P",
            "fa8d87111161d8e83c361b4ea41cadd881330cefc42257f09dc5aa9f3a8a2880",
        ),
        (
            "JK_PB2A16S15P",
            "c60b19fa9011fe00d1828d7a17ebe401ce9355eafdd459ecdea389e3e0e8acbb",
        ),
        (
            "JK_PB2A16S20P",
            "753e792f632c210eba24a35b0092c165eaab7ac619a5e199d027bdd181328ffb",
        ),
    ),
    *_targets(
        "14.14",
        "14.14",
        78967,
        0x20000DF8,
        0x0801534D,
        (
            "JK_PB1A16S10P",
            "b0339fbc6f2317562152e1bded625c00de0ade02ad11f170baf4133884907376",
        ),
        (
            "JK_PB1A16S15P",
            "3577a6755ffdbe6f57967ab5661f98cdf7fe7ba63114b7366003f5d6564c8e41",
        ),
        (
            "JK_PB2A16S15P",
            "c2686c384b8cb1353419c79ffa80442c45e5370a36e518199594ef14f1548fac",
        ),
        (
            "JK_PB2A16S20P",
            "60e8889dbf8a15c822b9927d3ac4876eb8857567f98319a5d97c3a811a78f9af",
        ),
    ),
    *_targets(
        "14.15",
        "14.15",
        80043,
        0x20000DF8,
        0x08015781,
        (
            "JK_PB1A16S10P",
            "2fc2b2656d303ff04309a93bf6a2177e062a8a5627f200e743589710d9f28f8a",
        ),
        (
            "JK_PB1A16S15P",
            "a1a47d8e99a8e63a020a72e9fcc37c5abf3005e964f2e70427a55979da0a95b1",
        ),
        (
            "JK_PB2A16S15P",
            "12e9b36aa75f9e712816a591edcbd44b05c9e37f0a5b588288701530b20df3f4",
        ),
        (
            "JK_PB2A16S20P",
            "b915eac8565d37992ef766cfa3d18cb614920237fb18e78962d474807dc6dbf5",
        ),
    ),
    *_targets(
        "14.17-pb1",
        "14.17",
        81687,
        0x20000E00,
        0x08015DED,
        (
            "JK_PB1A16S10P",
            "4f32b24b8b9111f93c26d1e3bc3800e5ce0d4f0db134545133fa357ce3ba0b36",
        ),
        (
            "JK_PB1A16S15P",
            "2bde4848c88de821001037883e33367dbae4260b5755d1ab43138dd14e0b98bf",
        ),
    ),
    *_targets(
        "14.17-pb2",
        "14.17",
        81691,
        0x20000E00,
        0x08015DF1,
        (
            "JK_PB2A16S15P",
            "c552d2db04798c74e9ac2b5ffdd8225e6494c588737009afdd8ab1baaf3a95fa",
        ),
        (
            "JK_PB2A16S20P",
            "297af14b0d50ed3650de434f906fe8f94cb60cc4d01431f86a5ac2e66aae6b87",
        ),
    ),
    *_targets(
        "14.19-pb1",
        "14.19",
        81667,
        0x20000E00,
        0x08015DD9,
        (
            "JK_PB1A16S10P",
            "6f220c7aa0879a1de67f13f36f11a21d569cc718df749c0059f69f960808480d",
        ),
        (
            "JK_PB1A16S15P",
            "1b8c71b97f801fbc8f45980bbe4c4ba336811ab892bfc9a2744461461d62c0e5",
        ),
    ),
    *_targets(
        "14.19-pb2",
        "14.19",
        81671,
        0x20000E00,
        0x08015DDD,
        (
            "JK_PB2A16S15P",
            "2a17e78cbc2ad755fc0fa830dd21943929194672735a0ea341ae2ff5872fbb9d",
        ),
        (
            "JK_PB2A16S20P",
            "6fabd107b1ad7aef87705cfc6f6cd5559ec629d0f5cdc3c46a62a6354e1a66a1",
        ),
    ),
    *_targets(
        "14.20-pb1",
        "14.20",
        81759,
        0x20000E00,
        0x08015E35,
        (
            "JK_PB1A16S10P",
            "a2a9507cf1b476fa910a1d255b55998d0d308f45b2cc89c0a45fb75e506fafa6",
        ),
        (
            "JK_PB1A16S15P",
            "020db999eafb34eedbf064bb693fc05b07a87fd47d06fc6e2f7d22d056b4f416",
        ),
    ),
    *_targets(
        "14.20-pb2",
        "14.20",
        81763,
        0x20000E00,
        0x08015E39,
        (
            "JK_PB2A16S15P",
            "45b93258abe21fdaa5419643595744fc01a86c894d0d03d5144976768bd4047d",
        ),
        (
            "JK_PB2A16S20P",
            "893e0d201d15783def8fb56a42a1b8cdf74c6ee94836a16aa4d0c2af2cef5491",
        ),
    ),
    *_targets(
        "15.10-pb1",
        "15.10",
        81583,
        0x20000E00,
        0x08015D85,
        (
            "JK_PB1A16S10P",
            "89399d01be5a5692ee02ca275bff5d716e1a58321861c064fbafca101fe063a9",
        ),
        (
            "JK_PB1A16S15P",
            "3c3f97e28002ffacf51b9671c6599caf5f8404a3d1843319249cafa31907d605",
        ),
    ),
    *_targets(
        "15.10-pb2",
        "15.10",
        81587,
        0x20000E00,
        0x08015D89,
        (
            "JK_PB2A16S15P",
            "9be0333a9d4ac4eb2192ad01329e334fef036ff5e82e6c20313761ef841f4a7c",
        ),
        (
            "JK_PB2A16S20P",
            "a7877c69239f54bb4f17516f3ce87a79dd6bf7d69d341011c95e098b52604229",
        ),
    ),
    *_targets(
        "15.11-pb1",
        "15.11",
        83250,
        0x20000E08,
        0x08016405,
        (
            "JK_PB1A16S10P",
            "387bb6c8dde96982373f9da2689116a31285c90b92a2998c190aa3fa323c9ada",
        ),
        (
            "JK_PB1A16S15P",
            "b4421b5dea8c148b7f47d728af71ab956416af40cfa34bee53b998079887d64e",
        ),
    ),
    *_targets(
        "15.11-pb2",
        "15.11",
        83254,
        0x20000E08,
        0x08016409,
        (
            "JK_PB2A16S15P",
            "a63939026f12ad9966ccd8271512e326caee1fc58d08fdd6e8bc803880773f84",
        ),
        (
            "JK_PB2A16S20P",
            "915776130c6815c27718595093fd73a5abc74eb0783f2f5d6deaf560da308f71",
        ),
    ),
    *_targets(
        "15.17",
        "15.17",
        85349,
        0x20004BE8,
        0x08016C41,
        (
            "JK_PB1A16S10P",
            "54c5194da514afe2cbb528c1eb43543c974fa9df615c7d7a59a56d6e18e4d4f9",
        ),
        (
            "JK_PB1A16S15P",
            "95cffe175699e6451669072ff869a1168d71d259a83cff994fd62129a9cde0c5",
        ),
        (
            "JK_PB2A16S15P",
            "ea9992fdc06e495c28d64f91eacc0838c95f4be8244b70c286f0ff1c1428a816",
        ),
        (
            "JK_PB2A16S20P",
            "41dbcfaf7c64675dbadaaacd5f2bb16b34ebbf0468a1ab7e36873f1bc6b59198",
        ),
    ),
    *_targets(
        "15.32",
        "15.32",
        90467,
        0x20004E18,
        0x08017FD9,
        (
            "JK_PB2A16S20P",
            "fdef7dd8ff200c2876f196364c495e045f401ad785766a820b54acbcde216dd4",
        ),
    ),
    *_targets(
        "15.41",
        "15.41",
        92152,
        0x20004E00,
        0x0801866D,
        (
            "JK_PB1A16S10P",
            "370df16995871bbdad5279eed738370509f30d425ed2c4e44a34f57968601a3d",
        ),
        (
            "JK_PB1A16S15P",
            "ff44351439c9856f9083fa506c128c53578c4213a88a8659a233cab47fd15f97",
        ),
        (
            "JK_PB2A16S15P",
            "e4a709d743d7cbc43d168d7b97e6722bacc668fd327da9cca537deacfe5a7e94",
        ),
        (
            "JK_PB2A16S20P",
            "68f7cbc0bb25b74cf2d556cdfab6c3f8049c05cd5fa5d59de3083cdd4d121b06",
        ),
    ),
    *_targets(
        "17.06-b2a8s30p",
        "17.06",
        90551,
        0x20004E98,
        0x0801805D,
        (
            "JK_B2A8S30P",
            "dcdae2bc725e4eaf3177dcd2fe90ba38937eb56b1e17e14ede1bf0cb8426d59b",
        ),
    ),
    *_targets(
        "19.02-15a",
        "19.02",
        98143,
        0x20005738,
        0x08019DAD,
        (
            "JK_PB1A16S15P",
            "49ef083ad02cb955c3460bf1f6ec3924860269a7f08a5d5945e422d76ce34943",
        ),
        (
            "JK_PB2A16S15P",
            "ef2a4f5a2defe707aaa11fcceecc63a0efce4ab175858fe8aa043153181350a0",
        ),
    ),
    *_targets(
        "19.02-pb1a10",
        "19.02",
        98091,
        0x20005738,
        0x08019D79,
        (
            "JK_PB1A16S10P",
            "6b0ef3d1dea6505ff55cb9f21d4321f25dfa1e2b4272a684e4bdaf5dbe295eaa",
        ),
    ),
    *_targets(
        "19.02-pb2a20",
        "19.02",
        98131,
        0x20005738,
        0x08019DA1,
        (
            "JK_PB2A16S20P",
            "964aeca39c5273219a75dd376ff234b62497f4937407bd0d7055af39587af4a4",
        ),
    ),
    *_targets(
        "19.04-b2a24s20p",
        "19.04",
        92791,
        0x20004EB8,
        0x0801891D,
        (
            "JK_B2A24S20P",
            "cc706c1a02b7c01e86b3946256fa89427479c36e3037d42763cd8b6cd353e81e",
        ),
    ),
    *_targets(
        "19.04-b2a8s30p",
        "19.04",
        92855,
        0x20004EB8,
        0x0801895D,
        (
            "JK_B2A8S30P",
            "56f06d7f0df1a44e7697b3a4f6ce7fda449f7cc4de3695f9e7cfb300c380b8cb",
        ),
    ),
    *_targets(
        "19.04-bd6a24s8p",
        "19.04",
        92839,
        0x20004EB8,
        0x0801894D,
        (
            "JK_BD6A24S8P",
            "3a0f7aa4c423dfd3c7ca71068be88a5467fb3a476595d8b20273eebca28e2bb1",
        ),
    ),
    *_targets(
        "19.04-pb2a20",
        "19.04",
        98535,
        0x20005738,
        0x08019F35,
        (
            "JK_PB2A16S20P",
            "04a619913064c30a2f97d44349876e89e501c5a7798d28fd092b392dc663d7f8",
        ),
    ),
    *_targets(
        "19.20-b2a8s20p",
        "19.20",
        99553,
        0x20004F20,
        0x0801A389,
        (
            "JK_B2A8S20P",
            "aa62d7896d7906c2fbacde5de5a9833ab443d138e2914a922311da512e7d1af0",
        ),
    ),
    *_targets(
        "19.20-b2a8s30p",
        "19.20",
        87625,
        0x20004EC0,
        0x08017509,
        (
            "JK-B2A8S30P",
            "bbbc3b924ebfc940103f2808493416674292991fb5e5841b229531c9df0f210b",
        ),
    ),
    *_targets(
        "19.21-b2a8s20p",
        "19.21",
        100017,
        0x20004C98,
        0x0801A551,
        (
            "JK-B2A8S20P",
            "e513e6d36ad0a9ad8dbb53f43405c7046a9b6e7bd601cb91fe6a0a80018bce70",
        ),
    ),
)
TARGETS = {target.sha256: target for target in _TARGET_LIST}
SUPPORTED_DEVICES = frozenset((target.model, target.version) for target in _TARGET_LIST)

# Assembled for the legacy response-object ABI at an address aligned to 0x100.
# The stock-return branch and two response-object offsets are rewritten per layout.
DUMPER_ASM = r"""
.thumb
cmp r0, #0x70
beq dump
push.w {r0, r4-r11, lr}
b.w DISPATCH_CONTINUE
dump:
ldr r3, [sp, #8]
ldr r12, [sp, #12]
push.w {r4-r8, lr}
mov r4, r3
mov r8, r12
ldrh r7, [r1]
ldr r5, [r4, #4]
movs r0, #0
movs r1, #75
mov r2, r5
zero_loop:
str r0, [r2], #4
subs r1, #1
bne zero_loop
ldr r0, =0x90ebaa55
str r0, [r5, #0]
movs r0, #0xd0
strb r0, [r5, #4]
movs r0, #1
strb r0, [r5, #5]
lsls r6, r7, #8
ldr r0, =0x08000000
adds r6, r6, r0
str r6, [r5, #8]
ldr r0, =0x1ffff7e0
ldrh r0, [r0]
lsls r0, r0, #10
str r0, [r5, #12]
strh r7, [r5, #18]
ldr r1, =0x08000000
subs r2, r6, r1
cmp r2, r0
bhs out_of_range
movw r1, #256
strh r1, [r5, #16]
add.w r2, r5, #20
movs r3, #64
copy_loop:
ldr r1, [r6], #4
str r1, [r2], #4
subs r3, #1
bne copy_loop
b crc
out_of_range:
movs r1, #1
strb r1, [r5, #6]
crc:
mvn r0, #0
mov r1, r5
movw r2, #276
ldr r6, =0xedb88320
crc_byte:
ldrb r3, [r1], #1
eors r0, r3
movs r3, #8
crc_bit:
lsrs r0, r0, #1
bcc crc_no_xor
eors r0, r6
crc_no_xor:
subs r3, #1
bne crc_bit
subs r2, #1
bne crc_byte
mvns r0, r0
str r0, [r5, #276]
movs r0, #0
mov r1, r5
movw r2, #299
sum_loop:
ldrb r3, [r1], #1
adds r0, r0, r3
subs r2, #1
bne sum_loop
strb r0, [r5, #299]
movw r0, #300
strh r0, [r4]
movs r0, #0
blx r8
movs r0, #1
pop.w {r4-r8, pc}
.ltorg
"""

_DUMPER_CODE_TEMPLATE = bytes.fromhex(
    "702803d02de9f14fe9f742be029bddf80cc02de9f0411c46e0460f8865680020"
    "4b212a4642f8040b0139fbd124482860d0202871012068713e0222483618ae60"
    "214800888002e8606f822049721a82420cd240f20011298205f11402402356f8"
    "041b42f8041b013bf9d101e00121a9716ff00000294640f21412154e11f8013b"
    "58400823400800d37040013bfad1013af4d1c043c5f814010020294640f22b12"
    "11f8013bc018013afad185f82b0140f22c1020800020c0470120bde8f08100bf"
    "55aaeb9000000008e0f7ff1f000000082083b8ed"
)
_RESPONSE_BUFFER_LOAD_OFFSET = 0x1C
_RESPONSE_LENGTH_STORE_OFFSET = 0xB2


class FlashDumpError(JkError):
    """The target image cannot be patched, or a dump frame is invalid."""


@dataclass(frozen=True)
class DumpChunk:
    """One validated response from the patched application."""

    address: int
    flash_size: int
    block: int
    data: bytes


def _offset(address: int) -> int:
    """Convert an application flash address to an image offset."""
    return address - APP_BASE


def _replace(out: bytearray, address: int, expected: bytes, replacement: bytes) -> None:
    """Replace bytes only when the exact stock instruction/data is present."""
    start = _offset(address)
    found = bytes(out[start : start + len(expected)])
    if found != expected:
        raise FlashDumpError(
            f"target bytes differ at 0x{address:08x}: "
            f"expected {expected.hex()}, found {found.hex()}"
        )
    out[start : start + len(replacement)] = replacement


def _thumb_bw(source: int, target: int) -> bytes:
    """Encode an unconditional Thumb-2 B.W without a runtime assembler."""
    delta = target - (source + 4)
    if delta & 1 or not -(1 << 24) <= delta < (1 << 24):
        raise FlashDumpError(
            f"Thumb branch from 0x{source:08x} to 0x{target:08x} is out of range"
        )
    sign = (delta >> 24) & 1
    i1 = (delta >> 23) & 1
    i2 = (delta >> 22) & 1
    j1 = 1 ^ i1 ^ sign
    j2 = 1 ^ i2 ^ sign
    first = 0xF000 | sign << 10 | (delta >> 12) & 0x3FF
    second = 0x9000 | j1 << 13 | j2 << 11 | (delta >> 1) & 0x7FF
    return struct.pack("<HH", first, second)


def _thumb_ldr_word(register: int, base: int, offset: int) -> bytes:
    """Encode a Thumb-1 LDR (immediate) for low registers."""
    if (
        not 0 <= register <= _LOW_REGISTER_MAX
        or not 0 <= base <= _LOW_REGISTER_MAX
        or offset % 4
        or not 0 <= offset <= _THUMB_WORD_OFFSET_MAX
    ):
        raise FlashDumpError("invalid Thumb LDR immediate operands")
    return struct.pack("<H", 0x6800 | (offset // 4) << 6 | base << 3 | register)


def _thumb_strh(register: int, base: int, offset: int) -> bytes:
    """Encode a Thumb-1 STRH (immediate) for low registers."""
    if (
        not 0 <= register <= _LOW_REGISTER_MAX
        or not 0 <= base <= _LOW_REGISTER_MAX
        or offset % 2
        or not 0 <= offset <= _THUMB_HALFWORD_OFFSET_MAX
    ):
        raise FlashDumpError("invalid Thumb STRH immediate operands")
    return struct.pack("<H", 0x8000 | (offset // 2) << 6 | base << 3 | register)


def _hook_address(target: FirmwareTarget) -> int:
    image_end = APP_BASE + target.image_size
    return (image_end + _INJECTION_ALIGNMENT - 1) & -_INJECTION_ALIGNMENT


def _table_address(target: FirmwareTarget) -> int:
    return _hook_address(target) + _TABLE_DELTA


def _trampoline_address(target: FirmwareTarget, index: int) -> int:
    start = (len(_DUMPER_CODE_TEMPLATE) + 3) & ~3
    return _hook_address(target) + start + index * 12


def _table_trampoline(
    address: int, register: int, table_address: int, resume: int
) -> bytes:
    """Load a relocated table address, then resume after a legacy ADDW."""
    if not 0 <= register <= _LOW_REGISTER_MAX:
        raise FlashDumpError(f"invalid low register r{register} for table trampoline")
    ldr_literal = struct.pack("<H", 0x4801 | register << 8)
    return (
        ldr_literal
        + _thumb_bw(address + 2, resume)
        + bytes.fromhex("00 bf")
        + struct.pack("<I", table_address)
    )


def _build_hook(target: FirmwareTarget) -> bytes:
    layout = _LAYOUTS[target.layout]
    hook_address = _hook_address(target)
    code = bytearray(_DUMPER_CODE_TEMPLATE)
    code[8:12] = _thumb_bw(hook_address + 8, layout.dispatch_addr + 4)
    code[_RESPONSE_BUFFER_LOAD_OFFSET : _RESPONSE_BUFFER_LOAD_OFFSET + 2] = (
        _thumb_ldr_word(5, 4, layout.response_buffer_offset)
    )
    code[_RESPONSE_LENGTH_STORE_OFFSET : _RESPONSE_LENGTH_STORE_OFFSET + 2] = (
        _thumb_strh(0, 4, layout.response_length_offset)
    )
    while len(code) % 4:
        code.append(0)
    table_address = _table_address(target)
    for index, reference in enumerate(layout.table_references):
        address = _trampoline_address(target, index)
        if address != hook_address + len(code):
            raise FlashDumpError("legacy table trampolines are not contiguous")
        code += _table_trampoline(
            address, reference.register, table_address, reference.address + 4
        )
    return bytes(code)


def _inject(out: bytearray, address: int, data: bytes) -> None:
    """Write extension data, padding the gap after the vendor image with 0xff."""
    start = _offset(address)
    if start < 0:
        raise FlashDumpError(
            f"injection address precedes the application: 0x{address:08x}"
        )
    if len(out) < start:
        out.extend(b"\xff" * (start - len(out)))
    end = start + len(data)
    if len(out) < end:
        out.extend(b"\xff" * (end - len(out)))
    out[start:end] = data


def identify_image(image: bytes) -> FirmwareTarget:
    """Return the exact audited target represented by an original vendor image."""
    digest = hashlib.sha256(image).hexdigest()
    target = TARGETS.get(digest)
    if target is None or len(image) != target.image_size:
        raise FlashDumpError(
            f"flash dumper supports {len(TARGETS)} exact audited vendor images; "
            f"got {len(image)} bytes, SHA-256 {digest}"
        )
    return target


def supports_device(model: str, version: str) -> bool:
    """Return whether the archive contains an audited image for this unit."""
    return (model, version) in SUPPORTED_DEVICES


def patch_image(image: bytes) -> bytes:
    """Add the cooperative dump action to any exact audited vendor image."""
    target = identify_image(image)
    layout = _LAYOUTS[target.layout]
    hook_address = _hook_address(target)
    table_address = _table_address(target)
    hook = _build_hook(target)

    table_size = layout.table_entries * 4
    old_table_off = _offset(layout.table_addr)
    table = image[old_table_off : old_table_off + table_size]
    if len(table) != table_size:
        raise FlashDumpError("stock command table is truncated")
    dump_register = M.FRAME_ADDR_OFFSET + M.ACTION_BASE + DUMP_SLOT
    if any(
        struct.unpack_from("<H", table, offset)[0] == dump_register
        for offset in range(0, len(table), 4)
    ):
        raise FlashDumpError(
            "dump action register is already present in the stock table"
        )
    table += struct.pack("<HBB", dump_register, 2, DUMP_COMMAND)

    if _offset(table_address) + len(table) > MIN_FLASH_SIZE - (APP_BASE - FLASH_BASE):
        raise FlashDumpError("injected content does not fit a 128 KiB part")

    out = bytearray(image)
    _replace(
        out,
        layout.dispatch_addr,
        _DISPATCH_PROLOGUE,
        _thumb_bw(layout.dispatch_addr, hook_address),
    )
    new_limit = bytes((layout.table_entries + 1, layout.lookup_limit[1]))
    for address in layout.lookup_limit_addrs:
        _replace(out, address, layout.lookup_limit, new_limit)

    if layout.table_pointer_addr is not None:
        _replace(
            out,
            layout.table_pointer_addr,
            struct.pack("<I", layout.table_addr),
            struct.pack("<I", table_address),
        )
    else:
        for index, reference in enumerate(layout.table_references):
            _replace(
                out,
                reference.address,
                reference.expected,
                _thumb_bw(reference.address, _trampoline_address(target, index)),
            )

    _inject(out, hook_address, hook)
    _inject(out, table_address, table)
    return bytes(out)


def is_patched(image: bytes) -> bool:
    """Return whether an image carries one complete audited dump patch."""
    descriptor = struct.pack(
        "<HBB", M.FRAME_ADDR_OFFSET + M.ACTION_BASE + DUMP_SLOT, 2, DUMP_COMMAND
    )
    checked: set[tuple[str, int]] = set()
    for target in _TARGET_LIST:
        key = (target.layout, target.image_size)
        if key in checked:
            continue
        checked.add(key)
        layout = _LAYOUTS[target.layout]
        hook_address = _hook_address(target)
        table_address = _table_address(target)
        hook = _build_hook(target)
        table_off = _offset(table_address)
        table_size = layout.table_entries * 4
        if len(image) < table_off + table_size + 4:
            continue
        if (
            image[_offset(layout.dispatch_addr) : _offset(layout.dispatch_addr) + 4]
            != _thumb_bw(layout.dispatch_addr, hook_address)
            or image[_offset(hook_address) : _offset(hook_address) + len(hook)] != hook
            or image[table_off : table_off + table_size]
            != image[
                _offset(layout.table_addr) : _offset(layout.table_addr) + table_size
            ]
            or image[table_off + table_size : table_off + table_size + 4] != descriptor
        ):
            continue
        if layout.table_pointer_addr is not None:
            start = _offset(layout.table_pointer_addr)
            if image[start : start + 4] != struct.pack("<I", table_address):
                continue
        elif any(
            image[_offset(reference.address) : _offset(reference.address) + 4]
            != _thumb_bw(reference.address, _trampoline_address(target, index))
            for index, reference in enumerate(layout.table_references)
        ):
            continue
        return True
    return False


def encode_frame(flash: bytes, block: int) -> bytes:
    """Build the frame emitted by the patch; used by the faithful simulator."""
    frame = bytearray(FRAME_SIZE)
    frame[:4] = FRAME_MAGIC
    frame[4] = FRAME_TYPE
    frame[5] = FRAME_VERSION
    address = FLASH_BASE + block * BLOCK_SIZE
    struct.pack_into("<IIHH", frame, 8, address, len(flash), BLOCK_SIZE, block)
    start = block * BLOCK_SIZE
    chunk = flash[start : start + BLOCK_SIZE]
    if len(chunk) != BLOCK_SIZE:
        frame[6] = 1
        struct.pack_into("<H", frame, 16, 0)
    else:
        frame[20 : 20 + BLOCK_SIZE] = chunk
    struct.pack_into("<I", frame, CRC_OFFSET, zlib.crc32(frame[:CRC_OFFSET]))
    frame[CHECKSUM_OFFSET] = sum(frame[:CHECKSUM_OFFSET]) & 0xFF
    return bytes(frame)


def decode_frame(raw: bytes, expected_block: int) -> DumpChunk:
    """Find and validate one dump frame among the following Modbus ACK bytes."""
    prefix = FRAME_MAGIC + bytes((FRAME_TYPE, FRAME_VERSION))
    start = raw.find(prefix)
    if start < 0:
        raise FlashDumpError(
            "no flash-dump frame received (the patched firmware may not be running)"
        )
    frame = raw[start : start + FRAME_SIZE]
    if len(frame) != FRAME_SIZE:
        raise FlashDumpError(
            f"truncated flash-dump frame ({len(frame)}/{FRAME_SIZE} bytes)"
        )
    if sum(frame[:CHECKSUM_OFFSET]) & 0xFF != frame[CHECKSUM_OFFSET]:
        raise FlashDumpError("flash-dump frame has a bad JK sum8 checksum")
    expected_crc = struct.unpack_from("<I", frame, CRC_OFFSET)[0]
    actual_crc = zlib.crc32(frame[:CRC_OFFSET])
    if actual_crc != expected_crc:
        raise FlashDumpError(
            f"flash-dump frame has a bad CRC32 ({actual_crc:08x} != {expected_crc:08x})"
        )
    address, flash_size, length, block = struct.unpack_from("<IIHH", frame, 8)
    if block != expected_block or address != FLASH_BASE + expected_block * BLOCK_SIZE:
        raise FlashDumpError(
            f"flash-dump frame is block {block} at 0x{address:08x}, "
            f"expected block {expected_block}"
        )
    if flash_size not in SUPPORTED_FLASH_SIZES:
        raise FlashDumpError(f"unsupported reported flash size: {flash_size} bytes")
    if frame[6] != 0 or length != BLOCK_SIZE:
        raise FlashDumpError(
            f"firmware refused flash block {block} (status={frame[6]}, length={length})"
        )
    return DumpChunk(address, flash_size, block, bytes(frame[20 : 20 + length]))


def _read_block(
    bus, slave: int, block: int, timeout: float, max_retries: int
) -> DumpChunk:
    """Request and validate one block, retrying only a failed read of that block."""
    last: Exception | None = None
    for _attempt in range(max_retries + 1):
        try:
            bus.write_registers(
                slave,
                bus.action_register(DUMP_SLOT),
                block.to_bytes(2, "little"),
                expect_reply=False,
            )
            raw = bus.read_raw(timeout, FRAME_SIZE + M.WRITE_REPLY_LEN + 64)
            return decode_frame(raw, block)
        except (FlashDumpError, M.ModbusError) as exc:
            last = exc
    raise FlashDumpError(
        f"flash block {block} failed after {max_retries + 1} attempts: {last}"
    ) from last


def dump_flash(
    bus,
    slave: int,
    *,
    report: Reporter = SILENT,
    timeout: float = 1.0,
    max_retries: int = 3,
) -> bytes:
    """Read all MCU flash through the patched action, with per-frame validation."""
    started = time.time()
    report.step("Reading full flash")
    first = _read_block(bus, slave, 0, timeout, max_retries)
    total_blocks = first.flash_size // BLOCK_SIZE
    image = bytearray(first.flash_size)
    image[:BLOCK_SIZE] = first.data
    report.sending(1, total_blocks, time.time() - started)
    for block in range(1, total_blocks):
        chunk = _read_block(bus, slave, block, timeout, max_retries)
        if chunk.flash_size != len(image):
            raise FlashDumpError(
                f"flash size changed at block {block}: {chunk.flash_size} != {len(image)}"
            )
        offset = block * BLOCK_SIZE
        image[offset : offset + BLOCK_SIZE] = chunk.data
        report.sending(block + 1, total_blocks, time.time() - started)
    return bytes(image)


def vector_sanity(
    image: bytes, model: str | None = None, version: str | None = None
) -> list[str]:
    """Return bootloader and audited application-vector validation problems."""
    problems: list[str] = []
    if len(image) not in SUPPORTED_FLASH_SIZES:
        return [f"unexpected flash size {len(image)}"]
    boot_sp, boot_reset = struct.unpack_from("<II", image, 0)
    if not SRAM_BASE <= boot_sp < SRAM_END:
        problems.append(f"bootloader SP is not SRAM: 0x{boot_sp:08x}")
    if not (boot_reset & 1) or not FLASH_BASE <= (boot_reset & ~1) < APP_BASE:
        problems.append(
            f"bootloader reset vector is outside pre-app flash: 0x{boot_reset:08x}"
        )

    candidates = _TARGET_LIST
    if model is not None or version is not None:
        candidates = tuple(
            target
            for target in candidates
            if target.model == model and target.version == version
        )
        if not candidates:
            problems.append(f"no audited application vectors for {model} {version}")
            return problems
    expected = {(target.sp, target.reset) for target in candidates}
    app_sp, app_reset = struct.unpack_from("<II", image, APP_BASE - FLASH_BASE)
    if (app_sp, app_reset) not in expected:
        choices = ", ".join(
            f"SP=0x{sp:08x}/RESET=0x{reset:08x}" for sp, reset in sorted(expected)
        )
        problems.append(
            "application vectors differ from the audited target: "
            f"SP=0x{app_sp:08x} RESET=0x{app_reset:08x}; expected {choices}"
        )
    return problems


__all__ = [
    "APP_BASE",
    "BLOCK_SIZE",
    "DUMP_SLOT",
    "FirmwareTarget",
    "FlashDumpError",
    "PERSISTENT_BASE",
    "SUPPORTED_DEVICES",
    "TARGETS",
    "decode_frame",
    "dump_flash",
    "encode_frame",
    "identify_image",
    "is_patched",
    "patch_image",
    "supports_device",
    "vector_sanity",
]
