# Changelog

Notable changes, newest first. The version is set in `src/jkctl/__init__.py`;
tagging `vX.Y.Z` publishes it (see `.github/workflows/release.yml`).

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
