# Extracting the JK BMS bootloader over the STM32 ROM bootloader

This procedure was originally written to obtain the receiver side of the
firmware-upgrade protocol. A full-flash capture has now recovered one example:
JK bootloader V2.0.2, built 2023-10-18, from a `JK_PB2A16S20P` running V15.41.
The application-based dumper obtained it without entering the STM32 ROM
bootloader; the invasive procedure below remains the fallback when the
application cannot run.

Read the whole document before touching hardware. The one irreversible mistake
is easy to avoid and is called out in bold everywhere it matters.

---

## 0. What we are trying to get, and why it may not be gettable

Every audited application (`0x08002000` upward) is what ships inside its
`.jkbms` file. The 8 KiB below it is absent: application callsites show
persistent pages at `0x08001800` and `0x08001c00`, with JK's bootloader below
them. Reading all flash is the only way to establish the exact code/data
boundary and inspect the XMODEM receiver.

Whether it can be dumped comes down to a single option-byte setting on the MCU:
**readout protection (RDP)**.

- **RDP disabled** → the ROM bootloader's *Read Memory* command works → we read
  all of flash, bootloader included. Easy.
- **RDP enabled** → *Read Memory* is refused, and the only way to clear
  protection is a **mass erase that destroys the flash first** (see §6). The
  easy path is then gone and extraction becomes an invasive glitching project
  that is out of proportion to the need.

We cannot know the RDP level without the device. **Attempting a read is
non-destructive** — it either returns bytes or is refused. So a first attempt
costs nothing but wiring.

### Result from the recovered V2.0.2 bootloader

The receiver starts at `0x08002000` and accepts 128-byte checksum-mode XMODEM
packets. It validates the block number and complement and the 8-bit additive
checksum, erases a page at page boundaries, writes 128 bytes, then ACKs. It
accepts EOT at any transfer length.

It does not check a signature, model, version, expected image length,
destination upper bound, whole-image checksum or flash readback. Before launch
it checks only that the first application word resembles an SRAM stack pointer,
then loads that stack pointer and branches through the reset-vector word.

This exact receiver successfully accepted both official V15.41 and the
generated dumper on the captured PB2A16S20P. That establishes the happy path
for one bootloader/device/image combination; it does not make malformed images
safe or establish behavior for another bootloader version.

---

## 1. Target identification (from static analysis)

| property | value | how known |
|---|---|---|
| core | ARM Cortex-M3 | `SCB @ 0xE000ED00`, bit-band alias `0x42xxxxxx` |
| family | STM32**F1** (or register-compatible clone: GD32F103 / APM32F103 / CKS) | `RCC @ 0x40021000`, `FLASH @ 0x40022000`, `GPIOA @ 0x40010800` — the F1 signature (F3/L4/G4 put GPIO at `0x48000000`) |
| flash | 128 or 256 KiB | the stock flash-size code handles exactly `0x80` and `0x100` KiB; the 67 audited applications end between `0x080128c6` and `0x0801a6b0` |
| RAM | hardware-line dependent | audited initial SP values range from `0x20000df8` to `0x20005738`; the largest exceeds the 20 KiB of a medium-density F103 |
| app self-programs flash | yes | `FLASH_KEYR` keys `0x45670123` / `0xCDEF89AB` present |
| app touches option bytes / RDP | **no** | option-byte keys `0x08192A3B` / `0x4C5D6E7F` **absent** — so if RDP is set, the bootloader set it, and we can't tell from what we have |

You do **not** need to nail the exact part number: `stm32flash` reads the chip
ID from the ROM bootloader on connect and prints the model. Let it tell you.

### Important: the F1 ROM bootloader is UART-only

There is **no USB DFU bootloader** in STM32F1 system memory. Any guide that says
the device enumerates as a USB "STM32 BOOTLOADER" is describing a different
chip family and does not apply here. On F1 you speak the **USART bootloader
protocol** (AN3155) over the same TX/RX pins, via a USB–TTL adapter.

### Important: this is NOT the JK boot trick

The `0x5AA5`-to-backup-register + reset path (JK command `0xFF`) enters **JK's
own** bootloader, which only speaks the XMODEM upgrade protocol — it will not
dump memory. To reach the **ST ROM** bootloader you must pull **BOOT0 high** in
hardware and reset. They are two different bootloaders.

---

## 2. What you need

- The BMS powered (battery or bench supply per the JK wiring rules — B-/P- etc.).
- A **USB–TTL 3.3 V** adapter (the JK RS485 cable is RS485, not TTL — you need
  a plain 3.3 V UART adapter wired to the MCU's bootloader USART pins, typically
  **USART1: PA9 = MCU TX, PA10 = MCU RX**). **Do not use 5 V logic.**
- A way to hold **BOOT0 high (to 3.3 V)** and to toggle **NRST**.
- `stm32flash` (already installed in this environment; `pacman -S stm32flash`
  or distro equivalent on the bench machine).

Wiring:

```
USB-TTL GND  ---- BMS GND
USB-TTL RX   ---- MCU PA9  (MCU TX / USART1_TX)
USB-TTL TX   ---- MCU PA10 (MCU RX / USART1_RX)
BOOT0 pin    ---- 3.3 V  (through ~10k is fine) to enter ROM bootloader
NRST         ---- momentary to GND to reset
```

Locating BOOT0, NRST and the USART pins on the specific board is the one part
this guide can't do for you — trace them from the STM32 package (BOOT0 is a
dedicated pin; PA9/PA10 are fixed for USART1). If the board exposes a debug/UART
header, those pins are often broken out there.

---

## 3. Enter the ROM bootloader

1. Power off the BMS (or hold NRST low).
2. Tie **BOOT0 to 3.3 V**.
3. Release NRST / power on. The MCU now runs the ST ROM bootloader on USART1.
4. Leave BOOT0 high for the whole session.

---

## 4. Confirm the link (read-only, safe)

```bash
stm32flash /dev/ttyUSB0
```

Expected: it prints the bootloader version and a **device model / flash size**,
e.g. `STM32F103xC/D/E` (or a clone's ID). This exchange is Get + Get-ID only —
**no memory is read, written or erased.** If you get this far, the link and
bootloader entry are correct.

If it can't sync: check TX/RX aren't swapped, GND is common, BOOT0 really is
high, baud/parity are default (the tool negotiates 8E1 automatically), and that
nothing else holds the USART pins.

---

## 5. Attempt the dump (read-only, safe)

Read the full flash to a file. `stm32flash` clamps the length to the detected
device size, so `0x40000` (256 KiB) is a safe over-request:

```bash
stm32flash -r fulldump.bin -S 0x08000000:0x40000 /dev/ttyUSB0
```

- **If it succeeds:** you have everything. Preserve that full image, then carve
  the complete pre-application region (bootloader plus persistent pages):

  ```bash
  dd if=fulldump.bin of=jk_pre_app_8k.bin bs=1 count=8192
  # == bytes 0x08000000 .. 0x08001FFF
  ```

  Sanity-check it: a valid Cortex-M vector table starts with an initial SP in
  `0x2000xxxx` and a reset handler in pre-application flash (odd, Thumb bit set):

  ```bash
  python3 - <<'PY'
  import struct
  d=open("jk_pre_app_8k.bin","rb").read()
  sp,reset=struct.unpack_from("<II",d,0)
  print("SP=0x%08x  RESET=0x%08x  (RESET should be <0x08002000, odd)"%(sp,reset))
  PY
  ```

  Then it can be loaded into Ghidra at base `0x08000000` (Cortex-M3, Thumb) to
  read the XMODEM receiver directly.

- **If it is refused** (`Failed to read memory` / read-protection error): RDP is
  enabled. **Stop here.** Do not try to "fix" it with an unprotect — see §6.

---

## 6. THE ONE THING NEVER TO DO

**Never issue a readout-unprotect or mass-erase to try to get past a refused
read.** On a protected STM32F1 those commands **erase the entire flash first**,
which destroys the bootloader you are trying to recover *and* bricks the BMS
(the application is gone too, and it can only be restored via a full image we
don't have). Concretely, on a device you care about, **never run**:

```bash
stm32flash -u  ...      # readout-UNPROTECT  -> mass erase on F1.  FORBIDDEN.
stm32flash -o  ...      # erase-only         -> FORBIDDEN.
stm32flash -w  ...      # write              -> not needed here, and risky.
```

The read commands in §4–§5 (`stm32flash` with no option, and `-r`) never erase.
Stay within those.

If §5 was refused, the realistic options are: accept that we validate the
receiver empirically instead (drive a real upgrade with `--trace` and read the
device's ACK/NAK timing off the wire), or treat RDP1 bypass as a separate,
invasive research task (SWD exception-timing / cold-boot reads — F1-specific,
needs an ST-Link and careful setup). Given we don't strictly need the
bootloader, empirical validation via `--trace` is almost always the right call.

---

## 7. Restore normal operation

When done, regardless of outcome:

1. Power off / hold NRST low.
2. Tie **BOOT0 back to GND** (its normal state — boot from flash).
3. Reset / power on. The BMS runs its application again as before.

Nothing in §3–§5 modifies flash, so a device that was only *read* (or refused)
comes back unchanged.

---

## 8. If a dump is obtained — what to do with it

- Load `jk_bootloader.bin` in Ghidra at `0x08000000`, Cortex-M3 little-endian,
  Thumb. Find the USART/RX state machine and the XMODEM handler.
- Cross-check against `jk_upgrade.py` / `re/notes/00-findings.md` §19:
  block = `SOH | n | ~n | data[128] | sum8`, ACK/NAK/CAN, EOT×3 on the last
  block. Confirm: does it send `NAK` or `'C'` to request the first block? Does
  it accept `EOT` appended to the final block's write? How long after the
  `0x5AA5` reset before it starts NAKing?
- Fold any corrections back into `jk_upgrade.py` and note them in the findings.
