# Full-flash dump through a patched JK BMS application

Scope: every `.jkbms` below `research/firmware`, across every archived hardware
line.

Status: **67 distinct vendor images and 33 binary layouts are statically and
simulator verified. One target/image pair is hardware verified: the V15.41
patch was flashed to a `JK_PB2A16S20P`, which booted, continued operating and
returned a validated 128 KiB full-flash capture.**

That experiment proves only this physical board, bootloader and source image.
It does not validate the other 66 images or every hardware revision sold under
the same model name. The patch remains research firmware for a battery safety
device; establish physical recovery and independent pack protection first.

---

## 1. Complete audit boundary

The archive contains 67 containers and 67 unique application SHA-256 values.
The patcher accepts those hashes only. A matching model, version, filename or
instruction pattern is insufficient.

| archive line | metadata versions | images | model coverage |
|---|---|---:|---|
| HW V14 | 14.03, 14.07, 14.10, 14.12, 14.13, 14.14, 14.15, 14.17, 14.19, 14.20 | 38 | `JK_PB1A16S10P`, `JK_PB1A16S15P`, `JK_PB2A16S15P`, `JK_PB2A16S20P`; 14.03 has PB2 models only |
| HW V15 | 15.10, 15.11, 15.17, 15.32, 15.41 | 17 | the same four PB models; 15.32 has `JK_PB2A16S20P` only |
| B-Series | 17.06, 19.04, 19.20, 19.21 | 7 | `JK_B2A8S30P`, `JK_B2A24S20P`, `JK_BD6A24S8P`, `JK_B2A8S20P`, and hyphenated variants |
| HW V19 PB | 19.02, 19.04 | 5 | four PB models at 19.02; `JK_PB2A16S20P` at 19.04 |

The full hash/model/version/length/vector allowlist is `TARGETS` in
`src/jkctl/flashdump.py`. Keeping that catalog next to the checked patch data
prevents documentation from becoming a second acceptance mechanism.

One archive-name discrepancy matters operationally: files in directory
`V14.05` identify themselves as version **14.03**. Compatibility and dumper
selection use authenticated container contents, not that directory name.

---

## 2. Common memory and processor evidence

Every audited application begins at `0x08002000`, not `0x08004000`.
Independent evidence:

- each image begins with an ARM Cortex-M vector table;
- every descriptor table reference resolves as `0x08002000 + image_offset`;
- reset vectors map to executable Thumb reset stubs inside the image;
- all images contain the STM32F1 FlashSizeReg address `0x1ffff7e0` and code for
  the 128/256 KiB size values;
- application callsites use persistent pages at `0x08001800` and `0x08001c00`.

| region | address |
|---|---|
| boot/persistent region absent from `.jkbms` | `0x08000000..0x08001fff` |
| application start | `0x08002000` |
| persistent pages used by the application | `0x08001800`, `0x08001c00` |
| end of 128 KiB flash | `0x0801ffff` |

Source applications contain 67,783 to 100,017 bytes and end at
`0x080128c6..0x0801a6b0`. Every injected hook and relocated table remains below
`0x08020000`; the tightest audited image still has 21,296 bytes free.
Initial stack pointers vary from `0x20000df8` to `0x20005738`, so the old V15
assumption that every target uses more than 20 KiB of RAM does not apply to the
earlier hardware line.

The hardware capture establishes the V15.41 PB2A16S20P layout: executable
bootloader content occupies `0x08000000..0x08001009`, persistent words/blobs
occur at `0x08001400` and `0x08001600`, the upgrade-marker page is at
`0x08001c00`, and the application starts at `0x08002000`. The host still calls
the complete 8 KiB region a pre-application carve because boundaries may differ
on other targets.

---

## 3. Shared dispatcher contract

All 67 images have the properties required by this dump design:

- descriptors are `{uint16 register, uint8 byte_length, uint8 command}`;
- stock action `0x1626`, command `0xff`, remains the upgrade/reset action;
- each protocol caller converts the two-byte request and invokes one command
  dispatcher;
- the response object and stock RS485 callback occupy the same dispatcher
  stack argument positions;
- every dispatcher starts with `push.w {r0,r4-r11,lr}` (`2d e9 f1 4f`);
- the native response buffer is 300 bytes;
- command `0x70` and action register `0x162e` are unused in every source image.

What varies is explicit layout data, never inferred while patching:

| layout | descriptors | dispatcher | descriptor table | table materialization | response buffer/length offsets |
|---|---:|---:|---:|---|---|
| 14.03 | 98 | `0x08002684` | `0x0800f5f4` | 3 direct `ADDW` | `+4` / `+0` |
| 14.07-pb1 | 98 | `0x08002684` | `0x0801083c` | 3 direct `ADDW` | `+4` / `+0` |
| 14.07-pb2 | 98 | `0x08002684` | `0x08010840` | 3 direct `ADDW` | `+4` / `+0` |
| 14.10-pb1 | 98 | `0x08002684` | `0x08010f90` | 3 direct `ADDW` | `+4` / `+0` |
| 14.10-pb2 | 98 | `0x08002684` | `0x08010f98` | 3 direct `ADDW` | `+4` / `+0` |
| 14.12 | 98 | `0x080026f8` | `0x08011d18` | 3 direct `ADDW` | `+4` / `+0` |
| 14.13 | 98 | `0x080026f8` | `0x08011ca4` | 3 direct `ADDW` | `+4` / `+0` |
| 14.14 | 98 | `0x080026f8` | `0x08011d24` | 3 direct `ADDW` | `+4` / `+0` |
| 14.15 | 98 | `0x080026f8` | `0x08012180` | 3 direct `ADDW` | `+4` / `+0` |
| 14.17-pb1 | 108 | `0x080026f8` | `0x0800e160` | 4 direct `ADDW` | `+4` / `+0` |
| 14.17-pb2 | 108 | `0x080026f8` | `0x0800e164` | 4 direct `ADDW` | `+4` / `+0` |
| 14.19-pb1 | 108 | `0x080026f8` | `0x0800e14c` | 4 direct `ADDW` | `+4` / `+0` |
| 14.19-pb2 | 108 | `0x080026f8` | `0x0800e150` | 4 direct `ADDW` | `+4` / `+0` |
| 14.20-pb1 | 108 | `0x080026fc` | `0x0800e1a8` | 4 direct `ADDW` | `+4` / `+0` |
| 14.20-pb2 | 108 | `0x080026fc` | `0x0800e1ac` | 4 direct `ADDW` | `+4` / `+0` |
| 15.10-pb1 | 108 | `0x080026fc` | `0x0800e12c` | 4 direct `ADDW` | `+4` / `+0` |
| 15.10-pb2 | 108 | `0x080026fc` | `0x0800e130` | 4 direct `ADDW` | `+4` / `+0` |
| 15.11-pb1 | 108 | `0x080026fc` | `0x0800e1b8` | 4 direct `ADDW` | `+4` / `+0` |
| 15.11-pb2 | 108 | `0x080026fc` | `0x0800e1bc` | 4 direct `ADDW` | `+4` / `+0` |
| 15.17 | 108 | `0x0800248c` | `0x08015c44` | literal at `0x0800e000` | `+4` / `+0` |
| 15.32 | 108 | `0x0800248c` | `0x08017044` | literal at `0x0800e90c` | `+4` / `+0` |
| 15.41 | 110 | `0x0800248c` | `0x080176d0` | literal at `0x0800edcc` | `+4` / `+0` |
| 17.06-b2a8s30p | 112 | `0x08002460` | `0x0801710c` | literal at `0x0800eb78` | `+8` / `+2` |
| 19.02-15a | 112 | `0x08002490` | `0x08018cf0` | literal at `0x080102a0` | `+8` / `+2` |
| 19.02-pb1a10 | 112 | `0x08002490` | `0x08018cbc` | literal at `0x08010268` | `+8` / `+2` |
| 19.02-pb2a20 | 112 | `0x08002490` | `0x08018ce4` | literal at `0x08010290` | `+8` / `+2` |
| 19.04-b2a24s20p | 113 | `0x08002460` | `0x08017870` | literal at `0x0800f1b0` | `+8` / `+2` |
| 19.04-b2a8s30p | 113 | `0x08002460` | `0x080178b0` | literal at `0x0800f1f4` | `+8` / `+2` |
| 19.04-bd6a24s8p | 113 | `0x08002460` | `0x080178a0` | literal at `0x0800f1e4` | `+8` / `+2` |
| 19.04-pb2a20 | 113 | `0x08002490` | `0x08018e74` | literal at `0x0801041c` | `+8` / `+2` |
| 19.20-b2a8s20p | 115 | `0x08002960` | `0x080197c8` | literal at `0x08010c00` | `+8` / `+2` |
| 19.20-b2a8s30p | 115 | `0x08002960` | `0x08016a08` | literal at `0x080106b4` | `+8` / `+2` |
| 19.21-b2a8s20p | 115 | `0x08002960` | `0x08019990` | literal at `0x08010c00` | `+8` / `+2` |

The exact lookup-limit and direct-reference addresses and their expected stock
bytes are also in `_LAYOUTS`. A patch aborts if any byte differs.

---

## 4. Issues uncovered across hardware versions

1. **The original private action register collides with stock firmware.**
   `0x1628` looked free in V15, but V17/V19 use it for command `0xc4`; they also
   use `0x162a` for `0xfe`, and newer builds use `0x162c` for `0xc5`. Register
   `0x162e` is the first action slot absent from all 67 tables, so the patch and
   host now use it.
2. **The response-object ABI changed.** V14/V15 store the output buffer pointer
   at object offset 4 and the length halfword at offset 0. B-Series and V19 use
   offsets 8 and 2. Reusing the V15 hook unchanged would dereference the wrong
   word and corrupt the response object. The patcher rewrites both instructions
   per layout.
3. **Descriptor counts are not a two-value V15 detail.** Counts are 98, 108,
   110, 112, 113 and 115. Tables also differ internally, so the patch always
   copies the source table and appends one record rather than synthesizing a
   canonical table.
4. **Table-address code has three forms.** Early V14 has three direct
   PC-relative `ADDW` sites; later V14 and early V15 have four; later builds
   share one pointer literal referenced by three or four callsites. Direct
   sites require non-flag-changing trampolines because the relocated table is
   beyond the immediate range.
5. **Model families can shift executable addresses within one release.** PB1
   and PB2 differ by four bytes in multiple V14/V15 releases. V19.02 and 19.04
   have larger model-specific shifts. Version-only selection is unsafe.
6. **Filename spelling is not stable.** The archive mixes `JK_...` and `JK-...`
   identities and contains a V14.05 directory whose metadata says 14.03. The
   exact in-image model/version and full image hash are authoritative.
7. **Vector/RAM assumptions do not cross hardware lines.** Initial SP values
   range over more than 18 KiB and reset stubs move with every layout. Dump
   validation must use the connected model/version's exact audited vector pair.
8. **Pattern-only acceptance remains unsafe.** Common prologues and register
   tables do not imply compatible call targets or object layouts. Full SHA-256
   allowlisting remains mandatory.
9. **Callback timing is proven for one target only.** The V15.41 patch ran on
   one PB2A16S20P without disrupting observed board operation and returned the
   complete capture. That does not prove timing or continued battery protection
   on another physical revision.
10. **One bootloader is now known.** The captured V2.0.2 receiver matches the
    sender format but has only per-block XMODEM checks; other bootloader
    versions, RDP state and recovery behavior remain unverified.

Conclusion: one data-driven implementation is practical for the complete
archive, but one heuristic patch recipe for arbitrary JK firmware is not. The
safe boundary is shared code plus exact hash-locked layout data.

---

## 5. Patch design

Implementation: `src/jkctl/flashdump.py`.

For each exact source image, the patcher:

1. puts a common Thumb-2 hook at the next `0x100` boundary after the image;
2. puts the relocated source descriptor table at `hook + 0x400`;
3. replaces the dispatcher prologue with a computed `b.w hook`;
4. increments both descriptor-count comparisons;
5. redirects the table pointer or replaces each direct `ADDW` with a branch to
   a dedicated 12-byte trampoline;
6. copies the source table unchanged and appends `{0x162e, 2, 0x70}`;
7. patches the hook's response buffer load and length store to the audited ABI;
8. pads the application to the exact end of the new table.

A direct-table trampoline loads the relocated table address into the original
low register, branches to the instruction after the displaced `ADDW`, and
changes no condition flags.

For commands other than `0x70`, the common hook replays the displaced
`push.w {r0,r4-r11,lr}` and branches to `dispatcher + 4`. Command `0x70`:

1. loads the little-endian block number copied by the stock converter;
2. reads FlashSizeReg;
3. copies at most one 256-byte block from `0x08000000 + block*256`;
4. builds one 300-byte response in the stock response object;
5. invokes the callback supplied by the stock Modbus caller;
6. returns to the Modbus task.

There is no autonomous stream, persistent dump state, flash write, interrupt
masking or long polling loop. The application regains control between blocks.
Keystone was used to assemble and compare the audited hook during research but
is not a runtime dependency; production branches and ABI instructions are
encoded directly.

---

## 6. Dump frame and host validation

The custom response keeps JK's 300-byte framing size:

| offset | size | value |
|---|---:|---|
| 0 | 4 | `55 aa eb 90` |
| 4 | 1 | type `0xd0` |
| 5 | 1 | protocol version `1` |
| 6 | 1 | status (`0` success, `1` out of range) |
| 7 | 1 | reserved |
| 8 | 4 | absolute flash address, little-endian |
| 12 | 4 | total flash bytes, little-endian |
| 16 | 2 | payload length, little-endian (`256`) |
| 18 | 2 | block number, little-endian |
| 20 | 256 | flash bytes |
| 276 | 4 | CRC-32/ISO-HDLC over bytes `0..275` |
| 280 | 19 | reserved |
| 299 | 1 | JK sum8 over bytes `0..298` |

The normal eight-byte FC16 acknowledgement follows. The host validates sum8,
CRC32, block, address, length and a stable total size before advancing. A bad
or missing block is retried independently. A 128 KiB device needs 512 requests;
a 256 KiB device needs 1024.

`dump-flash` is allowed only for a model/version pair in the audited catalog.
On stock firmware `0x162e` is absent, so the request is rejected rather than
invoking a stock action. After capture, vector checks use that pair's complete
set of audited SP/reset values.

---

## 7. Verification

For every one of the 67 source containers:

- decryption/inflation identified the expected model, version, vectors, length
  and SHA-256;
- the patcher selected the expected layout;
- all stock bytes at every edit point matched before replacement;
- the relocated table matched that source's table byte for byte and ended with
  `{0x162e, 2, 0x70}`;
- the dispatcher branch, stock-return branch and direct-table trampolines
  decoded to their intended targets and registers;
- the hook's response-object instructions matched that layout's ABI;
- no source byte changed outside the declared dispatcher, count and
  table-reference edits;
- injected content ended below the 128 KiB boundary;
- `firmware.repack()` output reloaded to the exact patched image and unchanged
  metadata.

The simulator exercised every request for synthetic 128 and 256 KiB devices.
Repository tests cover both trampoline shapes, both response-object ABIs, frame
corruption, request endianness, supported-device gating and vector validation.

The physical PB2A16S20P experiment additionally established:

- official `73-JK-PB2A16S20P-V15.41.jkbms` and its generated dumper both
  completed the normal firmware-transfer path;
- the patched board booted and operated as before;
- `flash.bin` is 131,072 bytes with SHA-256
  `a146f5574516116b11e426633e679c59537d697b9c133961680894b572468aa3`;
- its application region is byte-for-byte the 93,628-byte generated dumper,
  which is itself exactly `patch_image()` applied to the official image;
- `bootloader.bin` is exactly the first 8 KiB of `flash.bin`, SHA-256
  `f260f0561ab718d103fbeb6a7f0206284cd6ca8ff3e0dc2cb60378d86d2c1cd5`;
- both vector tables are valid and 78 structured 24-byte history records remain
  above the application, providing independent evidence against transport
  garbage.

One capture cannot establish repeatability. New targets should still be dumped
twice and compared before the result is treated as recovery material.

---

## 8. Real-device procedure

Prerequisites:

- physical access to BOOT0, reset, SWD/UART recovery and pack disconnect;
- the matching stock `.jkbms` and extracted application kept offline;
- an attended, quiescent pack within safe voltage/temperature limits;
- independent charge/load disconnect.

Build from the exact file for the connected model/version:

```sh
jkctl firmware make-dumper stock.jkbms -o /tmp/jk-flash-dumper.jkbms
jkctl firmware info /tmp/jk-flash-dumper.jkbms
```

Flash only after establishing the bench recovery route:

```sh
jkctl firmware flash --force /tmp/jk-flash-dumper.jkbms
```

After restart, require `jkctl info` to report the expected healthy unit, then:

```sh
jkctl firmware dump-flash \
  -o /tmp/jk-full-flash.bin \
  --bootloader-out /tmp/jk-pre-app-8k.bin

sha256sum /tmp/jk-full-flash.bin /tmp/jk-pre-app-8k.bin
```

Run the dump twice and require identical SHA-256 values. Preserve the complete
image; the 8 KiB carve alone is not a whole-chip recovery image.

Restore the exact stock file through the normal firmware command and confirm
protection, sampling, charge/discharge control and communications. If the
patched application does not answer, stop using the pack and follow
`research/firmware/bootloader-dump-procedure.md`. Do not clear readout
protection until a verified full-flash image exists: clearing RDP can mass-erase
the bootloader and persistent pages.
