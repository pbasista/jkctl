# Contributing

## Getting set up

```sh
uv sync
uv run pytest
```

That is the light set -- pytest, ruff, ty -- pure Python, quick, and it
installs anywhere the program does. The two checks that need a real engine
(an embedded V8, a headless Chromium) are in a separate `browser` group:

```sh
uv sync --group browser
```

Take it if you touch the web UI. It is opt-in because jkctl is meant to run
on small hosts -- a Raspberry Pi 3 reports `armv7l`, and `mini-racer`
publishes no armv7 wheel, so having it in the default set would turn
`uv run jkctl` in a checkout into a V8 build. Once the group is synced a
plain `uv run` keeps it; a plain `uv sync` trims back to the light set.

Everything the checks run:

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
biome ci src/jkctl/web/static/          # the browser half
```

[Biome] is a single native binary and needs no Node (`pacman -S biome`, a
release binary, or `npx @biomejs/biome`). It is not a project dependency, and
the two Python checks run under `pytest` as well, so a plain `uv run pytest`
covers the browser half too -- `htmcheck` and its tests skip themselves,
saying which group to sync, when the V8 is not there.

Two more need a browser, so they are not in that list and are not run under
`pytest`. They drive the real page over `jkctl ui --simulate`, which puts a
bank of fake boards on an in-memory bus and needs no hardware:

```sh
uv sync --group browser                        # once: playwright
uv run python -m playwright install chromium   # once: the browser itself
uv run tools/rendercheck.py    # what only a layout engine sees
uv run tools/screenshot.py     # docs/img/, for the README
```

`rendercheck` is worth running after anything that touches the page. Every
defect it looks for got past all seven checks above: a value column wide
enough to push four columns off-screen, a model number split across two
lines, a tab that renders nothing because an import was mistyped. None of
them is a crash, so nothing without a layout engine can see them.

[Biome]: https://biomejs.dev/

## How the code is laid out

`src/jkctl/` is one module per subject, and **none of them prints**. Only
`cli/` prints, prompts, or returns exit codes. That is what keeps the device
logic testable and what would let a second front end reuse it unchanged.

- `modbus.py` is the wire: framing, CRC, retries, the register bases.
- `protocol.py` is the field layout, parsed from JK's own datasource.
- `registers.py` joins the two: a named, typed, bounded, addressable register,
  and whether JK's document marks it writable.
- `values.py` turns what someone typed into what the wire takes, and refuses
  what will not fit.
- `device.py` is one BMS, read and written by field name.
- `identity.py`, `runtime.py`, `settings.py`, `controls.py` are one subject
  each, built on `device.py`.
- `firmware.py` and `upgrade.py` are the `.jkbms` container and the transfer.
- `doctor.py` is composition over those: one read, and everything that can be
  concluded from holding two of its numbers up against each other.
- `history.py` and `logcodes.py` are the board's own stored records: the
  layout, and what JK calls each code.
- `web/` is the second front end, and it obeys the same rule: it prints
  nothing and decides nothing about the device, it calls the same modules the
  commands call. `web/session.py` owns the serial port and runs every request
  through one worker, because only one program can hold a port and only one
  place should know that.

## The browser half

`src/jkctl/web/static/` is plain ES modules and plain CSS -- edit and reload,
there is nothing to build, and the files that ship in the wheel are the files
you edit. Three things check it in place of a build step:

- **Biome** parses and lints it, better than anything hand-written here could.
- **`devicectl.devtools.frontlint`** looks across the files, which is normally
  a bundler's job: every import has to name a file that exists and a name it
  exports, and every export has to be imported by somebody. Without it a
  mistyped path is a blank page found by reloading and not before.
- **`devicectl.devtools.htmcheck`** renders every `html` template through the vendored
  preact-htm bundle -- the same parser the browser runs -- and reports the one
  shape no valid output contains. An attribute that lost its `$` does not
  crash the page; it quietly swallows the markup up to the next `}`.

Adding a tab means a module in `static/js/`, an entry in `TABS` in `app.js`,
and an endpoint in `web/api.py` that calls the module the CLI already calls.
Do not reach for the device from a request handler: hand the work to the
worker, which owns the port.

## Adding a command

Write the handler in the right module of `src/jkctl/cli/commands/`, describe
it in that module's `add_parsers`, and name it in that module's `COMMANDS`.
Nothing outside that file changes. If the command needs no serial port, or
only a bus rather than one addressed unit, say so with `Need` in the
`Command` -- do not open anything yourself.

## Tests

No test may need hardware, a serial port or a network. The suite wires a real
`modbus.Bus` straight to `simulator.Sim` in memory (`tests/conftest.py`), so a
CLI test exercises the actual framing, chunking and decoding. If you teach the
tool about a new device behaviour, teach the simulator about it too -- that is
what stops the tool being tested against a model of the device that is kinder
than the device.

CLI tests call `cli.main([...])` and assert on the exit code and `capsys`.
Prompts are tested by patching `builtins.input`, including the `EOFError`
case, because a command left running unattended must stop rather than raise.

## Writing about the device

Every non-obvious constant, refusal and workaround in this codebase exists
because a real unit behaved that way. Say so in the comment, and say how it is
known -- a measurement, JK's own document, or a function in the decompiled
application. A comment that says *what* the code does is worth little; one
that says why the device made it necessary is worth a great deal, and it is
the only defence against someone later "simplifying" a workaround away.

Anything not yet proven against hardware must say so, in the docstring and in
`docs/reference.md`.

## Before you send it

- `uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest`
- New device behaviour recorded in `research/windows/windows-findings.md`
  with its evidence.
- `docs/reference.md` updated if you changed what is proven or what is not.
