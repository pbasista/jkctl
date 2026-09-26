# Usage

One section per job. Every example assumes the unit is at the default
`/dev/ttyUSB0`, address 1; use `--port`, `--id` or `--device` otherwise.

## The page

```sh
jkctl
```

With no arguments `jkctl` serves the web interface and opens a browser on it,
which is how most of what follows can also be done. It needs no
configuration file: with no port chosen it opens on a picker. See
[web-ui.md](web-ui.md) for what it shows and why.

Everything below works whether or not you ever open it.

## Finding what is on the bus

```sh
jkctl scan
```

Sweeps addresses 0 to 15 -- every position a four-way DIP switch can be set
to -- and prints the model, versions and serial of each unit that answers.
Retries are off while sweeping, because a unit that is there answers the first
query.

Nothing found? `jkctl probe` tries 115200 and then 9600, listens passively
first, scans every address, and if something answers, measures the read cap
and each table's extent and dumps everything decoded. It writes
`probe-result.json` and `probe-result.log` and never writes a register.

## Reading a unit

```sh
jkctl info                     # the nameplate
jkctl status                   # the pack right now
jkctl status --watch           # redraw every 2 s until Ctrl+C
jkctl cells                    # per-cell voltages and wire resistances
jkctl alarms                   # every protection bit raised, named
jkctl doctor                   # read it over and report what looks wrong
jkctl registers 'temp*'        # anything matching, from any table
```

`--json` on any of them gives the same data as a document, at the device's
full resolution rather than at display precision -- `status` prints
`-12.35 A`, `--json` carries `-12.345`.

`jkctl registers` is the one to reach for when the other commands do not show
what you are after: it reads all three tables and takes a glob against
the key, the name and JK's label, and `--raw` adds each field's register and the
bytes it arrived as.

## A bank of them

A bus carries up to sixteen boards, so the read commands take more than one
address:

```sh
jkctl status --id 1,2,5        # three of them, one section each
jkctl doctor --id 1-4          # a range
jkctl info --id all            # whatever answers a sweep
jkctl status --id all --json   # one document, keyed by address
```

One unit prints exactly what it always did. A board that has gone quiet is
reported and the others are still read.

## Logging

```sh
jkctl log --interval 10 --count 360 --file day.csv
jkctl log --interval 1 --follow-output | tee -a live.csv
```

Samples the runtime table into CSV or JSON. On a terminal with no `--file` it
names the file after the unit's serial number; piped, it writes to stdout.
`--follow-output` streams each sample as it is taken instead of writing at the
end, which is what you want for an open-ended run.

## The configuration

```sh
jkctl settings                       # everything, with values
jkctl settings list                  # everything, with units, ranges, defaults
jkctl settings list 'vol*'           # only the voltage setpoints
jkctl settings get volCellOV volCellUV
```

Each setting is printed with its key (what `get` and `set` take), its name
(the one the web page uses: *Cell OVP*, *Charge OCP*) and JK's own label, so
it can be found in JK's application too. A glob matches any of the three.

To change something:

```sh
jkctl settings set volCellOV=3.65 volCellUV=2.8
jkctl settings set 'cellConWireRes[3]=0.42'   # one element of an array
```

The connection-wire resistances are thirty-two values in one field, and each
is its own register, so one can be set without retyping the other
thirty-one -- which is how JK's own application edits them.

Values are given in display units -- volts, amps, degrees -- and checked
against JK's own bounds before anything goes out. A value the vendor's
application would silently clamp is refused here instead. Settings already at
the wanted value drop out, so there is nothing to write and it says so.

Before the first write to a battery that matters:

```sh
jkctl settings set volCellUV=2.9 --dry-run
```

which prints the plan and the exact Modbus frame each change would send.

## Moving a configuration between units

```sh
jkctl settings export bank-a.json --id 1
jkctl settings import bank-a.json --id 2 --dry-run
jkctl settings import bank-a.json --id 2
```

The file carries only the writable fields, so nothing device-specific -- the
serial number, the run-time counters -- travels with it. An import shows the
differences and asks before writing; `-y` skips the question, `--dry-run`
stops before it.

## Switches

```sh
jkctl charge                   # report
jkctl charge off               # and change
jkctl discharge on
jkctl balance on

jkctl switches                 # the sixteen multiplexed settings
jkctl switches set 'Smart Sleep=on'
jkctl switches set 'Multiplexed Port Switching=on'   # RS485 rather than CAN
```

The switch word holds sixteen unrelated settings in one register, so changing
one is a read-modify-write of all of them; `jkctl` does that for you and
leaves the other fifteen alone.

## Ports and dry contacts

```sh
jkctl protocols                # what each UART and the CAN port speaks
jkctl protocols list --uart    # the 21 protocols a UART can be set to
jkctl protocols set uart2 5    # 005 - Pylontech low-voltage RS485 V3.5
jkctl dry-contact              # the two contacts and the LCD buzzer
jkctl dry-contact set 1 --source 3 --on 100 --off 200
```

A port protocol change may need a restart before the unit speaks it.

The lists JK ships are in Chinese in both its English and its Chinese
datasource, so `protocols list` prints an English name with JK's own string
beside it -- the number is the same in both, which is what lets the two
screens be compared.

`set` offers `uart1`, `uart2` and `can`. `show` reads `uart3` and `uart4`
too, but JK's register map describes a two-port board and marks neither of
them writable, and a register that document does not list as writable is not
one to guess at.

## Actions that change the battery

These replace every protection setpoint, or turn the board off, so each says
what it will do and asks:

```sh
jkctl preset lifepo4           # JK's published LiFePO4 values, as a plan
jkctl preset lto --dry-run     # ... shown, with the frames, and not written
jkctl preset li-ion --one-key  # the board's own slot instead: values unseen
jkctl shutdown
jkctl calibrate voltage 53270      # tell it the pack is really 53.270 V
jkctl calibrate current -2648      # ... and the current really -2.648 A
```

`-y` skips the question. `jkctl emergency` fires the emergency start and does
not ask, because it starts a pack rather than reconfiguring one.

A preset is JK's published defaults for the chemistry -- the table in the
appendix of the BD-series manual -- planned and written like `settings set`,
so the plan says every setting it changes before any of it goes out. JK's
table has no charge or float voltage, SOC voltages or balance start: set
those yourself after changing chemistry. `--one-key` fires the board's own
preset slot instead, which chooses its values in firmware and shows nobody.

```sh
jkctl time                     # what the unit's clock says
jkctl time sync                # set it from this host
jkctl address                  # the address it answers on, and the one it holds
jkctl address set 3            # move it -- ask the DIP switches first
```

Three more, whose action slots appear in **neither** revision of JK's
register-map document. They were recovered from the vendor application's own
buttons (see [reference.md](reference.md)), and none has been fired at real
hardware:

```sh
jkctl restart                  # restart the protection board
jkctl factory-restore          # every setting back to the factory values
jkctl erase-data               # erase what the board has stored
```

## What the board wrote down

A JK board keeps its own history of what tripped, and each record is a
snapshot of the whole pack at that moment.

```sh
jkctl log-codes                # what every record code means; no hardware
jkctl history                  # the records, if this board maps them
```

`log-codes` is JK's own table, extracted from its application: 137 events,
including the per-cell protections. `history` is a different matter -- which
register window serves the records over Modbus is not documented and may not
exist on your board, so the command reads the likely one and says plainly
when nothing answers there. `jkctl probe` sweeps both candidates read-only,
which is what would settle it.

## Firmware

```sh
jkctl firmware info fw.jkbms                    # no hardware needed
jkctl firmware check fw.jkbms                   # against the connected unit
jkctl firmware list ~/jk-firmware               # a whole directory, judged
jkctl firmware flash fw.jkbms
```

`check` shows every step of the gate rather than the first refusal, so a file
turned down on its version does not leave you wondering whether the model
matched. `list` does the same for a directory at once: JK ships firmware as a
tree of one directory per hardware version, and this says which of them this
board would take.

`check` runs JK's whole gate: the file parses, it is not an expired
time-limited build, its major version matches the unit's, its minor version is
higher, and its model string equals the unit's. `--force` waives the
minor-version and expiry checks only -- model and major version are never
waived.

`flash` runs the same gate, asks for a typed `yes`, arms the bootloader and
sends the image, showing a progress bar. **This has never been run against a
real BMS.** Read the safety notes in the README and in
`docs/reference.md` first.

## Testing without hardware

```sh
socat -d -d pty,raw,echo=0,link=/tmp/jkA pty,raw,echo=0,link=/tmp/jkB &
jkctl simulate --port /tmp/jkB &
jkctl status --port /tmp/jkA
```

The simulator reproduces the real unit's quirks -- byte addressing,
big-endian numbers, the read cap, the refusal of odd offsets -- so a command
that works against it works against the device. `--save-image` writes out
whatever a `firmware flash` sends it.

## Not retyping the connection

```sh
jkctl config init
```

writes a commented `jk.toml`. Uncomment what you need:

```toml
port = "/dev/ttyUSB0"
id = 1

[devices.shed]
id = 2

[devices.garage]
port = "/dev/ttyUSB1"
timeout = 1.5
```

Then `jkctl status --device shed`. A flag always beats the file.

## A slow host

A Raspberry Pi with a USB-RS485 adapter can lose the first query and need
longer for a reply:

```sh
jkctl status --timeout 1.0 --retries 3
```

`--trace` hex-dumps every frame if you need to see what is actually going out.
