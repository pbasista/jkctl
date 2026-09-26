# Reference

## Global options

Every command that talks to a unit accepts these.

| option | default | meaning |
|---|---|---|
| `--device NAME` | - | a device named in `jk.toml` |
| `--port PATH` | `/dev/ttyUSB0` | the serial port |
| `--baud N` | `115200` | JK ships every board at 115200 8N1 |
| `--id ID` | `1` | the DIP-switch address (SW1 = bit 0 .. SW4 = bit 3, ON = 1). The read commands also take `1,2,5`, `1-4` or `all` |
| `--timeout S` | `0.35` | reply timeout; raise it on a slow host |
| `--retries N` | `2` | retries on a lost or garbled reply, never on a Modbus exception |
| `--addr-offset H` | `0x1000` | `frameAddrOffset`, from JK's own `main.json` |
| `--config PATH` | `$XDG_CONFIG_HOME/jk/jk.toml` | the settings file |
| `--trace` | off | hex-dump every frame on the wire to stderr |

Precedence is: command line, then `[devices.<name>]`, then the file's
top-level keys, then these defaults.

## Exit codes

| code | meaning |
|---|---|
| 0 | it did what was asked |
| 1 | anything that failed: no unit, no port, a value refused, a register that does not exist |
| 2 | `firmware check` / `flash`: the image failed the compatibility gate |
| 4 | `firmware flash`: armed and sent, but the transfer reached no good end |
| 5 | a confirmation was declined, so nothing was done |
| 130 | Ctrl+C |

There is deliberately no code per failure kind; the message on stderr says
which. `error:` means the command failed, `warning:` that it carried on
regardless, `note:` is an advisory.

## `jkctl ui`

| option | default | meaning |
|---|---|---|
| `--listen HOST[:PORT]` | `127.0.0.1:8087` | where to listen; a bare port or a bare host both work |
| `--no-browser` | off | do not open or raise a tab |
| `--read-only` | off | refuse every write server-side |
| `--token TOKEN` | generated off loopback | pin the access token |
| `--no-token` | - | serve without one, on a network you trust |
| `--allow-host NAME` | - | also answer to this hostname (repeatable) |
| `--interval S` | `3` | seconds between live refreshes |
| `--idle-timeout S` | `45` | seconds before an unused port is given back |
| `--firmware-dir DIR` | - | a directory to offer in the firmware library |
| `--debug` | off | log every request |

`jkctl` with no arguments is `jkctl ui`, and options with no command are
handed to it: `jkctl --port /dev/ttyUSB1` opens the page on that port.
`-h`, `--help` and `--version` still reach the root parser, and a mistyped
command still gets argparse's "invalid choice".

With no port named -- none on the command line, none in `jk.toml` -- the page
opens on a port picker instead of failing, unless `/dev/ttyUSB0` exists and is
the only adapter on the machine, in which case there is nothing to ask.

## The register map

    register = --addr-offset + table base + payload byte offset

| base | table | access |
|---|---|---|
| `0x1000` | settings (frame/01) | read/write throughout |
| `0x1200` | runtime data (frame/02) | read only |
| `0x1400` | device info (frame/03) | read, with a documented writable subset |
| `0x1600` | action slots | write only |
| `0x1800` / `0x1A00` | *candidates* for the system log and the fault records | unproven; `probe` sweeps them read-only |

The action slots are slot numbers, not byte offsets:

| slot | action |
|---|---|
| `0x00` | voltage calibration |
| `0x04` | shutdown |
| `0x06` | current calibration |
| `0x0A` / `0x0C` / `0x0E` | Li-ion / LiFePO4 / LTO one-key preset (`jkctl preset --one-key`) |
| `0x10` | emergency start |
| `0x12` | RTC synchronisation |
| `0x16` | restart the board -- **undocumented**, recovered from the application (§36) |
| `0x18` | factory restore -- **undocumented**, same source |
| `0x1A` | erase all data -- **undocumented**, same source |
| `0x26` | firmware upgrade arming -- **undocumented**, in neither revision of JK's register map |

## Notes and limitations

Each of these is a device behaviour or a deliberate omission, with how it is
known.

- **The map is byte-addressed.** A field's payload byte offset is its register
  offset, and a read of C registers returns 2C bytes. Proven on the live unit
  by an overlap test: `FC03(0x1400,32)[32:64] == FC03(0x1420,32)[0:32]`.
  JK's own document agrees -- its "address" column is the byte offset.

- **Numbers arrive big-endian, but the datasource says little-endian.** The
  datasource describes the native 55AAEB90 UART frames, which are
  little-endian; the Modbus interface serves the same fields in standard
  Modbus word order. Decoding a Modbus read as little-endian gives a pack
  voltage of 349175 V. Both orders are supported, chosen per transport.

- **FC03 quantity is capped near 122 registers.** A 122-register read works
  and 124 answers exception 2, so a whole table can never be read at a
  stroke. A read is planned from the byte ranges its fields fall in and split
  into chunks of at most 32 registers, which is what makes a whole-table read
  five transactions rather than sixty-eight. When one is refused, the largest
  read that still fits is binary-searched, taken, and the table's extent
  remembered -- nothing past a register the device will not serve is going to
  be served either.

- **Reads must be word-aligned.** An odd register is refused. Reads align down
  and trim; writes cannot, so a write must start and end on a word boundary.

- **A field narrower than a register cannot be written alone.** `uart1ProtoNo`
  and `canProtoNo` are the two halves of one register, as are `rcvTime` and
  `rfvTime`. Writing one reads the containing word first and replaces only
  that field's bytes.

- **A table can be shorter than the frame allows.** The unit measured answers
  about 244 bytes of frame/03, not 293; reading past that gives exception 2 or
  silence. A field the board does not map is reported absent, not as an error.

- **JK's "English" datasource is partly Chinese.** One datasource serves
  en_US and zh_CN, and fifteen runtime labels, the six temperature-sensor
  bits and three Off/On pairs were never translated. `protocol_en.json` is
  shipped exactly as the application decrypts it, so the English is supplied
  by `registers._ENGLISH_LABELS` and `_ENGLISH_BITS` instead. Only what JK
  left in Chinese, or got demonstrably wrong, is replaced -- awkward English
  is the vendor's own and is kept, so a label here is still findable on JK's
  own screen.

- **Six system-alarm bits named another bit's state.** A raised MOS
  over-temperature bit read out "Over Voltage", battery over-voltage read
  "Too Large", charge over-current read "Error". The bit *names* are right in
  the datasource and only the raised-state words are shifted, so each is set
  to what its own bit means. Bit 7 is also a short *circuit*, not a short
  "current"; bit 14 spells the same thing correctly.

- **`switchStatus` exists in two tables.** In frame/01 it is a 16-bit
  configuration word (the one `jkctl switches` drives); in frame/02 a 3-bit
  read-only status byte. They are not the same register.

- **The whole 0x1000 block is writable, but only part of 0x1400 is.** The
  writable device-info fields are the protocol selectors, the dry-contact and
  buzzer triggers and thresholds, the logging period, and the RCV/RFV times.
  A field absent from JK's RW column is treated as read-only rather than
  guessed at.

- **`jkctl log` ends when a sample fails.** Ctrl+C stops the run and writes
  the file, but a read that is refused or times out -- an adapter unplugged,
  a board that stops answering part way through the night -- exits with the
  error and writes nothing, because the file is composed at the end. Two ways
  round it for an unattended run: `--follow-output`, which streams each row
  as it is taken so the samples before the failure are already on disk, or a
  shell loop that restarts the command. The browser's live view survives it
  differently -- a board that stops answering is published as a failed row
  and the sweep carries on -- but what that keeps is a bounded window of
  samples for the chart, in memory, not a file.

- **`tempSensorAbsent` is a *present* flag.** JK's two names for the word
  disagree: the machine name says a raised bit is a probe that is absent, the
  Chinese label (传感器存在标志) says it is one that is present. The label is
  right. Three things say so, and the third is the one that settles it: the
  datasource's factory default for the word is 255 -- every bit raised, which
  is a sane default for "show them all" and nonsense for "none of them is
  connected"; a unit with all six probes wired reports all six bits raised;
  and that unit's own card listed six probes and six temperatures under a
  warning that all six were missing, which is what found this. The machine
  name is kept, because that is what the register is called on the wire; what
  reads it is `Runtime.sensors_present`. Which bit is which was never in
  doubt: bit 0 is the MOS probe and bits 1-5 the five battery probes, so a
  bit is named rather than numbered from one.

  A clear bit is a probe the model does not have -- most packs wire two of
  the five -- so nothing warns about one. What `jkctl doctor` warns about is
  a probe the unit still counts as connected and publishes no temperature
  for.

### Not proven on hardware

- **No write has ever reached a real BMS.** Every write path here is derived
  from JK's register map and from the vendor application's serializer, and
  exercised only against `jkctl simulate`. Start with a register rewritten
  with the value just read from it.

- **Settings writes may be password-gated.** JK's application has a
  settings-password dialog and a `Dynamic key is invalid!` string that the
  reverse engineering never resolved; `settingPassword` sits at register
  `0x1470`. Whether the Modbus path enforces it is unknown. `jkctl` reports a
  refused write with the device's own exception code rather than guessing at
  an authentication handshake.

- **The FC16 quantity cap is unmeasured.** Writes are chunked at 16 registers,
  well below the measured read cap. `jkctl probe --probe-writes` measures the
  real ceiling, writing only bytes it has just read back.

- **The firmware transfer has never run on a BMS.** The arming write matches a
  capture of JK's own application byte for byte
  (`01 10 16 26 00 01 02 00 00 D6 97`), and the XMODEM block format was
  recovered from its sender, but the receiving side is the bootloader, which
  ships in no `.jkbms` file and could not be read.

- **The bytes sent are verified; the device's defence against bad bytes is
  not.** The image `flash` sends is the same AES-CBC + zlib container JK ships,
  and its extracted bytes are identical (SHA-256) to an image pulled
  independently from a vendor build -- confirmed on
  `73-JK-PB2A16S20P-V19.02.jkbms` against `fw_PB2A16S20P_19.02.bin`. So the
  transfer does not send the wrong data. What is unknown is whether the device
  rejects data that is wrong for another reason: a `.jkbms` carries no signature
  and no whole-image checksum (only metadata and a 12-byte build-time/expiry
  trailer -- every container byte is accounted for), JK's application validates
  only that metadata, the on-wire guard is XMODEM's 8-bit per-block checksum
  alone, and the bootloader that would verify and commit the image could not be
  read. A dump procedure to settle it on the bench is in the research notes;
  it needs hardware with readout protection disabled.

- **A garbage app should leave the bootloader intact, but reaching it again is
  the catch.** A `.jkbms` carries only the application (`0x08004000`+); the
  bootloader (`0x08000000–0x08003FFF`) is never in the image, so it has nothing
  to overwrite itself with and every reason to preserve itself -- so a wrong or
  interrupted flash is expected to leave the bootloader alive and only the app
  broken (expected, not measured -- the bootloader could not be read). The catch
  is re-entry: the upgrade is armed *by the application* (it writes `0x5AA5` to a
  backup register and resets -- both the magic and the `AIRCR` reset key are in
  the app image), so a dead app cannot re-arm the upgrade over RS485, and
  whether the bootloader waits for a new image on its own when the app's vector
  table is invalid is unknown. Recovery then falls to the STM32 ROM bootloader
  (BOOT0 high, `stm32flash`): with RDP off it can rewrite the application region
  from the extracted image and leave the JK bootloader untouched; with RDP on,
  clearing it mass-erases the JK bootloader too and recovery needs a full image
  no `.jkbms` contains. See `research/windows/docs/bootloader-dump-procedure.md`.

- **The action slots have never been fired on a BMS.** The documented ones'
  addresses come from JK's register map; what each writes is inferred from the
  slot's existence.

- **Three action slots are in no JK document at all.** `restart` (`0x16`),
  `factory-restore` (`0x18`) and `erase-data` (`0x1A`) were read out of the
  vendor application's own buttons: each of its "Send" buttons is connected to
  a handler that fires one slot. The control is that the same reading gives
  `0x04` for "Shutdown Board" and `0x12` for the clock, both of which JK's
  document does list, at those addresses. That makes the three as well
  founded as the documented ones -- and no more tested, since none of them has
  been fired at hardware either.

- **Where the stored fault records live is unknown.** The board keeps them
  (`detailLogsCount` counts them) and the datasource describes each 24-byte
  record exactly, but the vendor application's four base getters cover frames
  01, 02, 03 and the action space and stop: it reads the records over its
  other channel. `0x1800` and `0x1A00` are the candidates by the same
  +0x200-per-frame pattern. `jkctl history` reads the candidate and says
  plainly when a board does not answer there, and `jkctl probe` sweeps both
  read-only, so one bench run settles it. What each record's *code* means is
  not in doubt -- `jkctl log-codes` prints the vendor's own table for it.

- **The four one-byte temperatures are signed by inference.**
  `tmpStartHeating`, `tmpStopHeating`, `tmpBatDCHUT` and `tmpBatDCHUTPR` are
  the numbers the datasource gives no type, which its loader reads as an
  unsigned byte, and a range from -40 °C, which no unsigned byte holds. jkctl
  reads and writes them as two's complement (`-5` is `0xfb`). That is what
  JK's own C++ application puts on the wire for a negative number in a byte
  whatever it calls the byte, but no unit has yet been read back with a
  negative value set from JK's application; one read of a board set that way
  would turn the inference into a fact.

- **Firmware 15.41 does not take the discharge under-temperature pair.**
  `tmpBatDCHUT` and `tmpBatDCHUTPR` are one register at `0x1122`, which JK's
  Modbus document for the JK_PB2A16S20P does not list (its settings stop at
  `0x0119`). The unit measured reads it as zeros and answers a write with
  exception 2 -- also when the write carries the zeros it holds -- and JK's
  application sends the same frame, byte for byte (FC16, one register,
  high byte `tmpBatDCHUT`). Exception 2 is also how the firmware refuses a
  value, so `jkctl` tells the two apart by writing back what the register
  holds (`Device._refused`), and remembers a register refused either way for
  the rest of the session. Seen in `jkctl-trace-20260926-074705`.

### Out of scope

- Bluetooth. The Android application is a read-only cross-reference and has no
  BMS firmware upgrade of its own.
- The native 55AAEB90 UART channel, beyond the read-only fallback `jkctl
  probe` uses to recognise a port configured for another protocol -- and,
  should the stored records turn out to live only there, the read path for
  frames 05 and 06 and nothing else.
- Any account, cloud service or telemetry. The vendor's application has
  licence activation keyed to your CPU id; this one has nothing of the kind,
  and the web interface it serves is bound to this machine unless you tell it
  otherwise.
