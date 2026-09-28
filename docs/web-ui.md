# The web interface

`jkctl` with no arguments serves a page on this machine and opens a browser
on it -- the web interface is this program's default mode of operation, and
`jkctl ui` spelled out is the same command. This document is what it shows
and why it is shaped the way it is; see the [README](../README.md) for the
rest of the tool.

```console
$ jkctl
jkctl ui is serving on http://127.0.0.1:8087/
  press Ctrl+C to stop
```

A browser opens on the bank: every board the bus answered for, one tile
each, with its state of charge, what it is drawing, how far its cells have
drifted apart and whether anything is raising a protection. Clicking one
opens its dashboard — the pack, the cells drawn as bars and the nameplate
with its clock across the top; the temperatures, the history this page has
watched and the three main switches under them; and last the protections and
the same health check `jkctl doctor` prints, which are the two cards worth
reading when something is wrong and which say so on their own badges.
The pack, the cells and the probes are each drawn against the setpoints that
would stop them, on a scale whose dots can be dragged.

Seven more tabs mirror the CLI. **Settings** holds the configuration table in
JK's own panels, edited a card at a time. **Cells** holds the connection-wire
resistances beside the ones the board measures, and the balancer's settings.
**Ports** holds the UART and CAN protocol selectors, the dry contacts and the
buzzer. **History** holds the board's stored fault records, and what every
record code means. **Registers** is every register of every table,
searchable, with what each one currently reads. **Firmware** holds the
compatibility gate and the flash. **Tools** holds the presets, the emergency
start, calibration, export and import, the address, the serial trace, and the
three board actions JK's own document does not list.

Which tab you are on and which board you are looking at are both in the
address bar, so a reload comes back where you were and `#2/cells` is a link
somebody can send.

## The header

Left to right: the wordmark -- a half-charged battery, which is also what the
browser puts in the tab -- linking to the project, and the version beside it,
which links to the release notes for exactly this build; then the board this
page is about, named by its serial number with its model and the two versions
under it.

**That name is the way to change board.** Click it and the list of boards the
bus answered for opens -- a dialog, over the middle of the page. Nothing is
drawn beside the name to say so: a chevron promises a list dropping out of the
control it is on, and a trailing ellipsis is the one mark this name cannot
wear, because a long name already ends in one when it runs out of room. Under
the pointer, and under the keyboard's focus, the name takes the border and
fill of every other button on the page. Each row
in that list is named the same two lines this one is — the serial number over
the model and the two versions, with the bus address and whether the board
answered off to the side — because a bank built out of matching units is
otherwise four identical rows reading "BMS 1" through "BMS 4". There used to be a
*change* button next to the name, which is a second control for an action
about the thing right beside it -- and the name is what you read to realise
you are looking at the wrong battery, so the name is where you go to click.
Before that it was a dropdown, which could name a board in twenty characters
and could not say which of them was in trouble.

Against the right edge: what the serial port is doing, the switch that pauses
and resumes the live refresh, a bell that asks this browser for a
notification when a second `jkctl ui` wants this tab, and the theme. The port
pill opens a menu holding everything else about the link; a click anywhere
else on the page, or Escape, puts it away.

While anything on the page has been changed and not sent, the header says
so, just left of the pill: how many changes are waiting — on the tab being
looked at or any other — **Discard**, which drops all of them, and
**Apply**, which shows one plan for all of them and then writes it. It is
not there at all until something has been changed. It sits in the header's
slack, so nothing else in the header moves and the header does not get
taller when it appears; on a phone it lies over the end of the board's name,
which gives way with an ellipsis.

The port pill's menu stays open while the list of watching browsers is up,
and after it closes: it goes away on the next click outside it, not on the
way back from a dialog opened from inside it.

Notifications need a secure context -- this machine, or https. Reached over
plain http from another computer, which is how a program with a serial cable
in it usually is reached, the browser will not take the question at all; the
bell is still there, struck through, and says as much when pressed.

All of it is `devicectl`'s, and alfenctl -- the same author's tool for Alfen
charging stations -- draws the identical header from the identical code. The
two pages are meant to differ in their content and in one hue, and in nothing
else: same type, same spacing, same controls in the same places. If you use
both, neither should have to be learned twice.

## The bank

Neither of JK's applications has this view, and for anyone with more than one
battery it is the reason to open the page at all. An RS485 bus carries up to
sixteen boards — a four-position DIP switch decides which — and a bank of
four is an ordinary installation. The vendor's answer is a device selector
and one window at a time.

A tile leads with the state of charge, because that is the number people come
for. Under it are the pack's voltage, current and power; under those the
highest and lowest cell with the spread between them, and the hottest sensor.
The tile's left edge is its verdict: green inside its limits, amber for a
cell spread wide enough to be worth a look, rose for a raised protection,
grey for an address that did not answer. The tile that needs attention is the
tile that looks different, which is the whole job of the view.

A board that has gone quiet gets a tile saying so rather than disappearing —
"this address did not answer" and "there is no board here" are different
things, and only one of them is worth walking out to the shed about.

## Reading a pack

**The cells are a chart, not a table.** Sixteen numbers to three decimals do
not answer "is one of them drifting"; sixteen bars do, because lengths
compare at a glance. The scale is the span of the cells rather than zero to
four volts — on a healthy pack every bar would otherwise be the same height,
and a chart on which nothing can be seen is worse than no chart — and the
scale is printed underneath, because a bar chart with a floating baseline
turns three millivolts into a cliff unless it says so. The highest and lowest
cells are called out in their own colours. Hovering a bar gives that cell's number,
its voltage and the resistance of its balance wire, in a label above the
chart — the same label the charger's energy chart uses, and for the same
reason: the browser's own tooltip takes a second to appear and arrives in
the system's font, which is sixteen pauses to find the odd cell out.

**While the balancer runs, the card says so.** Its title carries a
*balancing 5 → 12* badge, whose tooltip adds the balance current, and the two
cells are striped and marked: ▼ over the cell charge is being taken from, ▲
over the one it is given to, with the same words in their hover labels. A JK
board does not say what the balancer does to each cell. It says whether
balancing is on (`equStatus`), the current, and which cells are the highest
and the lowest, and its balancer is an active one that moves charge from the
highest to the lowest, so those two are what is marked. Marking every cell,
or guessing at more, would be a picture saying more than the board did. The
simulated bank's second board is near the top of a charge with its balancer
running, so `--simulate` shows one of each.

**Every setpoint is drawn where the reading is.** 42 °C is fine under a 60 °C
over-temperature protection and a fault under a 40 °C one; 3.48 V a cell is
the middle of the curve on one pack and an over-voltage trip on the next. A
number in a table on another tab cannot say which, so each of these cards
carries a band: the setpoints as dots on a scale, the reading on the same
scale, and the stretch where nothing trips filled in.

* The **Pack** card draws the pack's voltage against the cell protections
  multiplied by the number of cells in series. The board protects cells, not
  packs — there is no pack over-voltage register — so those two ends are
  worked out rather than read, and they are the one pair on the page that
  cannot be dragged.
* The **Cells** card draws every cell as the stretch between the lowest and
  the highest, against the two protections and the voltage balancing starts
  at. On a balanced pack that stretch is a few pixels wide, which is the
  answer.
* The **Temperatures** card draws two: the battery probes, as a stretch,
  against the four temperature protections; and the MOSFETs, which run hotter
  than the cells by design and would be drawn as a fault against the pack's
  limits, on a scale of their own.

Every number on a band is written above it: the reading -- one value, or the
span from the lowest cell to the highest -- and each setpoint where it sits,
with the mark it has on the track beside it. Where several of them hold the
same value, the number is written once in a box carrying a mark for each of
the things at it, because two dots drawn on top of each other under one number
read as one setpoint. A board out of the box has two of these: a release
sitting exactly on the protection it releases, and the two under-temperature
protections both at 0 °C. Hovering names all of them, and the pointer takes
hold of whichever is drawn on top; the one underneath is a Tab away, and the
arrow keys are what pull the two apart.

The dots can be dragged, and the Settings tab has the same bands with the
release points on them as well — the value the board lets the pack work again
at, drawn as a small hollow ring beside the protection it releases. See
[Changing something](#changing-something).

Which probes there are is the board's own answer: a bit per probe, the MOS
one and five battery ones. It picks the rows, and it picks which probes the
stretch is taken from — a board with two probes wired publishes a temperature
for all five, and the three that are not there read zero. A probe the board
still counts and has no temperature for is called out on the same card,
because a pack protected by a probe that fell off has no over-temperature
protection on that end of it.

That word was read upside down until 2026-09-16, and a pack with all six
probes wired and all six reading was told all six were missing. JK ships two
names for it that disagree — the machine name says a raised bit is a probe
that is *absent*, the Chinese label says it is one that is *present* — and
this believed the machine name. See [reference.md](reference.md).

**The history is the page's own memory.** The registers say what is happening
now and what has happened in total, and nothing in between. It exists for one
question a single live number cannot answer: you changed something, did
anything happen? Which is also why the writes are on it, as dashed rules —
the change and its effect end up next to each other.

The line is drawn from two places at once. Every reading the live refresh
brings is kept in the tab, per board; and the server has been keeping a
sample per board on every beat since it started, which the page asks for when
a board is opened. So a reload, or a second browser arriving an hour in, gets
the line as it already was rather than an empty box that fills a pixel every
three seconds. Power is drawn positive going into the pack and negative
coming out, and the rule across the middle is zero — the board reports the
power as a magnitude (`batWatt` is unsigned) and puts the direction in
`batCurrent`, so the two are put back together for the chart. The watts
above the picture are that line's right-hand end, and they are drawn in the
light weight `alfenctl` gives its live meter rather than the heavy one a
state of charge gets: a number to be read against the chart under it, not a
headline.

A second line rides on the same picture: the hottest probe, in its own
colour and on its own scale, named by the key under the plot. On its own it
would be a card holding a line that moves a degree an hour; next to the
power it answers the question it is actually there for, which is what an
hour of charging is costing the pack. They cannot share an axis — a watt and
a degree have no common scale — so each key prints the range its line is
drawn against.

Under the chart are the numbers the line cannot give, over exactly the window
drawn above them:

| Figure | What it is |
|---|---|
| **charged** | energy that went into the pack |
| **discharged** | energy that came out of it |
| **power range** | the lowest and the highest power seen, signed as the chart draws it — `-850 W to +1200 W`; each end says on hover which way it was, "850 W discharging" |
| **SOC change** | state of charge at the end of the window minus at its start |
| **voltage change** | the same, for the pack voltage |
| **temperature change** | the same, for the hottest probe — the chart's second line |

The energy is integrated between readings and a gap of more than 30 seconds
counts for nothing, so a laptop that was asleep does not invent
kilowatt-hours. The live watts above the chart have the same space under
them as the kW on alfenctl's live meter.

The chart itself is the shared one: the same drawing, from the same file, as
the charger's live meter in alfenctl. It used to be its own forty lines of
SVG, stroked in two colour tokens that had not existed since the palettes
were named for what a colour means — which leaves `stroke` at its initial
value, black, and drew a black line on a near-black card. Nothing caught it:
those are attributes on an SVG element, which no stylesheet check reads.

**Health is not run on load.** A full pass reads the nameplate, the runtime
table and the whole configuration, which is three reads of a wire other
things want. One click on **Run check**, in the card's title, answers "is
this pack all right" with the same findings the terminal prints; after that
it reads **Check again**. Its badge says *nothing to report* when the pass
found nothing — a check can only speak for what it looked at — or counts the
findings by weight. alfenctl's Health card is the same card, from the same
code, with the same button in the same place and the same words.

**The board's clock is local time.** The board keeps a count of seconds and
no time zone, and JK's own application counts it from local midnight on
1 January 2020 — so the Identity card calls it **Local time** and writes it
the way every time on this page is written, `2026-09-26 08:03:46`. Under it,
**Clock difference** says how far it is from the clock of the computer
running `jkctl`, in words with a direction: `12 seconds ahead`, `2.5 years
behind`, `in sync`. It does not say "this computer", because the page is
often read on another one. **Sync clock**, the card's only action, is in its
title. The Station card in alfenctl draws the same two rows and the same
button.

## Changing something

Every tab that writes follows the same rule, and it is the CLI's rule: a
card's fields edit together, **Apply** shows the plan, **Discard** drops it.
The plan is what `jkctl settings set --dry-run` prints —

```
volCellUV: 2.800 V -> 2.900 V
balanEn: On -> Off
```

— shown before anything goes out, with values already at the wanted setting
dropped from it. Ranges come from JK's own datasource, so a value its
application would silently clamp is refused here instead, naming the bound.

**An unsent edit is marked where it is, on its card, and in the header.**
The changed field takes the colour every unsent edit on the page wears, its
name beside it goes the same colour, and the card around it gets an edge in
that colour and a count in its title — *2 not sent* — so a change made four
screens down a long page is findable from anywhere on it. The header counts
every edit on the page. It is the same in alfenctl.

**Edits outlive the tab they were made on.** They are kept for the board,
not for the card: switch to another tab and back and they are still there,
and the header still counts them while you are away. A setting shown on two
cards is one edit shown on both — a protection dragged on the dashboard's
band is pending in the Settings tab's table, and in the register browser.
Choosing another board drops them, since an edit made for one battery is not
one for the next.

**Apply one card, or everything.** Beside a card's count are two small
buttons, a tick that applies that card's edits and a cross that discards
them, each named in its tooltip; the header's Apply and Discard do the same
for every edit on the page. Either way the plan comes first, one dialog for
all of it, with a heading per kind of write when there is more than one. They
used to be a bar at the foot of the card that appeared with the first edit,
which made the card taller and pushed every card below it down the page.
Nothing hung on a card's title — the count, those two buttons, a badge, Sync
clock, Run check — makes the title taller, and while a card holds edits a
title too long for its line is cut short rather than wrapped, so a page
does not move when something on it is changed.

The settings are grouped into JK's own panels, so somebody arriving from JK
BMS Monitor finds a setpoint where they left it. Each card carries one line
about itself with the paragraph folded behind it.

**A setpoint can be dragged instead of typed.** The bands described above are
on the settings cards too, and every dot on one is a control: drag it, or
focus it and use the arrow keys. What that edits is the card's draft — the
same draft the numbers in the rows edit, so a dragged dot moves the number
below it and both turn the colour of an unsent edit — and Apply still shows
the plan before anything goes out.

**Dragging a dot is also how the keyboard is aimed at it.** A pointer cannot
do better than a pixel, and a pixel of a band spanning a volt is worth about
two millivolts, so the drag is for "about here" and the arrow keys are for
the rest: the dot keeps the keyboard when you let go, and the label above it
stays up saying what one press is worth — `arrow keys: ±0.001 V` on the cell
voltages, a tenth of a degree on most of the temperatures. Each setpoint
moves by the smallest amount that register can hold, which is not always the
same for every dot on one band.
Page Up and Page Down move ten of those at a time, Home and End go to the
ends. The dot is ringed while it has the keyboard, and lets go when you
click or tab elsewhere.

The division is worth saying plainly. The picture is for deciding roughly
where a setpoint belongs, which is a question about where the cells or the
probes actually sit, and that is the thing a table of numbers cannot show;
the number in the row is still there and is still the quickest way to set one
from memory. The scale a dot is dragged on is the span of what the board is
holding plus a little air either side; past it, type the number.

**Every card that changes something is a draft.** Charging, discharging and
balancing are single settings registers the board acts on the moment they
land, and two of them are the pack's contactors: turning discharging off cuts
the pack off from whatever it is running. They used to be written on the
click, which made the most consequential click on the page the only one with
no plan and no way back; now a flipped switch is an unsent edit, and its
Apply shows the plan and asks first, as everywhere else. The sixteen
multiplexed switches on the Settings tab and the port protocols on the Ports
tab are the same now: they were written on the click and with a Set button
per row respectively, and are drafts sent the way every other edit is (the
switches one bit at a time, each against a fresh read of the word). Nothing
on this page writes a setting without Apply. A card that did would carry an
*applied immediately* badge in its title, as a few of alfenctl's do.

**Every setting has one name, everywhere.** JK's labels say "Vol. Cell RCV"
and "Continued Charge Curr.", which spend their words on what the unit
already says and none on what the setting is. The page names each setting
once (`src/jkctl/names.py`) — *Cell RCV*, *Charge OCP*, in the protection's
own acronym where it has one — and uses that name on every card, in every
band and in every plan. Hovering the name spells the acronym out, says what
the setting does, and gives JK's label and the register's key, so the same
setting can still be found in JK's application and typed to `jkctl
settings`. Hovering the value gives what it may be set to and its factory
default. The command line prints the same names, with the key and JK's label
beside them.

The Settings tab no longer opens with a line counting the registers that
answered: it says something only when some did not.

Three things about writes are worth knowing and are said on the cards that
need them:

* A field narrower than a Modbus register cannot be written on its own — the
  protocol selectors are two halves of one register, as are the absorption
  and float times — so those writes are a read-modify-write and are not
  atomic. Nothing else may be writing the same register at the same time.
* The connection-wire resistances are thirty-two values in one field, and
  each is its own register, so one can be changed without retyping the other
  thirty-one. The Cells tab edits them in a grid beside the resistances the
  board measures, which is the comparison that makes either number mean
  anything.
* **No write has ever reached a real BMS.** Every write path in this program
  is derived from JK's register map and from the vendor application's
  serializer, and exercised only against `jkctl simulate`. That is in
  [reference.md](reference.md) in full, and it applies to the browser exactly
  as it applies to the terminal.

## The registers tab

JK's application shows fixed panels. Between them, the other tabs here leave
two thirds of what a board actually reports undisplayed: the PWM duty cycles,
the relay and pre-discharge states, the six protection-release countdowns,
the sensor-presence bits, the MCU id. This tab shows all of it — 157
registers across the three tables — searchable by name or description, with
each one's unit, range, factory default and access.

A register JK's map marks writable can be edited here, with the same plan and
confirmation as anywhere else. The table is one card, titled *Registers*
like every other card and always drawn with its title, so the count, tick and
cross that appear with an edit take no room of their own and the table does
not move. Its edits are the same edits as the other tabs': a setting changed
on the Settings tab is pending in its row here too. One it does not is shown and not offered.
Guessing that a register is settable because its name sounds settable is how
a battery gets reconfigured by accident, so a field absent from JK's RW
column is read-only here rather than attempted.

**A setting the board will not take says so.** JK's firmware answers
"illegal data address" both for a register it does not accept writes to and
for a value it will not take, and those need different next steps. So when
a write is refused that way, `jkctl` writes back what the register already
holds — a write that changes nothing — and says which it was. A board that
refuses even that does not take the setting at all: the notice says so, the
field shows its value with *board refuses writes* beside it in place of an
editor, and a second attempt is turned down before anything goes on the
wire. Firmware 15.41 does this for Discharge UTP and Discharge UTPR, the two
halves of one register at 0x1122 that JK's Modbus document for the model
does not list. After any failed write the page reads the settings back,
because the changes in a plan before the refusal did go in.

**The chemistry presets are a plan like any other.** The Tools tab's
LiFePO₄, Li-ion and LTO buttons plan JK's published defaults for that
chemistry — the table in the appendix of JK's BD-series manual, nineteen
cell voltages, protection delays and temperatures — and show them in the
same dialog as any settings edit, every change as *was → will be*, sent by
**Apply**. They used to fire the board's one-key slots, whose values
the firmware chooses and nobody sees first, and to ask for the word *preset*
typed out; the slots are still there as `jkctl preset --one-key`. JK's
table leaves the charge and float voltages, the SOC voltages and where
balancing starts alone, so after changing chemistry set those on the
Settings tab before charging; and it stops charging at −20 °C for every
chemistry, which is JK's value, shown in the plan, and colder than a
LiFePO₄ maker allows.

## Firmware

The vendor's dialog opens one file and answers with one sentence: *"Minor
version must be larger than connected device!"* — which says nothing about
whether the model matched, and is why nobody can tell a file for the wrong
board from a file that is merely not newer.

Every step of the gate is shown here, passed and failed, with what each one
compared. **Force** waives the two steps JK's own "Force Updating" waives, the
expiry window and the minor version, and the checklist redraws to show what
that changes; the model and the major version are never waived, here or
there. The library reads a whole directory of `.jkbms` files and says which
of them this board would take, rather than making you open them one at a time
to find out.

The flash itself asks with the word typed out. While it runs it owns the
serial port outright — after the arming write the link is a raw XMODEM
stream, so the live refresh stops and every other request queues behind it,
and the link pill says *flashing* for the duration. This path has succeeded on
repeated flashes of official V15.41 to a `JK_PB2A16S20P`. Other model, hardware
and firmware combinations remain unverified, and the captured bootloader has no
end-to-end image or length check.

## When something will not work

A board that refuses a write answers with whatever its protocol layer
raised — "no/short response", "bad CRC", "modbus exception 2" — and that is
enough to know it failed and never enough to know why. The answer is in the
bytes, in what was asked immediately before them, and in the silences
between them, and none of that is kept: a bus that is working produces a
few hundred frames a minute that nobody wants.

So the **Serial trace** card on the Tools tab keeps them on request. Turn it
on, make the thing go wrong again, turn it off, and download the report —
plain text, with every frame decoded into words:

```
20:02:59.338  UI  POST /api/settings
              {
                "id": 9,
                "changes": { "tmpMosOT": "80" }
              }
20:02:59.340  TX  (+2 ms)  writing 1 setting(s)
              09 03 10 64 00 02 80 5c
              read · slave 9 · 2 register(s) at 0x1064 · settings byte 0x64
              · covers tmpMosOT · CRC ok
20:02:59.361  RX  (+20 ms)  writing 1 setting(s)
              (nothing)
20:02:59.361  --  writing 1 setting(s) failed: address 9: no/short response
20:02:59.361  UI  (+0 ms)  POST /api/settings  ->  400  no/short response
```

Both directions, including the ones that came back empty — a timeout is a
silence, and a `TX` with nothing under it looks exactly like the end of the
recording rather than like the failure it is. Each frame carries what the
program was doing when it went out, and, because a JK register is a byte
offset into a named table, which settings it touched.

The `UI` lines are what this page asked for: every request, once as it
arrives with the values you typed under it, and once with the status it was
answered with, so the frames in between are the ones that request put on the
bus. A report full of perfectly decoded Modbus still leaves the first
question anybody asks — what were you doing at the time — and those two
lines are the answer to it.

The report's header names the build, the port, the speed, the timeout, the
retry count, the gap this bus is leaving between frames and every board on
the bus, so it can be read a week later by somebody who has never seen the
program.

The recording belongs to the port rather than to a browser, so it survives
the port being handed back and picked up again, and two tabs share one
recording. It holds 20000 frames and then the oldest fall off the front,
which the card says. Nothing is recorded while it is off, nothing leaves the
machine unless you send the file, and a server started `--read-only` can
still be asked to record: recording puts nothing on the bus.

**It usually starts itself.** With *Record on failure* on — it is, until you
turn it off — the first time the board or the bus fails a request (no
answer, a refusal, a bad checksum, or a bug in `jkctl` met while talking to
the board) the recording starts and the notice that reports the failure says
so: try it again, and the trace will have every frame of it. A failure while
a recording is already running says the trace has it. Either notice carries
three buttons — **Download trace**, **Stop recording**, and **Stop recording
automatically** — and stays until it is dismissed; so does every other
failure notice on the page. A value out of range or a port nobody has chosen
starts nothing: those are not in the bytes, and neither does a refusal
`jkctl` has already diagnosed in words. The switch is on this card and is
kept per browser. Turning it off stops a recording it started, and keeps
what that recording holds; the card says *recording, since a failure* for
one of those, and a recording you started yourself runs on until you stop
it.

The same thing on the command line is `--trace`, which hex-dumps every frame
to stderr as it goes.

## The port, and who is holding it

Only one program can hold a serial port, so the pill in the header says what
this one is doing with it:

| | |
| --- | --- |
| **port free** | nothing is held; a terminal running `jkctl` can use it |
| **opening** | opening the port and letting the adapter settle |
| **port held** | held, nothing in flight, counting down to release |
| **busy** | with the operation named, and what is queued behind it |
| **flashing** | a firmware transfer owns the port; nothing else may go near it |
| **error** | the last attempt failed, with the reason |

Clicking it opens everything there is to say or do about the port: the live
updates switch and its beat, **Hold** and **Release**, what the port has been
doing lately, and how many browsers are watching it — which opens a list of
them, because "somebody is holding the port" and "who" are one question. An
unused port is given back by itself after 45 seconds (`--idle-timeout S`),
because a held `/dev/ttyUSB0` is a locked door and
leaving a browser tab open should not lock anybody out of the shed.

Live updates are off until you turn them on, and are shared rather than
per-browser: there is one port, so there is one answer to "is it being
polled". The pill is the only control that reacts to a refresh, and a click
during one is queued behind it rather than refused — work that lasts less
than 0.4 s disables nothing, because a page that greys out every button on
the beat reads as a fault rather than as a refresh.

A beat reads every board once and sends that reading to every open browser,
so the bank, the dashboard and the cells tab all move with it: the pack
figures, every cell voltage and resistance, which cells the balancer is on,
the temperatures, and the alarms. What a beat does not re-read is everything
that only changes because something wrote it — the setpoints, the switches,
the protocol selectors, the identity, and the several hundred rows of the
register browser. Those are read when their tab is opened, again after a
write, and on **Reload**; polling them on the beat would spend the bus on
answers that cannot have changed.

If the server itself goes away, the page says so: the readings on it are the
last ones that arrived, and a banner says the program drawing them has
stopped. The stream reconnects on its own within a few seconds, so a restart
comes back without the page saying anything.

## The first run

`jkctl` needs no configuration file to exist. With no port chosen — none
given, none in `jk.toml`, and either no `/dev/ttyUSB0` on this machine or
more than one adapter on it — the page opens on a picker listing the serial
ports this machine actually has, with whatever the system says each one is
and a badge on the ones that look like USB adapters. Choosing one scans all
sixteen addresses and shows what answered. Nothing is written.

The picker is skipped only when there is nothing to choose: `/dev/ttyUSB0`
exists and it is the one adapter on the machine. Three USB adapters plugged
into a Raspberry Pi are three ports the battery could be on, so it asks
rather than taking the first. `--port` or a `port` in `jk.toml` settles it
either way.

Picking the wrong one of those three is the ordinary way this goes, so the
picker stays where it is until a port answers. When nothing does, it says so
in place — which port, and that all sixteen addresses were tried — and the
list is still under it, ready for the next guess. A port that cannot be
opened at all says that instead, in the same place. Nothing is retried in the
background either way: one wrong choice costs one sweep, not a queue of them.

Afterwards the picker is still reachable, because a port that answers is not
necessarily the port you meant: **Change port…** on the Bank tab, and
**Choose another…** in the pill that names the port. Choosing a different one
drops whatever was queued for the last one rather than letting it arrive,
minutes later, as failures about a port nobody is on any more.

The browser remembers the last port a scan found boards on, and the baud it
was opened at. Next time the picker opens, that port is already chosen if it
is still plugged in, so somebody who has used the page before but not yet
written a `jk.toml` only has to confirm it. A port in `jk.toml`, or one
already open, still comes first. It is kept in this browser's storage and
nowhere else; a browser that will not keep it starts from the list as
before.

A port chosen here is opened the way `--port` would have opened it: the baud,
timeout, retry count and frame offset in `jk.toml` apply to it, and the
picker shows the baud it is about to use.

The one difference is which addresses are read. `--port` with no `--id` reads
the address `jk.toml` names, or address 1; a port chosen here is swept, all
sixteen, because "what is on this adapter" is the question the picker exists
to answer. A board that answers the sweep but will not give up its nameplate
is listed as **nameplate unreadable** rather than left out — it answered, so
it is there, and its dashboard reads like any other.

Starting `jkctl` twice raises the tab that is already open rather than
leaving a trail of them: the second one asks the first to bring its window
forward and exits quietly. No browser can be told from outside to switch to a
tab, so the page raises itself, and flashes its own title where the browser
will not.

## Seeing it without a battery

```console
$ jkctl ui --simulate
jkctl ui is serving on http://127.0.0.1:8087/
  simulated: 3 fake board(s) in this process, and no serial port open -- nothing here is a battery
  press Ctrl+C to stop
```

`--simulate [N]` puts a bank of simulated boards on an in-memory bus and
serves the page against them: no port is opened, nothing is held, and every
tab works, writes included. It is the same simulator the tests run against,
so what the page shows is what the decoder really produces — which makes it
worth more than a mock-up for judging the tool before wiring anything to a
pack, and is how the screenshots in this repository are taken
(`tools/screenshot.py`).

## Sharing it

By default the server listens on `127.0.0.1` and needs no token. To let
somebody else watch:

```console
$ jkctl ui --listen 0.0.0.0 --read-only
jkctl ui is serving on http://127.0.0.1:8087/?token=Zx8-QpN1yq0hV3mA
  read-only: nothing on the battery can be changed from the browser
  shareable: http://192.168.1.20:8087/?token=Zx8-QpN1yq0hV3mA
  the link includes an access token -- share it deliberately
```

Off loopback an access token is generated and included in the link; the
browser keeps it in a `SameSite=Strict` cookie, so it appears in the address
bar only once (`--token` to pin one, `--no-token` on a network you trust).
`--read-only` refuses every write server-side — not by hiding the buttons —
so a dashboard can be shared without handing over the battery.

Two more guards, both because a local server that can reconfigure a battery
and flash its firmware is worth attacking: the `Host` header must be an IP
address or `localhost` (`--allow-host NAME` if you use a hostname), which is
what stops a page elsewhere from pointing a name it controls at your loopback
address; and every write must carry a header a cross-origin form cannot set.

## How it is built

No web framework, on either side. The server is `http.server`'s threading
server with a small router: everything behind it is synchronous — one serial
port on one worker thread — so thread-per-connection is the shape that fits,
and an event stream is a handler thread blocking on a queue. Updates reach
the browser over one Server-Sent Events stream, multiplexed by event name.

It is the same code underneath as the CLI: every endpoint calls the module
the command calls, so a value read in the browser is the value `jkctl` prints.

The page is [Preact] with [htm], vendored as a single ES module, so there is
**no build step and no Node toolchain** — the files that ship in the wheel are
the files you edit. Dark on true black, because a bank gets checked in a
garage at night as often as at a desk. Colour has jobs rather than
decoration: green for a pack inside its limits, amber for something worth a
look, rose for a raised protection or an irreversible action, blue for
identity and for a bus at rest. Which theme you get is the system's answer
unless you say otherwise; the button in the header cycles system → light →
dark and only an explicit choice is remembered.

What a build step would have caught is caught by [Biome],
`devicectl.devtools.frontlint` (imports and exports resolving across files,
which no bundler is here to do) and `devicectl.devtools.htmcheck`, which
renders every template through the vendored
parser — the same code the browser runs — to catch the mis-parses that render
rather than crash.

What none of those can see is what the page looks like, so
`tools/rendercheck.py` draws all nine tabs in a real browser at three widths
and fails on a page error, a value broken mid-token, a table wider than the
box it scrolls in, or a tab that rendered nothing. Every one of those has
happened here and none of them was a crash. See
[CONTRIBUTING.md](../CONTRIBUTING.md).

[Preact]: https://preactjs.com/
[htm]: https://github.com/developit/htm
[Biome]: https://biomejs.dev/
