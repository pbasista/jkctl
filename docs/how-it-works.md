# How it works

Nothing here came from JK. It was recovered from their Windows application,
JK BMS Monitor 3.11.0, and from two register-map documents they publish. The
full record, round by round including one wrong turn and its reversal, is
`research/windows/windows-findings.md`. This is the readable summary.

## The datasource is the protocol spec

The application ships `config/en_US.jsonds` and `config/zh_CN.jsonds`. Both
are AES-256-CBC with a zero IV over `uint32_le raw_len || zlib(json)`, keyed
with a 32-byte ASCII string found in `protocore.dll`'s `.rdata`. The JSON
inside is the datasource the application's own search engine walks -- so it is
not a description of the field layout, it *is* the field layout the vendor's
UI uses.

It describes six tables inside a 300-byte frame:

    off 0    header     55 AA EB 90
    off 4    frame code 01..06, which table follows
    off 5    counter
    off 6    payload    293 bytes, laid out per table
    off 299  checksum   sum of bytes 0..298

| table | contents | fields |
|---|---|---|
| 01 | settings, device to host | 46 |
| 02 | runtime and cell data | 74 |
| 03 | device info | 47 |
| 04 | settings, host to device | 45 |
| 05 | system log | 4 |
| 06 | fault records | nested |

Every one of them measures out to exactly offset 299, which is what validates
the size model. Each field carries a type, a scale, a unit, a display
precision, and -- for 93 of them -- a minimum, a maximum and a factory
default. `jkctl` reads all of that rather than transcribing any of it:
`jkctl.protocol` parses the JSON, `jkctl.registers` turns it into addressable
registers, and `jkctl.values` validates against the vendor's own bounds. The
alarm bit names in `jkctl alarms` come from the same place.

The two decrypted files ship as `src/jkctl/protocol_{en,zh}.json`.

## The transport is Modbus RTU

115200 8N1, slave address from the DIP switch, function codes 0x03 and 0x10
only. The serializer in the application (`FUN_140015560`) builds

    [slave] [func] [reg hi] [reg lo] [count hi] [count lo]
    (0x10:) [byte count] [data ...]
    [crc lo] [crc hi]

with a standard CRC-16/MODBUS. The register bases are `frameAddrOffset`
(0x1000 by default, from the application's `main.json`) plus 0x000, 0x200,
0x400 for the three readable tables and 0x600 for the action slots.

Inside a table the register address is the field's *byte* offset in the
payload. That was the hardest thing to settle -- see `docs/reference.md`,
which lists it along with the quantity cap, the alignment rule and the
byte-order split, each with the evidence.

## The board will not be spoken to straight away

A request sent a couple of milliseconds behind the board's previous reply is
not refused -- it is not heard. No exception code, no reply, nothing on the
wire at all; the same request sent after a pause is answered first time.
This was measured on a live JK_PB2A16S20P from a recorded trace: nine
requests in a row sent 2 ms after a reply went unanswered, and every request
sent after a pause was answered.

Modbus RTU's own rule -- 3.5 character times, a third of a millisecond at
115200 -- is nowhere near enough for this board, and the vendor's own
application never found out, because it polls on a timer rather than sending
back to back.

So every transaction waits out a turnaround delay first. How long it needs
is not something the trace can say -- it says only that 2 ms is too little
and that 380 ms is plenty -- so the bus finds out for itself: a board that
has been answering and then says nothing was spoken to too soon, and the
delay doubles, up to a ceiling past which the query timeout would expire
first. An address nobody is at never widens it, which is what keeps a scan
of sixteen addresses from slowing the whole bus to its ceiling.

The web UI carries what it learned across the port being released and
reopened, so it is learned once per session rather than once per minute.

## The .jkbms container

    file          = AES-256-CBC(key, iv=0) over payload_blob
    payload_blob  = uint32_le raw_len || zlib deflate stream
    payload       = zlib.decompress(...)          len must equal raw_len
    payload[-12:] = int64_le build_ms || int32_le valid_hours
    image         = payload[:-12]                 the bytes actually flashed

The key is a 32-byte ASCII string in the application's `.rdata`, handed to its
`J::Aes` constructor. Verified against all 63 firmware files on hand, with no
failures. The image is a raw ARM Cortex-M vector-table image linked at
0x08004000; the bootloader below it is in no firmware file.

A fixed metadata header sits at payload offset 0x200: six 16-byte NUL-padded
ASCII fields -- software version, build date, build time, model -- followed by
a `uint32_le` device code at 0x260 that matches the numeric prefix of the
vendor's own filenames (0xFFFFFFFF on the V14 and V15 lines).

`jkctl firmware check` reimplements the application's whole eleven-step
validation chain, including the time-limited-build window that seven of the
supplied files have long since left.

## The upgrade

Writing 0x0000 to one register, `frameAddrOffset + 0x626`, reboots the BMS
into its bootloader. That register appears in neither revision of JK's
register-map document; the whole exchange was recovered from the application,
and the arming frame was later corroborated byte for byte against a packet
capture someone else published:

    01 10 16 26 00 01 02 00 00 D6 97

After that the link is raw XMODEM-128 with the classic additive checksum, not
CRC: `SOH | block | ~block | data[128] | checksum`, blocks numbered from 1,
the last padded with 0xFF, `EOT EOT EOT` appended to the final write. The
sender scans received bytes *backwards* for the first ACK, NAK or CAN.

The receiving half is the bootloader, so none of this could be checked against
the device side. `jkctl simulate` implements the receiver the sender expects,
which is how the transfer is tested.

## Reading a table without asking sixty-eight times

A field's payload offset is its register offset, so a read is planned from
the *bytes* rather than from the fields: the wanted registers are merged into
byte ranges (joining any two closer together than a Modbus round trip is
worth), each range is split into chunks under the device's ~122-register
quantity cap, and every field wholly inside what came back is decoded out of
one buffer. A whole table is five transactions instead of one per field.

Boards map tables shorter than the 293 bytes the frame allows, and a read
past the end is refused with an exception rather than answered short. So a
refusal is treated as the end of the range -- the largest read that still
fits is binary-searched, taken, and the extent remembered, because nothing
past a register the device will not serve is going to be served either. A
*timeout* is never treated that way: a board that says nothing at all is a
link fault, not a short table, and telling the two apart is what stops a
disconnected board looking like a board with no registers.

## What the board wrote down

`detailLogsCount` in the runtime table counts records the board keeps, and
the datasource's table 06 describes each one: a 24-byte snapshot of the whole
pack at the moment something tripped. Three bytes of header, twelve records,
two reserved -- 293 exactly, which is the payload every table measures out
to, so the layout checks itself.

What each record's code *means* was recovered whole: the vendor application
builds a map from code to display string, one `tr()` per entry, ending in two
32-iteration loops that name the per-cell protections. 137 events, shipped as
`logcodes.json`. Where the records live over Modbus was not: the
application's four base getters cover frames 01, 02, 03 and the action space
and stop, and it reads the records over its other channel. `jkctl history`
reads the candidate window the +0x200-per-frame pattern points at and says
plainly when a board does not answer there; `jkctl probe` sweeps it
read-only, so one bench run settles it.

## AES with nothing to install

The one cryptographic operation is decryption, once per file, over at most
20 MiB. `jkctl.aes` tries PyCryptodome, then `cryptography`, then the system
OpenSSL `libcrypto` through stdlib `ctypes`, then a bundled pure-Python AES.
The third is the one that matters on a small ARM host, where `cryptography`
wants Rust and PyCryptodome wants a C toolchain: OpenSSL is already there, and
`ctypes` reaches it with nothing to build. All four agree on the FIPS-197
known-answer vector, which the test suite checks for whichever are present.

## The MCU

Static analysis of a decrypted image puts it on a Cortex-M3, STM32F1 family or
a register-compatible clone: RCC at 0x40021000, FLASH at 0x40022000, GPIOA at
0x40010800. The application self-programs flash but never touches the option
bytes. Its ROM bootloader is UART-only, so the widely repeated "it enumerates
as a USB STM32 bootloader" story does not apply to this chip -- and JK's own
0x5AA5 path enters JK's bootloader, which is a different thing again.
`research/windows/docs/bootloader-dump-procedure.md` has the bench procedure,
and the one mass-erase trap to avoid, if anyone wants to read it out.
