# Changelog

Notable changes, newest first. The version is set in `src/jkctl/__init__.py`;
tagging `vX.Y.Z` publishes it (see `.github/workflows/release.yml`).

## 0.2.0 — 2026-09-27

Firmware, end to end. This release repacks a `.jkbms`, builds an exact-image
full-flash dumper for every audited image, and — on one physical
`JK_PB2A16S20P` running official V15.41 — flashed the board and dumped its
flash for the first time. Out of that dump come the stored fault records the
serial link will not give up, and the firmware gate and its page are sharper
throughout. Still requires `devicectl-core>=0.1.0`.

- **Stored fault records, out of a flash dump.** The records are not served
  over Modbus — the firmware bounds a read to frames 01-03 (`0x1000`-`0x15FF`)
  and refuses anything above, and the vendor reads them over Bluetooth instead
  — so `jkctl history` guessing at a register was never going to work. They
  *are* in a full flash dump: on the audited `JK_PB2A16S20P` V15.41 a
  three-page ring at `0x08019000`, stored little-endian.
  `jkctl history --from-flash-dump full.bin` (and the History tab's "Load a
  flash dump") now decode every record, newest first, with no hardware; a real
  128 KiB capture yields 78. The flag reads `--from-flash-dump` so it plainly
  reads a dump rather than writes one, and the History tab now says outright
  that the serial link carries no records because this firmware does not
  implement it — not a jkctl fault — that another board's firmware may serve
  them now or later, and that the vendor's mobile app reads them over
  Bluetooth. `history.read`, the over-the-wire attempt, is kept for a board
  that might differ and now says plainly why it found nothing. Fixes the
  record's pack current, which is signed (a discharge read `+6507.9 A`
  unsigned instead of `-45.7 A`).

- **The gate reads in one order everywhere.** The "Check a file" checklist, the
  library's verdict column and its tooltip now all list the steps most
  important first (model, then major version, …), so a file's reasons never
  appear in one order in one place and another elsewhere. Matches `firmware
  list`.

- **The flash notices no longer say "once".** The V15.41 → `JK_PB2A16S20P` path
  has now succeeded on repeated flashes, so the CLI, the page and the docs say
  so, while still not claiming anything for other models or versions.

- **Flashing keeps the clock on local time.** A reboot can leave the board's
  real-time clock at the firmware's power-on default, and the board keeps no
  time zone, so a flash could leave the clock a whole zone offset out (an "8
  hours ahead" on a UTC+8 host). `firmware flash` — on the command line and on
  the page — now re-syncs the clock to the host's local time once the unit
  answers again, best effort (`controls.resync_clock_after_flash`).

- **Clearer firmware-gate messages.** The compatibility-gate steps are named
  for what they check (`model`, `major version`, `minor version`, …) and each
  says plainly why it passed or failed, in place of the vendor's terse and
  ambiguous strings. The library table and `firmware list` now name *every*
  reason a file was turned down, worst first — so a file for the wrong board is
  reported as such rather than only as "not newer".

- **Firmware page tidied.** The flash card's warning is shorter and no longer
  splits down two columns; the library's directory box says it is a path on the
  computer running jkctl (the server); and the check and library cards are
  sized to their content.

- **First measured firmware transfer.** `firmware flash` successfully installed
  official V15.41 on one physical `JK_PB2A16S20P`. The recovered V2.0.2
  bootloader confirms only per-block XMODEM checks, with no signature,
  whole-image checksum, model/version, expected-length or destination-bound
  validation. This result does not establish support for other targets.

- **Full-flash recovery across the firmware archive.** `firmware make-dumper`
  now accepts all 67 audited vendor images across HW V14, V15, V17 and V19,
  using 33 exact binary layouts. The cross-version audit moved the private
  action from occupied register `0x1628` to universally free `0x162e` and
  handles the distinct B-Series/V19 response-object ABI. One V15.41 image was
  flashed successfully to a `JK_PB2A16S20P`; it remained operational and
  yielded a validated 128 KiB dump. The other 66 images remain static- and
  simulator-verified only.

- **Firmware repack.** `jkctl firmware repack` (and `firmware.build` /
  `firmware.repack`) re-encode a `.jkbms` — the inverse of `firmware.load`:
  wrap an image back into the AES-CBC + zlib container, optionally swapping in
  a patched image or editing the in-image metadata header (version, model,
  expiry). It round-trips a vendor file to the same image and metadata. Adds an
  `aes.encrypt_cbc` mirror of `decrypt_cbc`, with the same backends (still no
  package required — pure-Python by default).

- **A demonstration bank worth looking at.** `jkctl ui --simulate` now puts
  three distinct packs on the in-memory bus — one resting, one charging and
  balancing near the top, and one working hard in a warm enclosure far enough
  to raise a battery-over-temperature alarm — so the bank, dashboard and cells
  views show the real spread of states instead of three copies of one reading
  under different serial numbers. Each is a different 16S part on its own
  hardware revision and firmware (`JK_PB2A16S20P` 15A/15.41, `JK_PB1A16S15P`
  14B/14.28, `JK_B2A16S20P` 11.XW/11.34) — all 16S, so a bank that would
  really be paralleled — and the bank tab now names every tile the way the
  header and the board picker already do: the serial number no two units
  share, the Modbus address after it — smaller, lighter, italic and set off by
  a gap so it reads as a marker for where the board answers rather than a tail
  of the serial or more of the caption — and that model, hardware and firmware
  as the line
  under them. The per-cell voltages are a natural
  scatter now, not a giveaway ramp, and the README leads with that dashboard.

- **A bank tile's heading no longer moves when it has more to say.** A tile is
  a button, and a button centres its content, so a pack that was balancing —
  a third activity badge that wrapped to a second line — pushed its heading
  up out of line with the tiles beside it. Tiles are top-aligned now: an extra
  line only makes the card taller, and every heading stays on one line across
  the row. The three activity badges a balancing pack shows are packed to fit
  one line in the bargain (`devicectl-core`'s `.tile`).

## 0.1.0 — 2026-09-26

The first release. A tool for JK BMS battery management systems on an RS485
bus, reimplemented from JK's own Windows application and register-map
documents. It requires `devicectl-core>=0.1.0`.

What it does:

- **Finds and reads a bus.** Scans the sixteen addresses, reports nameplate,
  live pack data, per-cell voltages and resistances, alarms, the board's own
  fault history, and every register of every table -- one board or the whole
  bank at once, as text or `--json`.
- **Writes the configuration.** The whole settings table, typed, scaled and
  range-checked against JK's own datasource before anything goes out, with a
  write always showing what it will change; the one-key chemistry presets, the
  three main switches, the multiplexed on/off settings, the port protocols and
  the dry contacts.
- **Board actions.** The emergency start, the power-off, calibration, the
  address, the clock, and the three actions JK's document does not list.
- **Firmware.** Validates and flashes a `.jkbms` file the way JK's own
  application does, needing no crypto package (PyCryptodome, `cryptography`,
  system OpenSSL, or a bundled pure-Python AES).
- **A web interface, first.** `jkctl` with no command serves a local page and
  opens a browser on it -- the bank as tiles, live dashboards, draggable
  setpoint bands, and every command the terminal has -- built on the shared
  `devicectl-core` design system with no build step.
- **Diagnostics.** `jkctl probe` characterises a silent bus, `jkctl doctor`
  reports what looks wrong, `jkctl simulate` serves a fake BMS, and a serial
  trace can be turned on from the page and sent on.

See the [README](README.md) and [docs](docs/) for what each of those does.
