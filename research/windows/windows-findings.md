# JK BMS Monitor 3.11.0 — reverse engineering notes

## 1. Installer
`jk-bms-monitor-3.11.0-setup.exe` = **Qt Installer Framework** self-extractor.
- magic cookie `0xc2630a1c99d668f8` at EOF-8, `MagicInstallerMarker 0x12023233` at EOF-16
- dataBlockSize 39322006 -> dataBlockStart = filesize - dataBlockSize = 0x1314200
- 3 components: com.jktech.desktop.app / .qt / .modules
- 16 embedded 7z archives, all SHA-1 verified OK on extraction

Extraction is fully scripted; no wine needed. See `re/bin/` and `re/extracted/`.

## 2. Application architecture (all PE32+ x86-64, MSVC, NOT packed, .pdata + RTTI intact)
| file | role |
|---|---|
| jk-bms-monitor.exe (2.9 MB) | Qt5 Widgets GUI, upgrade dialog + .jkbms handling lives HERE |
| protocore.dll (7.0 MB) | generic protocol engine, namespace `J`. Bundles ChaiScript, LibTomCrypt, rapidjson, SOCI. Classes: JProtocol, JFrame, JFrameCode, JCheck, JSerialChannel, Aes, Des, Md5 |
| protometa.dll (2.1 MB) | Qt/QML model layer over protocore (JCmdModel, JProtocolPool, ...) |
| protowidget.dll | UI widgets |
| jwt.dll | Qt UI toolkit (JNode etc.), not JSON-web-token |
| qwt.dll | plotting |

`JEncryptModel` (protometa) = PC-app **license activation** (cpuId/macAddress/hdSeries/serialNumber).
NOT firmware crypto — do not chase.

## 3. Firmware files (.jkbms)
- Fully encrypted: entropy 7.997, no header, no magic, no plaintext tail
- All sizes are multiples of 16 -> block cipher, 16-byte block (AES)
- Zero repeated 16-byte blocks -> NOT plain ECB over a sparse image (CBC/CTR, or compress-then-encrypt)
- Two files of same version/different model share 0 common prefix -> per-file IV or per-model key
- `config/*.jsonds` use the SAME scheme (encrypted, 16-multiple). Their plaintext is JSON =>
  **known-plaintext oracle** to validate any recovered key/algorithm before touching a BMS.
- App strings confirm: "Must to apply decrypt method for jsonds file "%1"!",
  "Decrypt content of file "%1" failure!"

## 4. Upgrade validation logic recovered from Qt translation catalog (ui-zh_CN.qm)
Dialog "Upload Fireware": fields `Fireware:`, `Open fireware file`, `Force Updating` (checkbox),
`Start Updating`, result `Upload fireware successfully!`

Checks, in likely order:
1. `Open encrypted fireware failure (%1)! Please Select a valid one!`
2. `Encrypted fireware is too small! Please Select a valid one!`
3. `Encrypted fireware is too large (>%1MB)!`
4. `Fireware is invalid! Please select a valid one!`      <- post-decrypt structural check
5. `Major version is not same to connected device!`        <- HW major must MATCH
6. `Minor version must be larger than connected device!`   <- no downgrade (bypassed by "Force Updating"?)
Other relevant: `Dynamic key is invalid!`, `Software version is invalid! (%1)`,
`Serial number is invalid!`, `Boot`, `Restart Board`.

This maps directly onto the V14/V15/V19 "HW version" directory split in the firmware tree.

## 5. Reference data extracted
- `extracted/bms-model-families.json` — 7 families (JK-B1A24S, JK-B1A24S-P, JK-B1A32S-P,
  JK-DZ08-B1A24S, JK-B1A24S-PLW, JK-B1A24S-PSR, JK-BXAXS-XP) -> model names + cell `count` + `beta` flag
- `extracted/protocols-{en,zh}.json` — UART (21) and CAN protocol id->name tables
- `extracted/config/main.json` — defaults: serial, COM3, **115200 8N1**, queryTimeout 350 ms,
  parallelTimeout 10000 ms, addrCode 1, frameAddrOffset 0x1000

## 6. RS485 addressing (from "JKBMS Upgrade.pdf")
Device ID 0..15 set by a 4-position DIP switch (SW1=bit0 .. SW4=bit3), ON = 1.

## 7. Android APK
`JK_BMS_6.1.01.apk` — Kotlin, no JK native lib. `FirmwareUpgradeActivity` is **app self-update**
(isAppUpgraded / forcedUpgrade / INTENT_EXTRA_BADGE_UPGRADENUMBER), NOT BMS flashing.
Confirmed by user: the Android app does not support BMS firmware upgrade.
Useful only as cross-reference for the BLE read/parse protocol (BleMessage, BmsAdvParser).
Windows app is the source of truth.

## 8. Open questions -> require decompilation
- .jkbms / .jsonds cipher, mode, key derivation (LibTomCrypt in protocore is the likely provider)
- decrypted firmware container layout: where model id / HW major.minor / CRC live
- the RS485 upgrade command sequence: enter-boot, erase, chunked write, verify, reboot
- what "Dynamic key" is (session key from BMS? challenge-response?)
- how the numeric filename prefix (044-, 069-, 073-) maps to a model/device code

---------------------------------------------------------------------------
# ROUND 2 -- container cracked, validation recovered, transport identified
---------------------------------------------------------------------------

## 9. .jkbms container -- SOLVED (verified on all 63 files, 0 failures)

    file     = AES-256-CBC(key, iv=0) over payload_blob
    key      = b"A39FF3F613F94FDD957EC22EF642ADA9"   (32 ASCII bytes, used raw)
               .rdata 0x1401fb730 -> J::Aes::Aes() in FUN_1400454a0
    payload_blob = uint32_le raw_len || zlib deflate stream
    payload  = zlib.decompress(...)          len must == raw_len, <= 0x1400000
    payload[-12:] = int64_le build_ms || int32_le valid_hours
    image    = payload[:-12]                 <- the bytes actually flashed

Image is raw ARM Cortex-M linked at **0x08004000** (vector table 0x144 bytes,
first handler exactly 0x08004144).  Bootloader occupies 0x08000000-0x08003FFF
and is NOT shipped in any .jkbms file.

### Metadata header, at payload offset 0x200 (6 x 16-byte NUL-padded ASCII)
    0x200 software version   "19.02"
    0x210 build date         "Mar 23 2025"   (__DATE__)
    0x220 (zero)
    0x230 build time         "23:51:31"      (__TIME__)
    0x240 (zero)
    0x250 model / identity   "JK_PB2A16S20P" | "JK-B2A8S20P"
    0x260 uint32_le device code  (73/44/74/... == the filename numeric prefix;
                                  0xFFFFFFFF on HW V14/V15 builds)

## 10. Full validation chain (FUN_1400456b0 + FUN_140045d90) -- IMPLEMENTED
 1 file exists                      -> "Fireware is invalid!"
 2 open ok                          -> "Open encrypted fireware failure (%1)!"
 3 size <= 20 MiB (0x1400001 bound) -> "Encrypted fireware is too large (>20MB)!"
 4 decrypt + inflate ok             -> internal failure
 5 len(payload) > 0x25f             -> "Binary file is invalid!"
 6 if valid_hours > 0:  now must be within [build_ms-2h, build_ms+valid_hours]
                                    -> "Binary file is invalid!"
 7 fw version splits on '.' into exactly 2 ints
                                    -> "Software version is invalid! (%1)"
 8 device version likewise          -> "Device Identify is not same..."
 9 fw_major == dev_major            -> "Major version is not same to connected device!"
10 dev_minor <  fw_minor            -> "Minor version must be larger than connected device!"
11 fw_model == dev manuDeviceID     -> "Device Identify is not same to connected device!"

NOTE: 7 of the 63 supplied firmware files are time-limited builds whose window
has long expired; the official app would refuse them (step 6):
  B-Series/044-JK-B2A8S20PHCR-V19.20.jkbms          (1 h)
  HW V14/legacy Firmware/V14.05/{72,73}-*.jkbms     (24 h)
  HW V14/legacy Firmware/V14.07/{69,70,72,73}-*.jkbms (72 h)

## 11. Device-side fields
FUN_140045c40:
    JSearchEngine::begin("update-fireware")
      .beginGroup("frame/03", 2, true)
        .field("manuDeviceID")      -> compared against payload[0x250]
        .field("softwareVersion")   -> compared against payload[0x200]
      .endGroup()

## 12. Transport = Modbus RTU (NOT the BLE-style 0x55AAEB90 framing)
Evidence:
  * FUN_140015880 byte-swaps values to big-endian 16-bit words and passes a
    *register count* (len/2) to the sender.
  * config/main.json: frameAddrOffset "0x1000", addrCode 1, 115200 8N1,
    queryTimeout 350 ms.
  * bundled protocol list: "001 - 极空BMS RS485 Modbus通用协议V1.0".
  * app string "This function code operation register is not supported".
  * V14.17 release notes mention the JKBMS Modbus protocol / RCV RFV registers.
=> "frame/03" is Modbus **function code 0x03**, and manuDeviceID /
   softwareVersion / deviceSN are named registers in that map.
The 0x55AAEB90 / 0xAA5590EB frames found in the firmware belong to the BLE and
inverter-UART channels (the inverter UART uses an A5 5A header, FUN_080146c8).

## 13. BMS firmware command dispatcher FUN_08004490 (case label == cmd - 1)
  cmd 0x96 -> emits frame type 1 (settings) + type 3 (device info)
  cmd 0x97 -> emits frame type 3 (device info)
  cmd 0xc3 -> emits frame type 2 (cell info)
  cmd 0xa1 -> type 5 ; cmd 0xa7 -> type 6
  cmd 0xff -> FUN_08007000(1) writes magic **0x5AA5** to a backup register,
              then FUN_080083ca() resets  => "reboot into bootloader"
  Response frames are 300 bytes: 55 AA EB 90 | type | counter | 292B | cksum
  Command frames are 20 bytes:   AA 55 90 EB | cmd | ... | cksum (sum & 0xFF)
  !! cmd 0x18 is a SETTINGS WRITE (persists a temperature setpoint) -- never
     send it while probing.  Verified read-only: 0x96, 0x97, 0xc3.

## 14. Upgrade trigger path in the PC app
  FUN_140045b50 ("Start Updating") -> confirm dialog -> FUN_140046880
  FUN_140046880 -> FUN_140017260(engine, imageQByteArray, callback)
  FUN_140017260 computes frame code = frameAddrOffset + 0x600 + 0x26 (= 0x626)
    and calls FUN_140015880 -> FUN_1400150b0 / FUN_140015560.
  Remaining unknown: the exact on-wire upgrade sequence, because the receiving
  side is the bootloader, which is absent from every .jkbms file.

## 15. Deliverables written
  jkbms_tool/jk_firmware.py  container + all validation      (done, verified)
  jkbms_tool/jk_probe.py     read-only hardware recon        (done, safe)
  jkbms_tool/jkbms.py        CLI: list / info / check+upgrade(transfer pending)

---------------------------------------------------------------------------
# ROUND 3 -- datasource decrypted, register map + upgrade protocol recovered
---------------------------------------------------------------------------

## 16. config/*.jsonds -- SOLVED (same container as .jkbms, different key)

    file    = AES-256-CBC(key, iv=0)
    key     = b"2B10F23AC94C4910AE8BCCE19E4D485B"   (.rdata of protocore.dll)
    plain   = uint32_le raw_len || zlib deflate stream  ->  JSON

    en_US.jsonds -> 24741 B JSON     zh_CN.jsonds -> 25633 B JSON
    (decrypted copies: re/extracted/config/{en_US,zh_CN}.json,
     shipped with the tool as jkbms_tool/jk_protocol_{en,zh}.json)

Note protocore.dll also carries three other 32-char keys that are NOT this one:
161FF7528B899B2D0C28607CA52C5B86, CF5AC8395BAFEB13C02DA292DDED7A83,
E87579C11079F43DD824993C2CEE5ED3.  Untried; not needed.

## 17. The datasource IS the protocol spec

    vs[0] JKTech / ss[0] JK_BMS / tas[0] "JK-BXAXS-XP"
      is: header  h  c=4  dvs "55-AA-EB-90"
          frameCode  fc
          counter    ct
          frame      f   -> tables 01..06
          check      ck  ct="sum8"  ep=298

    => 300-byte frame: 55 AA EB 90 | type | counter | 293B payload | sum8(0..298)

    table 01 settings (up)     46 fields   ends at 299
    table 02 cell/runtime (up) 74 fields   ends at 299
    table 03 device info (up)  47 fields   ends at 299
    table 04 settings (down)   45 fields   ends at 299
    table 05 system log        4 fields    ends at 299
    table 06 fault records     nested

Field sizes: n -> nt (u8/i16/u32/f32...), a -> c * at, bm/bv -> ceil(c/8) bits,
h -> c.  Every table measuring out to exactly 299 is what validates the model.
This is confirmed independently by the BMS firmware's own frame builders
(FUN_08008a14/a68/af0/b4c/bc4): header 4B, [4]=type, [5]=counter, 0x124 bytes
of payload at +6, checksum = sum of the first 299 bytes stored at [299].
FUN_08008c2c builds the 20-byte AA5590EB reply (cmd 0xC8).

jkbms_tool/jk_protocol.py turns this JSON into offsets/decoders directly.

## 18. Transport: Modbus RTU -- CONFIRMED, with the full register map

FUN_140015560 @ 0x140015560 is the serializer:

    [slave] [func] [reg_hi][reg_lo] [cnt_hi][cnt_lo]
    (func 0x10:) [byte_count] [data...]
    [crc_lo][crc_hi]              <- Ghidra-identified crc16_modbus

    slave = engine+0x28 = config "global.addrCode"  (default 1)
    func  = 0x03 or 0x10 only (anything else is rejected)

FUN_140013f80 @ 0x140013f80 is the response framer: byte0 == slave, byte1 ==
func (bit7 = exception, len 5), FC3 len = bytecount+5, FC10 len = 8, CRC LE.

Register map -- FUN_140019c80 (the app's own bus simulator) + the base getters
FUN_140010730/750/770/790 (= addrOffset + 0x000/0x200/0x400/0x600):

    0x1000 .. 0x11FF   frame/01   settings
    0x1200 .. 0x13FF   frame/02   cell / runtime data
    0x1400 .. 0x15FF   frame/03   device info
    0x1600 + id        action / command space

frameAddrOffset comes from config "global.frameAddrOffset", parsed as hex,
stored as a short at protocol+0x158, written back as "{:04X}"; default 0x1000.

Inside a table the register address is the BYTE offset of the field within the
293-byte payload, and the count is bytes/2.  Evidence: the app's change-password
path writes 12 bytes to base+0x400+0x70, and settingPassword is at frame offset
118 == payload offset 0x70 in the datasource.  jk_probe.py decodes a real dump
both ways so one hardware run settles it beyond doubt.

Action ids seen in the RX dispatcher FUN_1400138e0, which switches on
(pending_register - addrOffset) - 0x600:
    0x0a 0x0c 0x0e 0x1c 0x1e 0x20 0x24 -> FUN_140013290
    0x22                               -> FUN_140013c60   (25 s timeout)
    0x26                               -> FUN_140014610   FIRMWARE UPGRADE
    default                            -> FUN_140013f80   (normal Modbus reply)

## 19. Firmware upgrade protocol -- SOLVED (XMODEM-128 checksum)

Start (FUN_140017260 @ 0x140017260):
    Modbus FC 0x10, register frameAddrOffset+0x626 (= 0x1626), 1 register,
    value 0x0000.  Timeout argument 0xFFFFFFFF -- this action never times out.
    The extra QByteArray (the image) is deliberately NOT appended for this
    register (explicit `!=` test in FUN_140015560).
    The call is refused if a request for that register is already pending.

Then the BMS reboots into its bootloader and the link becomes raw XMODEM.

Block sender (FUN_140015bf0 @ 0x140015bf0):
    SOH(0x01) | blockNo | ~blockNo | data[128] | checksum
    blockNo  = (counter + 1) & 0xFF          first block is 1
    data     = image[counter*128 : +128], last block padded with 0xFF
    checksum = sum(block[3:]) & 0xFF         classic XMODEM, NOT CRC
    counter += 1
    if counter == total: append EOT EOT EOT to the same write
    QThread::msleep(1) before writing

Receiver handling (FUN_140014610 @ 0x140014610): scan the received bytes
BACKWARDS for the first of {0x06 ACK, 0x15 NAK, 0x18 CAN}
    ACK -> counter == total ? success : send next block
    NAK -> counter = max(0, counter-1), resend
    CAN -> abort
Total blocks = ceil(len(image)/128)  (FUN_1400181b0, the progress bar maximum).

The bootloader (0x08000000-0x08003FFF) is in no .jkbms file, so the device side
could not be cross-checked; all of the above is from the sender.

## 20. Deliverables (jkbms_tool/)
    jk_firmware.py   .jkbms container + the app's 11-step validation chain
    jk_protocol.py   frame/field layout from the decrypted datasource
    jk_protocol_en.json / jk_protocol_zh.json   the decrypted datasource
    jk_modbus.py     Modbus RTU master + register map
    jk_upgrade.py    the XMODEM sender
    jkbms.py         CLI: list / --id info / --id + --firmware upgrade
    jk_probe.py      read-only hardware recon (never writes, never sends 0x18/0xFF)
    jk_sim.py        fake BMS over a pty, mirroring FUN_140019c80

Verified without hardware, over a socat pty pair:
    list  -> finds the simulated unit
    --id  -> decodes device info + runtime frame incl. per-cell voltages
    upgrade of 73-JK-PB2A16S20P-V19.04.jkbms: 770 blocks, received image is
    byte-identical to fw.image with 0xFF padding to the block boundary.

---------------------------------------------------------------------------
# ROUND 4 -- external corroboration (web)
---------------------------------------------------------------------------

## 21. Vendor Modbus specs -- re/docs/
Pulled from github.com/syssi/esphome-jk-bms/docs/pb2a16s20p/ :
  JK-BMS-RS485-Modbus-V1.0-pb2a16s20p.pdf  (+ .txt extraction)
  JK-BMS-RS485-Modbus-V1.1-pb2a16s20p.pdf  (+ .txt extraction)

These are JK's own register-map documents.  They confirm the map derived in
section 18 from FUN_140019c80 and the base getters, independently:

    0x1000  settings        RW   VolSmartSleep@0x000  CellConWireRes0@0x088
                                 DevAddr@0x108        switch bits@0x114
    0x1200  cell/runtime    R    CellVol0@0x000       BatWatt@0x094
    0x1400  device info     RW   ManufacturerDeviceID@0x000 (ASCII 16)
                                 HardwareVersion@0x010 (ASCII 8)
                                 SoftwareVersion@0x018 (ASCII 8)
                                 ODDRunTime@0x020      PWROnTimes@0x024
    0x1600  actions         W    LI-ION@0x0A  LIFEPO4@0x0C  LTO@0x0E
                                 Emergency@0x10  Timecalibration@0x12

=> The offset convention question from section 18 is SETTLED: offsets are
   relative to the 293-byte payload, not to the 300-byte frame.  Every field
   above lines up exactly with the datasource-derived map in jk_protocol.py.
   No hardware run is needed for this.

=> The action ids the app's RX dispatcher special-cases (0x0a/0x0c/0x0e ->
   FUN_140013290) are exactly the one-key chemistry presets in the vendor doc.

=> Register 0x1626 (firmware upgrade) appears in NEITHER spec revision.  It is
   undocumented; the vendor action list stops at 0x12.

## 22. The arming command, corroborated byte for byte
github.com/jblance/mpp-solar issue #510 quotes a capture of the real Windows
app's firmware-update command on RS485-1:

    01 10 16 26 00 01 02 00 00 D6 97

jk_modbus/jk_upgrade generate exactly these bytes (verified).  That is an
independent confirmation of slave=1, FC=0x10, reg=0x1626, count=1, value=0.
That issue contains nothing about what follows the write -- no block format,
no logs.  The XMODEM sequence in section 19 is still only recoverable from
jk-bms-monitor.exe.

## 23. Bootloader -- still not public
Searched for public dumps, SWD/RDP unlock writeups and existing flashers:
nothing.  No JK bootloader image and no written spec for the post-arming
transfer exists publicly as far as the search reached.

Secondary: several sources describe a hardware recovery path -- hold RST while
plugging the USB-TTL cable, the device enumerates as "STM32 BOOTLOADER" (the
built-in ST system bootloader, speakable with stm32flash).  Useful as an
anti-brick route, but it needs a full image INCLUDING 0x08000000-0x08003FFF,
which no .jkbms file contains.  Sourcing on this was weak (SEO content farm);
treat as unverified.

## 24. Model
NOT determined.  The user's tree carries firmware for 13 models (PB2A16S20P,
PB2A16S15P, PB1A16S15P, PB1A16S10P, B2A8S30P, BD6A24S8P, B2A8S20PHCR,
B2A8S20P, B2A24S20P, ...); which board is on the bench is unknown.  It does
not matter for the register map: the app's datasource defines one family,
"JK-BXAXS-XP", covering all of them.  manuDeviceID at register 0x1400 will
report it, and that string is what the compatibility gate compares against.

## 25. MCU identification + bootloader-dump route
Target MCU (from firmware static analysis):
  Cortex-M3, STM32F1 family (or register-compatible clone GD32/APM32/CKS)
  - RCC 0x40021000, FLASH 0x40022000, GPIOA 0x40010800 = F1 signature
  - >=128 KiB flash (app ~96 KiB above the 16 KiB bootloader), >20 KiB RAM used
  - app self-programs flash (FLASH_KEYR keys 0x45670123/0xCDEF89AB present)
  - app does NOT touch option bytes/RDP (OPTKEY 0x08192A3B/0x4C5D6E7F absent)
Note: F1 ROM bootloader is UART-only (no USB DFU) -> the "USB STM32 BOOTLOADER"
web story does not apply to this chip. ST ROM bootloader needs BOOT0=high; the
JK 0x5AA5 path enters JK's OWN bootloader, a different thing.
Full bench procedure to attempt a bootloader dump via stm32flash (and the one
mass-erase trap to avoid) is written up in re/docs/bootloader-dump-procedure.md.
Not required for the tool; confirmation-only.

## 26. First live-device probe (2026-09-07) + two corrections
Ran jk_probe.py against the real BMS on /dev/ttyUSB0. Results:
  - Transport CONFIRMED: Modbus RTU, 115200 8N1, slave address 1.
  - Model CONFIRMED from the device itself: register 0x1400 count 8 returned
    "JK_PB2A16S20P" (so the model really is PB2A16S20P; earlier we had refused
    to assume it -- now it is measured, not assumed). See [[jk-bms-model]].
  - Read path CONFIRMED for the device-info table's first field.

CORRECTION A -- the register map is WORD-addressed, not byte-addressed.
  The probe's first table chunk (reg 0x1400, 120 regs) succeeded but the second,
  at reg 0x14F0, was rejected by the device. 0x14F0-0x1400 = 240; as a *word*
  index that is byte 480, past the 293-byte (147-word) table -> correctly
  refused. A byte-addressed map would have put byte 240 there and answered.
  So: wire_register = table_base + payload_byte_offset//2, 2 bytes per register.
  The vendor PDF's per-field "address" column is the byte offset (0x0000,0x0004,
  0x0008 for consecutive UINT32s); halve it for the register. Our earlier
  byte-offset reading (from the change-password static analysis) was wrong.
  Fixed in jk_modbus.read_frame/read_payload, jkbms.Device.read_field,
  jk_probe.dump_tables, and jk_sim (the sim had the same byte-model bug, which
  is why the pre-hardware end-to-end tests passed). manuDeviceID at byte 0 reads
  the same under both models, which is why 'list'/model detection worked anyway.
  Action/command registers (upgrade arming = 0x1626) are slot numbers, NOT byte
  offsets -> unchanged, still corroborated. Settings *writes* would need the same
  //2 conversion, but the tool exposes no settings-write path today.

CORRECTION B -- dropped the hard pycrypto dependency (no aarch64 wheel).
  AES-256-CBC now goes through jk_aes.decrypt_cbc, which prefers pycryptodome
  then cryptography then a bundled pure-Python AES-256 (jk_aes.py). Verified
  byte-identical across all three backends on 73-JK-PB2A16S20P-V19.02.jkbms, and
  the pure-Python path alone loads+inflates the real firmware. So jkbms.py now
  imports and runs on aarch64 with zero binary crypto installed.

Re-tested end-to-end against the corrected word-addressed sim: list, info,
info --cells (full 293-byte tables incl. SN/date/all cells), check-only, and a
--force upgrade (767 blocks, received image byte-identical). All pass.

## 27. Second live probe (2026-09-07) -- FC03 quantity cap, and the fix
The word-addressing fix alone did NOT make the tables read: the second probe
returned exception 2 (illegal data address) on the bulk read. Data points from
the two live runs, all at register 0x1400:
  - count 8   -> OK (manuDeviceID)          (both runs)
  - count 120 -> OK (240 bytes)             (run 1, first chunk)
  - count 125 -> exception 2                (run 2)
  - start 0x14F0 (word 240) -> no response  (run 1, past any table)
The vendor Modbus PDF shows frame/03 fields extending to ~byte 268 (word 134),
so the table is NOT short -- the device simply **caps FC03 quantity at ~120
registers** and answers exception 2 above it. (JK field offsets in the PDF match
our datasource exactly: manuDeviceID@0, HardwareVersion@16, SoftwareVersion@24,
ODDRunTime@32, PWROnTimes@36, UART1@178 -- so the Modbus map == native frame.)

FIX -- read fields individually, never the whole frame.
  jkbms.Device.info()/cells() now read each displayed field on its own (a small
  register range, <=32 regs), exactly as the app does and as the live 'list'
  path already proved reliable (manuDeviceID, count 8). A field the device does
  not map (e.g. protocolVer at word 146, past the readable range on some models)
  comes back as None instead of failing the whole read. jk_modbus.MAX_READ_CHUNK
  = 32; read_frame is now diagnostic-only and stops at the first refused chunk.
  Verified: with the sim capped at 120 regs (jk_sim MAX_READ_QTY), list/info/
  cells all succeed; trace shows small reads 01 03 1400 0008, 01 03 140c 0004, ...

jk_probe.py is now a one-run characteriser: a max-read sweep (reports the exact
quantity cap) + a binary-search table-extent probe + per-field decode. Run it
once on the real BMS to record the cap and each table's true length.

Raspberry Pi "no response at all" note (slower host, same cable/adapter):
  added serial robustness -- 0.10 s post-open settle + buffer flush, comms
  retries (default 2, only on lost/garbled replies, never on a Modbus
  exception), and --timeout/--retries/--trace flags on jkbms.py and jk_probe.py.
  RESOLVED: with the settle + retries the probe now works on the Pi too.

## 28. Third live probe (2026-09-07) -- addressing is BYTE, not word. Correction B reverted.
The full table dump from the 3rd probe overturned §26's word-addressing call.
read_frame (which chunked by 32 registers) produced data that REPEATS every 128
bytes; the tell is an overlap test on two real reads:
  R0 = FC03(0x1400, 32),  R1 = FC03(0x1420, 32)  ->  R0[32:64] == R1[0:32].
That equality means register 0x1420 returns byte offset 0x20 (32), i.e. the map
is BYTE-addressed: register == table_base + byte_offset, and a read of C regs
returns 2*C bytes = payload[B : B+2C]. (Word addressing would give R1 = byte 64,
no overlap.) Reassembling byte-addressed, the real device decodes cleanly:
  manuDeviceID  JK_PB2A16S20P   hardwareVersion 15A   softwareVersion 15.41
  manufactureDate 241103 (2024-11-03)   deviceSN 40705491867
-- real values at their exact datasource offsets, which also confirms the
datasource/vendor-PDF offsets are right. So the app runs firmware 15.41 / HW
"15A" (the 15.x line, NOT V19). This matches the vendor PDF, whose "address"
column IS the byte offset and doubles as the register offset -- no /2.

Why §26 was wrong: run-1's failure at register 0x14F0 was NOT "word 240 past the
table"; it was a byte-addressed read of byte offset 240, count 27 -> bytes
240..293, which overran the table's REAL end (~244 bytes) -> exception/silence.
I mistook a table-overrun for an addressing signal. The word "fix" then read the
wrong registers (softwareVersion at 0x140C instead of 0x1418) and read_frame's
32-word chunks manufactured the 128-byte aliasing.

Device quirks now established (all from the 3rd probe):
  * BYTE addressing: register = table_base + byte_offset; FC03 count C -> 2C bytes.
  * FC03 quantity cap ~122 registers: count 122 OK (244 B), 124 -> exception 2.
  * Reads must be word-aligned: an odd register/offset is refused (extent probe
    hit exception 2 at reg base+1). read_payload word-aligns down and trims.
  * frame/03 is ~244 bytes on this unit (shorter than the 293-byte max frame);
    reading past the end answers exception 2 / silence.

Fixes (reverted to byte model, kept the good parts):
  jk_modbus: table_register(table, byte_off) = base+byte_off; read_payload is
  byte-addressed + word-aligned + chunked under MAX_READ_CHUNK(32); read_frame
  is byte-addressed, chunked, and stops at the first refused chunk (diagnostic).
  jkbms.Device.info()/cells() already read fields individually (each <=32 regs,
  under the cap) -- kept. jk_probe: max-read sweep + byte-addressed even-offset
  extent probe (bounded to the 0x200 table window). jk_sim: byte-addressed again,
  with the ~122 quantity cap, odd-offset refusal, and table-end refusal, so it
  faithfully reproduces the device. cmd_list uses retries=0 while scanning empty
  addresses (present device answers first try) to stay fast.
Verified: unit test reads the real frame/03 bytes and lands model/hw/sw/date/SN
on the correct registers (0x1400/0x1410/0x1418/0x1448/0x1450) with no odd reads;
list/info/cells/check/--force-upgrade all pass against the byte-addressed sim.

## §29  Modbus numbers are BIG-ENDIAN (the datasource is little-endian) -- CONFIRMED live

The byte-addressing (§28) put strings right (model/SN/date) but every multi-byte
NUMBER came out garbage: `jkbms.py --id 1` showed pack voltage 349175.808 V,
current -1460273 A, temps -972 degC, cells 0.269 V (real pack is a 16S LFP at
~3.328 V/cell). Cause: the datasource (jk_protocol_en.json) declares scalars
little-endian because it describes the native 55AAEB90 UART frames, which ARE
little-endian. But the Modbus interface serves holding registers BIG-ENDIAN
(standard Modbus word order), full field width. decode_field hard-coded "<", so
each multi-byte number was byte-reversed.

Proof from the live 4th-probe dump (2026-09-07), decoding big-endian:
  cells        bytes 0d 01 -> 0x0D01 = 3329 -> 3.329 V   (LE gave 0x010D=269=0.269)
  pwrOnTimes   00 00 00 09 -> 9 power-ons                 (LE gave 150994944)
  oddRunTime   03 6b 0f a0 -> 57 348 000 s = 663.75 days  (device built 2024-11-03,
                                                           ~673 days prior -- matches)
  batVol@144   00 00 d0 15 -> 53269 -> 53.269 V  = 16 x 3.33 V
  batCurrent   ff ff f5 a8 -> -2.648 A (idle)
  SOC          95 %;  remain 296.8 Ah / full 312.0 Ah = 95.1 % (self-consistent)
  cellVolAve   3.329 V   maxVoltDelta 0.003 V
  tempMos 23.5, batTemp1 21.8, batTemp2 22.0 degC (room temperature)
All physically consistent -- the datasource FIELD OFFSETS were right all along;
endianness was the only bug. Single-byte fields (SOC) were right by luck.

Fix (transport-aware endianness, native path unchanged):
  jk_protocol: _SCALAR codes lost their "<" prefix; decode_field(f, frame,
    byteorder="<") and Protocol.decode(..., byteorder="<") take the order from
    the caller. Constants NATIVE_BYTEORDER="<", MODBUS_BYTEORDER=">".
  jkbms.Device.read_field: decodes with P.MODBUS_BYTEORDER (">").
  jk_probe.dump_tables: proto.decode("03", frame, P.MODBUS_BYTEORDER).
  jk_sim: build_device_info/build_cell_info now encode ">" so the simulator
    serves big-endian like the real device (was silently modelling the OLD bug).
Verified: the live dump decodes to the physical values above; end-to-end
list/info/cells against the big-endian sim returns exactly the sim's values
(52.8 V, -12.345 A, 651 W, SOC 87, cells 3.300..3.315).

## §30  AES backend: system libcrypto via ctypes (zero-install fast path for weak/exotic hosts)

Problem: on a 32-bit armv7l Raspberry Pi 3, `jkbms.py --firmware ...` "appeared
to hang" -- it was the pure-Python AES decrypting the firmware (F.load runs
before cmd_check prints anything). Timing: 58 KB encrypted = 3623 blocks -> ~3 s
on x86, minutes on a Pi3. And the obvious fixes don't apply cleanly there:
`cryptography` needs Rust, `pycryptodome`/pycrypto need a C toolchain (no armv7l
wheel), so pip drags in a build.

Fix: added a 3rd backend that calls OpenSSL's libcrypto through stdlib ctypes --
no install, no compiler, present on essentially every system. jk_aes now tries
pycryptodome -> cryptography -> **openssl (ctypes)** -> pure-python.
  _load_libcrypto(): ctypes.util.find_library("crypto") + common sonames
    (libcrypto.so.3 / .1.1 / .1.0.0 / .so / .dylib / win dlls); cached; checks
    EVP_DecryptUpdate + EVP_aes_256_cbc are present.
  _try_openssl(): EVP_CIPHER_CTX_new / EVP_DecryptInit_ex(EVP_aes_256_cbc) /
    EVP_CIPHER_CTX_set_padding(ctx,0) / DecryptUpdate / DecryptFinal_ex / free.
    Padding OFF (the container manages its own length via raw_len), so output is
    byte-identical to the pure-Python path.
  Registry refactored to (name, available_probe, decrypt_fn); backend_name() and
  decrypt_cbc() both consult the probe. jkbms._load_firmware warns only when the
  active backend is pure-python (i.e. libcrypto ALSO missing) and suggests
  installing openssl (apt/pacman/apk), not a pip wheel.

Verified: all four backends pass the FIPS-197 AES-256 KAT; openssl output ==
pure-python output on the real 73-JK-PB2A16S20P-V15.41.jkbms; with pip crypto
absent the selector returns "openssl", with nothing at all it returns
"pure-python" and still decrypts correctly.

## §31  Firmware upgrade: gate verified live; device already at newest available V15

The compatibility gate (jk_firmware.check_compatible) was exercised against the
real device identity (model JK_PB2A16S20P, version 15.41). Order: major, then
minor (strictly greater unless --force), then model. Results for the four
HW V15/JKBMS-PBXX-V15.41 files:
  69 PB1A16S10P, 70 PB1A16S15P, 72 PB2A16S15P (wrong model): REJECT on minor
    (all are 15.41, == device) without --force; with --force -> REJECT on model.
  73 PB2A16S20P (correct model): REJECT on minor without --force; --force -> PASS.
So the model gate works (reached only past the minor gate, via --force, since the
files share the device's exact version). The device is already at 15.41, the
newest V15 firmware on hand; a genuine (gate-passing, non-force) upgrade needs a
JK_PB2A16S20P build with minor > 41. The only real-flash path with current files
is `--force` re-flash of file 73 (same version) -- still UNTESTED on hardware
(the XMODEM sender has only run against jk_sim). Not attempted; brick risk.
73's image: 92152 bytes, 720 XMODEM blocks, SP=0x20004e00 RESET=0x0801866d,
built May 13 2025, not time-limited.

---------------------------------------------------------------------------
# ROUND 5 -- the write side, and the rename to jkctl (2026-09-08)
---------------------------------------------------------------------------

## §32  Three protocol facts the read-only tool never had to face

Building the write path and the full command surface turned up three things
the read paths had hidden.  None needed hardware to establish; all three come
from the datasource and the vendor register map, and two of them were latent
bugs in the read path as well.

(a) BITMAPS FOLLOW THE TRANSPORT'S BYTE ORDER, like every other number.
    decode_field had `int.from_bytes(raw, "little")` hard-coded for the bm/bv
    kinds, a survivor of the pre-§29 assumption that everything was
    little-endian.  The vendor Modbus map lists sysAlarm at 0x00A0 as a UINT32
    and switchStatus at 0x0114 as a UINT16, so over Modbus they arrive
    big-endian like everything else -- and read little-endian, a 4-byte alarm
    word reports entirely the wrong protections (bit 11, cell under-voltage,
    reads as bit 19, "modify password in time").  The read paths never caught
    it because jkbms.py only ever displayed scalars and arrays, never a
    bitmap.  Fixed: decode_field/encode_field take the byte order for bm/bv
    too, and the sim serves them big-endian.

(b) A FIELD NARROWER THAN A REGISTER CANNOT BE WRITTEN ON ITS OWN.
    The smallest thing FC16 can carry is one 16-bit register, and several JK
    settings are half of one:
      uart1ProtoNo @0x00B2 / canProtoNo   @0x00B3   (frame/03)
      rcvTime      @0x0104 / rfvTime      @0x0105   (frame/03)
      lcdBuzzerTrigger @0x00E4 / dry1Trigger @0x00E5
      timeSmartSleep @0x118 (frame/01), whose neighbour is enableFlags
    The vendor register map shows this directly -- it lists 0x00B2 as one
    2-byte entry holding two UINT8s.  A naive write of one would zero its
    neighbour, which for the protocol selectors means silently switching the
    CAN port to protocol 0 while setting UART1.  Device.write_register now
    reads the containing word first and replaces only the field's own bytes.
    Note this makes a settings write a read-modify-write on those fields, so
    it is NOT atomic; nothing else on the bus may write them concurrently.

(c) FRAME/01 vs FRAME/04.  The datasource has two settings tables, 01 (up) and
    04 (down), and they diverge past payload offset 280: 01 has
    tmpStartHeating@278 tmpStopHeating@279 timeSmartSleep@280 enableFlags@281,
    04 has ...timeSmartSleep@280 tmpBatDCHUT@281 tmpBatDCHUTPR@282.  The
    vendor Modbus document's 0x1000 block matches 01, not 04 (switchStatus at
    0x0114, TIMSmartSleep at 0x0118).  So the Modbus settings region is
    frame/01 in BOTH directions; frame/04 belongs to the native UART download
    frame and is not used over Modbus.  jkctl reads and writes 0x1000 through
    table 01 only.

## §33  Access: which registers JK marks writable

Transcribed from JK-BMS-RS485-Modbus-V1.1.txt, and the only hand-copied data
in the tool (jkctl/registers.py ACCESS):
  0x1000 (frame/01)  RW throughout -- every one of its 46 fields.
  0x1200 (frame/02)  R throughout.
  0x1400 (frame/03)  R, except: uart{1,2,3,4}ProtoNo, canProtoNo,
                     lcdBuzzerTrigger, dry{1,2}Trigger, lcdBuzzer{Trigger,
                     Release}Val, dry{1,2}{Trigger,Release}Val,
                     dataStoredPeriod, rcvTime, rfvTime.
                     NOT the identity fields, NOT settingPassword, NOT
                     bluetoothName/Pwd -- the document marks those R.
  0x1600             W only, and unreadable.
A field absent from that list is treated as read-only rather than guessed at.

## §34  Still not proven on hardware

Unchanged from §31, and now larger because the tool does more:
  * No write of any kind has reached the real device.  Every FC16 path --
    settings, switches, protocol selectors, the action slots -- has run only
    against jk_sim/jkctl.simulator.
  * The FC16 quantity cap is unmeasured (the FC03 one is ~122).  Writes are
    chunked at 16 registers, well under it.  `jkctl probe --probe-writes`
    measures the real ceiling by rewriting registers with the values just read
    from them, so an interrupted sweep changes nothing.
  * Whether Modbus writes are gated by settingPassword (§8's unresolved
    "Dynamic key is invalid!") is unknown.  jkctl deliberately implements no
    authentication handshake: a refused write reports the device's own Modbus
    exception code verbatim, which is the datum that would settle it.
    First live write to try: read a setting, then write it back unchanged.
  * The action slots (0x1600) have never been fired.  Their addresses are from
    the vendor document; what value each takes is inferred from the slot
    existing at all.
  * The firmware transfer is unchanged from §19/§31 and still unrun on metal.

## §35  The tool: jkbms_tool -> jkctl

Restructured along the lines of the neighbouring alfenctl, since it is the
same shape of project: a src/ package with a console script, one module per
subject none of which prints, a Command/Need dispatch table, a TOML config
file, one JkError base with one handler in main(), ruff + ty + pytest + CI.

  jk_modbus.py   -> jkctl/modbus.py      + write_payload/write_action, a link
                                           seam for tests, named exception codes
  jk_protocol.py -> jkctl/protocol.py    + encode_field, transport-aware bitmaps,
                                           mi/mx/dv carried on Field
  jk_firmware.py -> jkctl/firmware.py    unchanged
  jk_upgrade.py  -> jkctl/upgrade.py     + a Reporter seam for the progress bar
  jk_aes.py      -> jkctl/aes.py         unchanged
  jk_probe.py    -> jkctl/probe.py       library + a write-cap sweep
  jk_sim.py      -> jkctl/simulator.py   + FC16, realistic defaults, reboots
                                           back to Modbus after EOT
  jkbms.py       -> jkctl/cli/           23 commands
  new: registers.py (the catalog), values.py (coercion), device.py (the facade),
       identity.py / runtime.py / settings.py / controls.py (one subject each),
       config.py, report.py, progress.py

177 tests, no hardware: the Modbus master is wired straight to the simulator in
memory, so a CLI test exercises the real framing, chunking and decoding.

---------------------------------------------------------------------------
# ROUND 6 -- the read planner, the unnamed action slots, the log-code table
---------------------------------------------------------------------------

## §35b  Reading by spans instead of field by field

`Device.snapshot()` issued one Modbus transaction per field: 45 for the
settings table, 68 for the runtime one, 44 for device info.  That is
tolerable for one `status` and wrong for `--watch`, for `log --interval 1`,
and for a browser polling four boards on a shared wire.

The fix is to plan the read from the *bytes* rather than from the fields:

  * `device.plan_spans()` merges the wanted registers into byte ranges,
    joining any two closer together than SPAN_GAP (32 bytes) -- a Modbus
    round trip costs more than sixteen registers of payload nobody wanted.
  * `Bus.read_span()` reads a range in <=MAX_READ_CHUNK (32) register chunks,
    and with `partial=True` treats a *Modbus exception* (not a timeout) as
    the end of the mapped range: it binary-searches the largest read that
    still fits, takes it, and stops.  Nothing past a register the device
    refuses is going to be served either.
  * `Device.read_fields()` decodes every field wholly inside what came back,
    and caches the table's measured extent per device, so the next read is
    clipped instead of walking into the same refusal.

Measured against the simulator, per table: 5 transactions warm (45/68/44
before), 5-10 cold while the extent is being found once.  `status --watch` at
its 2 s beat went from 68 transactions a redraw to 5.

Also fixed on the way past: a read that fails *before anything has been read*
now raises, naming the address.  It used to be swallowed per field, so a
board that had gone away and a board with a very short table produced the
same empty document.

## §36  The three action slots JK's register map does not list -- SOLVED

The vendor document's action list stops at 0x12.  The application's RX
dispatcher (FUN_1400138e0, §18) special-cases 0x0a 0x0c 0x0e 0x1c 0x1e 0x20
0x22 0x24 0x26, which says only how a *reply* is handled.  What each slot
*is* comes from the other end: the buttons.

  FUN_14006a310(page, widget, SLOT, ...) is the app's "fire an action slot"
  call.  Its callers are the click handlers, and the page constructor
  (FUN_140065... at all_exe.c:79190ff) both creates each JRoundButton with
  its tr() label and connects it to its handler, so label -> handler -> slot
  reads straight off:

    page+0x50   "Shutdown Board"      FUN_140069360   slot 0x04
    page+0x58   "Restart Board"       FUN_140069460   slot 0x16
    page+0x60   "Factory Restore"     FUN_140069030   slot 0x18
    page+0xc0   (clock)               FUN_140068c10   slot 0x12
    page+0x118  "Erase All Data"      FUN_1400696e0   slot 0x1a
    page+0x120  "Erase History Data"  FUN_140068ee0   slot 0x18, behind a
                                                      JShadowDialog
    (emergency)                       FUN_140068... slot 0x10, with JK's own
                                      "only for use in emergencies" warning

The control is the first and fourth lines: 0x04 for "Shutdown Board" and 0x12
for the clock are what the vendor document already says, recovered by the
same reading.  So 0x16, 0x18 and 0x1a are as good as those two.

Two caveats recorded rather than resolved:
  * "Erase History Data" fires 0x18 with the same item pointer "Factory
    Restore" uses.  Either the app has a copy-paste bug or the two handlers
    belong to differently-laid-out classes; either way 0x18 is named for the
    button whose own handler is unambiguous.
  * 0x1c, 0x1e, 0x20, 0x22 (25 s timeout) and 0x24 are still unfired by this
    page.  They are dispatched but nothing on this screen sends them.

Implemented as `restart`, `factory-restore` and `erase-data` -- ordinary
entries in registers.ACTIONS, each with a warning and a confirmation, and
each unfired at real hardware.

## §37  The stored fault records: names recovered, address still open

`detailLogsCount` in frame/02 counts records the board keeps, and the
datasource's table 06 describes each one: begin index u16, count u8, then
twelve 24-byte records of RTC stamp, log code, a four-bit switch bitmap, the
highest and lowest cell number and voltage, pack voltage and current,
remaining and full capacity, three temperatures and the heater current.
3 + 12*24 + 2 = 293, which is the payload every table measures out to -- the
layout checks itself.

**The code names are recovered.**  FUN_140008870 builds the map the app uses
to turn a code into a line, one QMetaObject::tr per entry, and it ends with
two 32-iteration loops that name the per-cell protections.  Extracted whole:
73 fixed codes 1..73 with no holes, plus codes 100-131 "Cell N over charge
protection" and 200-231 "Cell N over discharge protection".  Shipped as
`src/jkctl/logcodes.json`; `jkctl log-codes` prints it and needs no hardware.
These are also the vendor's own words for the protections the sysAlarm bits
name, which makes the table useful on its own.

**The address is not.**  The four base getters (FUN_140010730/750/770/790)
return +0x000/0x200/0x400/0x600 and stop: frames 01, 02, 03 and the action
space, and nothing for 05 or 06.  The app reads them over its other channel.
Whether the Modbus interface maps them anywhere is unanswered; +0x800 and
+0xA00 are the candidates by the same +0x200-per-frame pattern.

So `jkctl history` reads the candidate, decodes whatever comes back, and says
plainly when a board does not answer there -- which is a different sentence
from "this battery has no history".  `jkctl probe` now sweeps both candidates
read-only, so one run on real hardware settles it.  The decoder is tested
against synthetic records, which is exactly what is known.

## §38  Protocol names, and the two UART selectors that were never writable

The shipped UART/CAN/trigger lists are the vendor's own and are in **Chinese
in both** the en_US and the zh_CN datasource -- an English-speaking installer
choosing "005" in JK's application is choosing between forty-two lines they
cannot read.  `protocols.json` now carries an `en` beside each `name`, and
`jkctl protocols list` shows both columns.

Separately: `jkctl protocols set uart3|uart4` could never have worked.  The
commands existed, `identity.py` read the fields, and `registers._RW_INFO_FIELDS`
did not list them -- so `Device.write_register` refused them as read-only at
the last moment.  Re-reading the document settles it: it describes a two-port
board and lists UART1MPRTOLNbr, UART2MPRTOLNbr and the CAN selector as RW and
nothing else.  §33's claim of uart{1,2,3,4} was wrong.  `set` now offers the
three the document marks writable, `show` still reads all five, and a test
holds the offered list to the catalog.

## §39  What the vendor calls things, where it is wrong

§38 gave the UART/CAN/trigger lists an `en` column.  Three of those English
names were transliterations of the Chinese rather than the company's own name,
and one of them named the wrong company outright:

| # | JK's Chinese | was | is |
|---|---|---|---|
| CAN 004 | `维克多_CAN通信协议l_20170717` | Victor | **Victron Energy** |
| UART 007 | `日月元逆变器与485通信协议20200325` | Riyueyuan | **Voltronic Power** |
| CAN 011 | `鹏城CAN通信协议V01` | Pengcheng | **Luxpowertek** |

JK's own English protocol list (jkbms.net, "inverter-bms-communication")
settles all three by file name and date: entry 6 is
`CAN-BUS_BMS_Protocol_201707` against **Victron Energy**, which is the file the
Chinese entry dates `20170717`; entry 10 is
`Voltronic Power Inverter and BMS 485 communication protocol 20200325`, the
same date as the Chinese entry; entry 14 is
`Luxpowertek Battery CAN Protocol V01`, the same version.  The company names
corroborate: 日月元科技 is Voltronic Power's own Chinese name, and 深圳鹏城新能
科技 is Luxpowertek's.  `维克多` is the ordinary transliteration of "Victor",
which is how it became one -- but the protocol behind it is Victron's, and
Victron is the one brand a JK pack is most often wired to.

**The en_US datasource is partly Chinese.**  Not only the protocol lists:
fifteen items of the runtime table (`系统报警标志`, `运行时间`, the six
protection release-time fields, ...), the six temperature-sensor bits
(`MOS温度`, `电池温度1`..`5`) and three Off/On option pairs (`开启`/`关闭`)
arrive untranslated, so an English page drew Chinese rows between English
ones.  `protocol_en.json` is shipped exactly as the application decrypts it,
so `registers._ENGLISH_LABELS` and `_ENGLISH_BITS` supply the English on the
way into the catalog.  Only what JK left in Chinese, or got wrong, is
replaced; awkward vendor English ("Remain Battery" for SoC) stays, because a
label that cannot be found on JK's own screen is worse than an awkward one.

**Six system-alarm bits carried another bit's state word.**  The datasource
writes a bit as `name:when-clear;when-set`, and in `sysAlarm` the *set* words
of bits 1, 2, 3, 5, 6 and 7 belong to some other bit:

    1  Protection - MOS over temp.        Normal;Over Voltage
    2  Cell count is not equal to settings Normal;Over Current
    3  Protection - current sensor anomaly Normal;Over Temp.
    5  Protection - Battery over voltage   Normal;Too Large
    6  Protection - Charge over current    Normal;Error
    7  Protection - Charge short current   Normal;Timeout

The names are right and only the state words are shifted, which is how a
tripped MOS over-temperature protection came to display "Over Voltage" -- the
one thing on that card that has to be right.  Each is now set to what its own
bit means.  Bit 7 is a short *circuit*, not a short "current"; bit 14 spells
the same protection correctly.

**`tempSensorAbsent` is two names that disagree.**  The machine name says a
raised bit is a probe that is *absent*; the Chinese label `传感器存在标志` says
it is one that is *present*.  Unresolved, and it needs a unit with every probe
connected to settle.  What the datasource does settle is which bit is which --
bit 0 is the MOS probe, bits 1..5 the five battery probes -- and both the
doctor and the dashboard numbered them from one, so a missing MOS probe was
reported as "sensor 1" and sent somebody to the wrong wire.  Both now name the
probe.

---------------------------------------------------------------------------
# ROUND 7 -- firmware upgrade re-audit before publishing (2026-09-26)
---------------------------------------------------------------------------

## §40  The bytes are proven; the device's defence against bad bytes is not

A pre-publication re-check of the whole upgrade path, since it has never run on
metal and a wrong flash bricks a battery.  Two questions: does the tool send
the wrong data, and does the BMS check what it is given.

**Container -> image, verified to the byte.**  `firmware.load()` decrypts and
inflates `73-JK-PB2A16S20P-V19.02.jkbms` and extracts an image whose SHA-256
(`964aeca3...af4a4`, 98131 bytes) is *identical* to `fw_PB2A16S20P_19.02.bin`,
the image carved independently from a vendor build in round 2.  So the bytes
`flash` puts on the wire are exactly the vendor's.  The container is fully
accounted for, too: decrypted plaintext = `uint32 raw_len || zlib stream`, and
the payload = image + a 12-byte trailer (`int64 build_ms || int32 valid_hours`)
with zero bytes left over (the `05 05 05 05 05` after the zlib stream is just
AES PKCS#7 padding).  There is **no signature and no whole-image checksum**
anywhere in the file, and the app's 11-step gate (§10) validates only the
metadata -- model, version, expiry -- never the image content.

**XMODEM sender, re-derived and re-checked.**  Rebuilt every one of the 767
blocks of the 19.02 image independently: `SOH | n | ~n | data[128] | sum8`,
block numbers wrap mod 256 (block 767 -> number 255), last block 0xFF-padded,
EOT x3 appended to the last block's write.  Reassembling the data fields
reproduces the image exactly with the expected 45-byte 0xFF tail.  End-to-end
against the simulator over a socat pty (V15.41, `--force`): 720/720 blocks, the
`--save-image` capture is byte-identical to the source.  This matches §19 and
the byte-for-byte-corroborated arming write of §22.

**Does the BMS self-check after upload?  Unknown, and unknowable from what we
have.**  The receiver is the bootloader at `0x08000000-0x08003FFF`, which is in
no `.jkbms` and has never been dumped (see `bootloader-dump-procedure.md`).
The application image we *do* have (`fw.bin`, `0x08004000`+) references the
STM32 hardware CRC unit (`0x40023000`) **zero** times and does not touch the
option bytes (no `OPTKEY`), so nothing in the application points to a
self-integrity check; but the application is not the code that receives and
commits a new image, so this is only weak, indirect evidence.  On the wire the
sole integrity guard is XMODEM's 8-bit additive per-block checksum -- enough to
catch most random corruption of a block, far too weak to be relied on, and no
end-to-end check of the whole image at all.

**The hand-off, and why a dead app cannot re-arm.**  The application's whole
role in an upgrade is a hand-off, confirmed in `fw.bin` by static bytes:
`0x5AA5` (the magic, 8 occurrences), the backup-register base `BKP`
`0x40006C00`, and the `AIRCR` reset key `0x05FA0004` are all present -- i.e. it
writes the magic to a backup register and issues a software reset, matching §13
(cmd `0xff` -> FUN_08007000 -> FUN_080083ca) and §14.  `FLASH_KEY1/KEY2` are
present too, so the app *can* self-program flash, but for its own settings, not
to receive a firmware image.  The upshot for recovery: the arming path runs in
the application, so once the app is dead there is nothing to receive the RS485
arming write or set the flag -- the upgrade cannot be re-triggered over the
wire.  Whether the bootloader stays resident and waits for XMODEM when the app's
vector table is invalid is unknown (it could not be read).

**Brick model.**  A `.jkbms` holds only the application (`0x08004000`+), never
the bootloader, so a wrong/interrupted flash is *expected* to leave the
bootloader intact and only the app broken -- expected, not measured.  Recovery
then is the STM32 ROM bootloader (BOOT0 high, `stm32flash`), not JK's: with RDP
off it can rewrite the application region from the extracted image and leave the
JK bootloader alone; with RDP on, clearing protection mass-erases the JK
bootloader too, and recovery needs a full image no `.jkbms` contains (§23, §25,
`bootloader-dump-procedure.md`).

**Conclusion.**  The risk is not that `jkctl` sends the wrong bytes -- it sends
the vendor's exactly.  The risk is that the BMS is not known to reject bytes
that are wrong for any other reason (a truncated transfer, a checksum
collision, a mismatched-but-well-formed image), because its receiver could not
be examined and the path has never touched hardware; and that if the app is
left dead, re-arming needs the bench, not the wire.  Recorded for the user in
the CLI (`firmware flash` prints it before every flash), the web UI (a standing
caveat on the Flash card), the README and `docs/reference.md`, and in
`devicectl-unification.md`.  Settling it for real needs the bench dump.
