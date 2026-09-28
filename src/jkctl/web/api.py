"""The endpoints the browser calls, and what they run against the bus.

Handlers never touch the serial port directly: each one hands a function to
the :class:`~jkctl.web.session.BusWorker`, which owns it.  A handler is
therefore short -- work out what to run, submit it, shape the reply -- and
the interesting code stays in the modules the CLI already uses
(:mod:`jkctl.identity`, :mod:`jkctl.runtime`, :mod:`jkctl.settings`,
:mod:`jkctl.controls`, :mod:`jkctl.doctor`, :mod:`jkctl.firmware`).  A value
read in the browser is the value ``jkctl`` prints because it came out of the
same function.

Two kinds of endpoint:

* the ordinary ones submit with :meth:`BusWorker.run` and block their
  request thread until the answer comes back;
* a firmware flash submits with :meth:`BusWorker.start_job` and answers at
  once with a job id, because it holds the port outright for a minute and
  its progress belongs on the event stream.

Routes marked ``write`` are refused outright when the server was started with
``--read-only``, so a dashboard can be shared without handing over the
battery.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devicectl.web.http import (
    ApiError,
    Request,
    Response,
    Route,
    describe_call,
    discard,
    ok,
    spool,
)
from devicectl.web.progress import JobReporter

from jkctl import (
    __version__,
    controls as C,
    doctor as D,
    firmware as F,
    history as HI,
    identity as I,
    logcodes,
    runtime as R,
    settings as S,
    tracing as TR,
    upgrade as U,
    values as V,
)
from jkctl.device import Device
from jkctl.errors import JkError
from jkctl.registers import (
    ACTIONS,
    INFO,
    SETTINGS,
    TABLES,
    protocol_entries,
    protocol_names,
)
from jkctl.web import schema
from jkctl.web.session import BusWorker, Job, Target

# The largest upload we will take from the browser.  A .jkbms is under 100 kB;
# anything at this size is a mistake, and we would rather say so than buffer it.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# How many samples the recorder keeps per unit before dropping the oldest.
# At the default three-second beat that is about six hours.
RECORDER_SAMPLES = 7200


@dataclass
class Context:
    """Everything a handler needs besides the request."""

    worker: BusWorker
    read_only: bool = False
    firmware_dir: str = ""
    defaults: Target | None = None
    """What a port chosen on the page is opened with.

    The configuration file's baud, timeout, retries and frame offset, as the
    command line would have resolved them.  Without it a port picked in the
    browser was opened at the factory values while the same port named with
    ``--port`` was opened at the configured ones -- the same adapter, read
    two different ways, depending on where it was named.
    """
    samples: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    """The recorder: what the live refresh has seen, per unit."""


# --- helpers -----------------------------------------------------------------------------


def _unit(ctx: Context, req: Request) -> int:
    """Which board this request is about."""
    raw = req.param("id") or (req.json().get("id") if req.method == "POST" else "")
    if raw in ("", None):
        units = ctx.worker.units()
        if not units:
            raise ApiError(409, "no unit has been found on this bus yet")
        return units[0].slave
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ApiError(400, f"not an address: {raw!r}") from None


def _device(worker: BusWorker, slave: int) -> Device:
    """Return the device object for one address, on the worker thread."""
    return worker.device(slave)


def _reading(worker: BusWorker, slave: int) -> R.Runtime:
    """One runtime read, on the worker thread."""
    return R.read(_device(worker, slave))


# --- reads -------------------------------------------------------------------------------


def get_state(ctx: Context, req: Request) -> Response:
    """Report the link, the units, and what this build is."""
    return ok(
        {
            "version": __version__,
            "readOnly": ctx.read_only,
            "link": ctx.worker.state(),
            "jobs": ctx.worker.jobs(),
        }
    )


def get_ports(ctx: Context, req: Request) -> Response:
    """Every serial port on this host, named the way a person would know it.

    A first run should not need a configuration file to exist, and "which of
    these is the RS485 adapter" is answered by the adapter's own description
    far better than by ``/dev/ttyUSB0``.
    """
    return ok(
        {
            "ports": list_serial_ports(),
            "current": _target_json(ctx.worker.target),
            # What a port chosen here will be opened with, so the page can
            # show the baud it is about to use rather than assume one.
            "defaults": _target_json(ctx.defaults),
        }
    )


def list_serial_ports() -> list[dict[str, Any]]:
    """List the serial ports, with whatever the system says each one is."""
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - pyserial is a hard dependency
        return []
    found = []
    for port in list_ports.comports():
        found.append(
            {
                "device": port.device,
                "description": (port.description or "").strip(),
                "manufacturer": (port.manufacturer or "").strip(),
                "serial": (getattr(port, "serial_number", "") or "").strip(),
                "hwid": (port.hwid or "").strip(),
                "likely": _looks_like_an_adapter(port),
            }
        )
    return sorted(found, key=lambda p: (not p["likely"], p["device"]))


def _looks_like_an_adapter(port: Any) -> bool:
    """Whether a port looks like a USB serial adapter rather than a modem."""
    text = f"{port.device} {port.description or ''} {port.hwid or ''}".lower()
    if any(
        word in text for word in ("ttyusb", "ttyacm", "usb-serial", "ft232", "ch340")
    ):
        return True
    return "usb" in text


def _target_json(target: Target | None) -> dict[str, Any] | None:
    """Describe a bus target, if there is one."""
    if target is None:
        return None
    return {
        "port": target.port,
        "baud": target.baud,
        "timeout": target.timeout,
        "retries": target.retries,
        "addrOffset": target.addr_offset,
        "ids": list(target.slaves),
    }


def get_bank(ctx: Context, req: Request) -> Response:
    """One summary row per unit on the bus -- the whole bank at a glance."""

    def read(worker: BusWorker) -> list[dict[str, Any]]:
        units = worker.units() or worker.rescan()
        rows = []
        for unit in units:
            try:
                rows.append(schema.bank_row_json(unit, _reading(worker, unit.slave)))
            except JkError as exc:
                rows.append(schema.bank_row_json(unit, None, str(exc)))
        return rows

    return ok({"units": ctx.worker.run("reading the bank", read)})


def post_scan(ctx: Context, req: Request) -> Response:
    """Sweep the bus and remember what answered."""
    units = ctx.worker.run("scanning the bus", lambda worker: worker.rescan())
    return ok({"units": [schema.unit_json(u) for u in units]})


def get_dashboard(ctx: Context, req: Request) -> Response:
    """Everything one unit's dashboard draws, in one read."""
    slave = _unit(ctx, req)

    def read(worker: BusWorker) -> dict[str, Any]:
        device = _device(worker, slave)
        reading = R.read(device)
        ident = I.read(device)
        # The settings the dashboard draws beside the readings: the three
        # switches, the capacity the state of charge is scaled against, and
        # the setpoints the cards draw their bands against.  They are all in
        # one table and read in one pass, so a setpoint that is on a band is
        # cheaper to ask for here than to send the browser to another tab
        # for.
        wanted = (
            "volCellOV",
            "volCellUV",
            "volStartBalan",
            "tmpBatCOT",
            "tmpBatCUT",
            "tmpBatDcOT",
            "tmpBatDCHUT",
            "tmpMosOT",
            "tmpMosOTPR",
            "batChargeEn",
            "batDischargeEn",
            "balanEn",
            "cellCount",
            "capBatCell",
            "volCellRCV",
            "volCellRFV",
        )
        catalog = device.catalog
        regs = [r for r in (catalog.get(k, SETTINGS) for k in wanted) if r is not None]
        limits = device.read_fields(regs)
        return {
            "id": slave,
            "identity": schema.identity_json(ident),
            "runtime": schema.runtime_json(reading),
            "limits": schema.registers_json(regs, limits),
            "clock": _clock_json(reading),
        }

    return ok(ctx.worker.run(f"reading BMS {slave}", read))


def _clock_json(reading: R.Runtime) -> dict[str, Any]:
    """Render the unit's RTC, and how far it is from this host's.

    ``local`` is the board's time as its own vendor's application shows it:
    the board keeps no zone, and its count is from local midnight (see
    :func:`jkctl.identity.rtc_epoch`), so local time is all it has.
    """
    from datetime import datetime, timezone

    from devicectl.clock import format_drift

    when = I.rtc_to_datetime(reading.get("rtcCounter"))
    if when is None:
        return {"local": None, "driftS": None, "drift": None}
    drift = (when - datetime.now(timezone.utc)).total_seconds()
    return {
        "local": when.replace(tzinfo=None).isoformat(timespec="seconds"),
        "driftS": round(drift, 1),
        "drift": format_drift(drift),
    }


def get_registers(ctx: Context, req: Request) -> Response:
    """Every register of one or all tables, with what it currently reads."""
    slave = _unit(ctx, req)
    table = req.param("table")
    if table and table not in TABLES:
        raise ApiError(400, f"no table {table!r}")

    def read(worker: BusWorker) -> list[dict[str, Any]]:
        device = _device(worker, slave)
        out: list[dict[str, Any]] = []
        for name in [table] if table else list(TABLES):
            regs = device.catalog.table(name)
            out.extend(
                schema.registers_json(regs, device.read_fields(regs), device.unwritable)
            )
        return out

    return ok({"id": slave, "registers": ctx.worker.run("reading registers", read)})


def get_settings(ctx: Context, req: Request) -> Response:
    """Read the whole configuration table, described and valued."""
    slave = _unit(ctx, req)

    def read(worker: BusWorker) -> list[dict[str, Any]]:
        device = _device(worker, slave)
        regs = device.catalog.table(SETTINGS)
        return schema.registers_json(regs, device.read_fields(regs), device.unwritable)

    return ok({"id": slave, "settings": ctx.worker.run("reading settings", read)})


def get_alarms(ctx: Context, req: Request) -> Response:
    """Every protection bit one unit currently raises."""
    slave = _unit(ctx, req)
    reading = ctx.worker.run("reading alarms", lambda w: _reading(w, slave))
    return ok(
        {
            "id": slave,
            "alarms": [
                {"register": a.register, "bit": a.bit, "name": a.name, "state": a.state}
                for a in reading.alarms
            ],
        }
    )


def get_doctor(ctx: Context, req: Request) -> Response:
    """One health pass over a unit."""
    slave = _unit(ctx, req)
    report = ctx.worker.run(
        f"checking BMS {slave}", lambda w: D.check(_device(w, slave))
    )
    return ok(schema.report_json(report))


def get_protocols(ctx: Context, req: Request) -> Response:
    """Read the protocol lists, and which one each port is set to."""
    slave = _unit(ctx, req)
    entries = protocol_entries()

    def read(worker: BusWorker) -> dict[str, Any]:
        device = _device(worker, slave)
        ident = I.read(device)
        keys = (
            "lcdBuzzerTrigger",
            "dry1Trigger",
            "dry2Trigger",
            "lcdBuzzerTriggerVal",
            "lcdBuzzerReleaseVal",
            "dry1TriggerVal",
            "dry1ReleaseVal",
            "dry2TriggerVal",
            "dry2ReleaseVal",
            "rcvTime",
            "rfvTime",
            "dataStoredPeriod",
        )
        regs = [r for r in (device.catalog.get(k, INFO) for k in keys) if r is not None]
        return {
            "ports": schema.identity_json(ident)["ports"],
            "outputs": schema.registers_json(
                regs, device.read_fields(regs), device.unwritable
            ),
        }

    doc = ctx.worker.run("reading the ports", read)
    doc["id"] = slave
    doc["lists"] = {
        kind: [
            {"id": number, "name": en, "vendor": native}
            for number, (en, native) in sorted(table.items())
        ]
        for kind, table in entries.items()
    }
    from jkctl.cli.commands.protocols import SELECTORS, SETTABLE

    doc["selectors"] = [
        {"name": word, "register": key, "kind": kind, "settable": word in SETTABLE}
        for word, (key, kind) in SELECTORS.items()
    ]
    return ok(doc)


def get_export(ctx: Context, req: Request) -> Response:
    """One unit's writable configuration, as the file ``settings export`` writes."""
    slave = _unit(ctx, req)
    text = ctx.worker.run("exporting settings", lambda w: S.export(_device(w, slave)))
    return Response(
        body=text.encode("utf-8"),
        content_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="jk-{slave}-settings.json"'
        },
    )


def get_firmware_library(ctx: Context, req: Request) -> Response:
    """Every ``.jkbms`` under a directory, judged against one unit."""
    directory = req.param("dir") or ctx.firmware_dir
    if not directory:
        return ok({"dir": "", "files": [], "note": "no firmware directory set"})
    if not os.path.isdir(directory):
        raise ApiError(400, f"not a directory: {directory}")
    slave = _unit(ctx, req)
    found = F.scan(directory)
    ident = ctx.worker.run("reading the nameplate", lambda w: I.read(_device(w, slave)))
    files = []
    for item in found:
        checks = (
            F.gate(item.firmware, ident.model, ident.software_version)
            if item.firmware
            else None
        )
        files.append(schema.candidate_json(item, checks))
    return ok(
        {
            "dir": directory,
            "unit": {"model": ident.model, "version": ident.software_version},
            "files": files,
        }
    )


def get_samples(ctx: Context, req: Request) -> Response:
    """Return what the live refresh has recorded for one unit."""
    slave = _unit(ctx, req)
    return ok({"id": slave, "samples": ctx.samples.get(slave, [])})


def get_jobs(ctx: Context, req: Request) -> Response:
    """Every job still remembered."""
    return ok({"jobs": ctx.worker.jobs()})


def get_trace(ctx: Context, req: Request) -> Response:
    """Render the serial recording as the plain-text report somebody sends on.

    A download rather than a page: it is thousands of lines on a bad day,
    the point of making one is to attach it to a bug report, and a browser
    is the wrong place to read hex.
    """
    worker = ctx.worker
    text = TR.report(
        worker.trace,
        version=__version__,
        target=worker.target,
        units=worker.units(),
        catalog=worker.catalog,
        turnaround=worker.turnaround,
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Response(
        body=text.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="jkctl-trace-{stamp}.txt"'
        },
    )


def post_trace(ctx: Context, req: Request) -> Response:
    """Start, stop or empty the serial recording.

    Not a ``write`` route.  Recording puts nothing on the bus and changes
    nothing on the battery, so a server started ``--read-only`` -- which is
    how a dashboard gets shared with whoever is asking what went wrong --
    can still be asked to record what it is doing.
    """
    doc = req.json()
    action = str(doc.get("action") or "").strip().lower()
    trace = ctx.worker.trace
    if action == "start":
        trace.start(automatic=bool(doc.get("automatic")))
    elif action == "stop":
        trace.stop()
    elif action == "clear":
        trace.clear()
    else:
        raise ApiError(400, f"not something a trace does: {action or 'nothing'!r}")
    return ok({"trace": trace.state(), "link": ctx.worker.state()})


# The endpoints a recording does not record.  Everything about the recording
# itself: "start" would land in its own first line, the download would be the
# last thing every report ever said, and neither tells anybody anything.
UNRECORDED = ("/api/trace",)


def watcher(ctx: Context):
    """Return the hook that writes what the page asked for into the recording.

    The frames say what went on the bus.  They never say which button was
    pressed, what was typed in the box beside it, or what the page was told
    afterwards -- so a report full of perfectly decoded Modbus could still
    leave the one question nobody could answer, which is what the person was
    doing at the time.  This is that half: the request as it arrives, body
    and all, and the status it was answered with, with the frames it caused
    in between.
    """

    def watch(req: Request, status: int | None, error: str) -> None:
        if req.path in UNRECORDED:
            return
        ctx.worker.trace.called(*describe_call(req, status, error))

    return watch


# --- writes ------------------------------------------------------------------------------


def post_port(ctx: Context, req: Request) -> Response:
    """Point the server at a serial port, and scan it."""
    doc = req.json()
    port = str(doc.get("port") or "").strip()
    if not port:
        raise ApiError(400, "no port given")
    ids = doc.get("ids") or []
    # Whatever the page did not say is what the configuration file says, not
    # a second copy of the factory values written down here.
    base = ctx.defaults or Target(port=port)
    target = Target(
        port=port,
        baud=int(doc.get("baud") or base.baud),
        timeout=float(doc.get("timeout") or base.timeout),
        retries=int(doc.get("retries") or base.retries),
        addr_offset=int(doc.get("addrOffset") or base.addr_offset),
        slaves=tuple(int(i) for i in ids),
    )
    ctx.worker.set_target(target)
    units = ctx.worker.run("scanning the bus", lambda worker: worker.rescan())
    return ok(
        {
            "target": _target_json(ctx.worker.target),
            "units": [schema.unit_json(u) for u in units],
        }
    )


def post_link(ctx: Context, req: Request) -> Response:
    """Open or close the port on demand."""
    action = str(req.json().get("action") or "").lower()
    if action == "release":
        ctx.worker.release()
    elif action == "connect":
        ctx.worker.connect()
    else:
        raise ApiError(400, "action must be 'connect' or 'release'")
    return ok({"link": ctx.worker.state()})


def post_live(ctx: Context, req: Request) -> Response:
    """Turn the shared live refresh on or off."""
    doc = req.json()
    ctx.worker.set_live(bool(doc.get("live")), doc.get("interval"))
    return ok({"link": ctx.worker.state()})


def post_settings(ctx: Context, req: Request) -> Response:
    """Plan a settings change, and write it unless this was a dry run.

    The same plan/diff/apply the CLI runs, and the same refusals: a value out
    of the datasource's range never reaches the wire, and a register JK's map
    does not mark writable is turned down here rather than attempted.
    """
    doc = req.json()
    slave = _unit(ctx, req)
    wanted = doc.get("changes") or {}
    # A chemistry preset is a list of settings like any other change, so it
    # is planned, shown and written by this same path (controls.PRESET_VALUES).
    if doc.get("preset"):
        try:
            wanted = C.preset_values(str(doc["preset"]))
        except C.ControlError as exc:
            raise ApiError(400, str(exc)) from exc
    if not isinstance(wanted, dict) or not wanted:
        raise ApiError(400, "no changes given")
    dry = bool(doc.get("dryRun"))
    # The Ports tab edits the writable subset of the device-info table through
    # this same path, so every register JK marks RW is edited, planned and
    # confirmed the one way rather than in two different ones.
    table = str(doc.get("table") or SETTINGS)
    if table not in TABLES:
        raise ApiError(400, f"no table {table!r}")

    def write(worker: BusWorker) -> dict[str, Any]:
        device = _device(worker, slave)
        coerced: dict[str, Any] = {}
        for key, raw in wanted.items():
            reg, index = S.resolve_key(device.catalog, key, table)
            text = raw if isinstance(raw, str) else repr(raw)
            coerced[key] = (
                V.coerce_input(reg, text)
                if index is None
                else V.coerce_element(reg, text)
            )
        changes = S.plan(device, coerced, table=table)
        if not dry:
            S.apply(device, changes)
        return {
            "id": slave,
            "table": table,
            "dryRun": dry,
            "changes": [schema.change_json(c) for c in changes],
        }

    return ok(ctx.worker.run(f"writing settings on BMS {slave}", write))


def post_switch(ctx: Context, req: Request) -> Response:
    """Flip one bit of the multiplexed switch word."""
    doc = req.json()
    slave = _unit(ctx, req)
    bit, on = doc.get("bit"), bool(doc.get("on"))
    if not isinstance(bit, int):
        raise ApiError(400, "bit must be a number")

    def write(worker: BusWorker) -> dict[str, Any]:
        device = _device(worker, slave)
        word = device.set_bit(S.SWITCH_REGISTER, bit, on, SETTINGS)
        return {"id": slave, "word": word}

    return ok(ctx.worker.run(f"setting a switch on BMS {slave}", write))


def get_switches(ctx: Context, req: Request) -> Response:
    """Read the sixteen multiplexed on/off settings, named."""
    slave = _unit(ctx, req)

    def read(worker: BusWorker) -> list[dict[str, Any]]:
        device = _device(worker, slave)
        word = device.read(S.SWITCH_REGISTER, SETTINGS)
        return [
            {"bit": bit, "name": name, "on": on, "state": state}
            for bit, name, on, state in S.switch_state(device.catalog, word)
        ]

    return ok({"id": slave, "switches": ctx.worker.run("reading switches", read)})


def post_control(ctx: Context, req: Request) -> Response:
    """Drive one of the three main switches."""
    doc = req.json()
    slave = _unit(ctx, req)
    what = str(doc.get("what") or "")
    if what not in S.TOGGLES:
        raise ApiError(400, f"no switch named {what!r}")
    on = bool(doc.get("on"))

    def write(worker: BusWorker) -> dict[str, Any]:
        device = _device(worker, slave)
        C.toggle(device, what, on)
        return {"id": slave, "state": C.toggle_state(device)}

    return ok(ctx.worker.run(f"{what} {'on' if on else 'off'} on BMS {slave}", write))


def post_protocol(ctx: Context, req: Request) -> Response:
    """Point one port at a different protocol."""
    from jkctl.cli.commands.protocols import SELECTORS, SETTABLE

    doc = req.json()
    slave = _unit(ctx, req)
    selector = str(doc.get("selector") or "")
    if selector not in SELECTORS:
        raise ApiError(400, f"no port named {selector!r}")
    if selector not in SETTABLE:
        raise ApiError(
            400,
            f"{selector} cannot be set: JK's register map does not mark "
            f"{SELECTORS[selector][0]} writable",
        )
    key, kind = SELECTORS[selector]
    number = doc.get("number")
    if not isinstance(number, int) or number not in protocol_names().get(kind, {}):
        raise ApiError(400, f"{number!r} is not in the {kind} protocol list")
    ctx.worker.run(
        f"setting {selector} on BMS {slave}",
        lambda w: _device(w, slave).write(key, number, INFO),
    )
    return ok({"id": slave, "selector": selector, "number": number})


def post_action(ctx: Context, req: Request) -> Response:
    """Fire one of the write-only action slots."""
    doc = req.json()
    slave = _unit(ctx, req)
    name = str(doc.get("action") or "")

    if name == "time-sync":
        when = ctx.worker.run(
            f"syncing the clock on BMS {slave}",
            lambda w: C.sync_clock(_device(w, slave)),
        )
        return ok({"id": slave, "set": when.isoformat(timespec="seconds")})
    if name in ("voltage-calibration", "current-calibration"):
        raw = doc.get("value")
        if not isinstance(raw, (int, float)):
            raise ApiError(400, "a calibration needs the true reading")
        call = (
            C.calibrate_voltage
            if name == "voltage-calibration"
            else C.calibrate_current
        )
        ctx.worker.run(
            f"{ACTIONS[name].label} on BMS {slave}",
            lambda w: call(_device(w, slave), int(raw)),
        )
        return ok({"id": slave, "action": name, "value": int(raw)})
    if name in ("lifepo4", "li-ion", "lto"):
        ctx.worker.run(
            f"{name} preset on BMS {slave}",
            lambda w: C.preset(_device(w, slave), name),
        )
        return ok({"id": slave, "action": name})
    if name == "emergency":
        ctx.worker.run(
            f"emergency start on BMS {slave}", lambda w: C.emergency(_device(w, slave))
        )
        return ok({"id": slave, "action": name})
    if name == "shutdown":
        ctx.worker.run(
            f"shutdown of BMS {slave}", lambda w: C.shutdown(_device(w, slave))
        )
        return ok({"id": slave, "action": name})
    if name in C.BOARD_ACTIONS:
        ctx.worker.run(
            f"{ACTIONS[name].label} on BMS {slave}",
            lambda w: C.board_action(_device(w, slave), name),
        )
        return ok({"id": slave, "action": name})
    raise ApiError(400, f"no action named {name!r}")


def get_history(ctx: Context, req: Request) -> Response:
    """Attempt the stored fault records over the wire (see :func:`post_history_dump`).

    Answers with ``supported: false`` and the reason rather than an error:
    stock firmware does not serve the records over Modbus (the firmware bounds
    a read to frames 01-03 and keeps the records where only Bluetooth reaches
    them), so the expected outcome here is a clear "not over the wire -- load a
    flash dump", which the tab offers.
    """
    slave = _unit(ctx, req)
    base = req.param("base")
    where = int(base, 0) if base else None
    try:
        records = ctx.worker.run(
            f"reading BMS {slave}'s history",
            lambda w: HI.read(_device(w, slave), where),
        )
    except HI.HistoryError as exc:
        return ok({"id": slave, "supported": False, "why": str(exc), "records": []})
    return ok(
        {
            "id": slave,
            "supported": True,
            "source": "wire",
            "why": "",
            "records": [schema.record_json(r) for r in records],
        }
    )


def post_history_dump(ctx: Context, req: Request) -> Response:
    """Decode the stored fault records out of an uploaded full flash image.

    The records are not on the wire, but a full flash dump of a patched board
    holds them; this reads the record ring straight out of the uploaded image
    and touches no hardware, so it works whatever the connected unit is running
    (and even with none connected).
    """
    path = spool(
        req,
        max_bytes=MAX_UPLOAD_BYTES,
        prefix="jkctl-ui-",
        suffix=".bin",
        too_large="that file is far larger than a 128 KiB flash image",
    )
    try:
        image = path.read_bytes()
    finally:
        discard(path)
    records = HI.read_dump(image)
    return ok(
        {
            "supported": True,
            "source": "dump",
            "why": "",
            "records": [schema.record_json(r) for r in records],
        }
    )


def get_log_codes(ctx: Context, req: Request) -> Response:
    """Every stored-record code the vendor's application names."""
    return ok({"codes": {str(k): v for k, v in logcodes.all_names().items()}})


def post_import(ctx: Context, req: Request) -> Response:
    """Plan (and optionally write) a configuration file onto a unit."""
    doc = req.json()
    slave = _unit(ctx, req)
    text = doc.get("file")
    if not isinstance(text, str):
        raise ApiError(400, "no file contents given")
    dry = bool(doc.get("dryRun", True))

    def write(worker: BusWorker) -> dict[str, Any]:
        device = _device(worker, slave)
        wanted = S.load(device.catalog, text)
        changes = S.plan(device, wanted)
        if not dry:
            S.apply(device, changes)
        return {
            "id": slave,
            "dryRun": dry,
            "changes": [schema.change_json(c) for c in changes],
        }

    return ok(ctx.worker.run(f"importing settings onto BMS {slave}", write))


def post_firmware_check(ctx: Context, req: Request) -> Response:
    """Parse an uploaded image and judge it against a unit, writing nothing."""
    slave = _unit(ctx, req)
    path = _spool(req)
    try:
        fw = F.load(path)
    except F.FirmwareError as exc:
        raise ApiError(400, str(exc)) from None
    finally:
        discard(path)
    ident = ctx.worker.run("reading the nameplate", lambda w: I.read(_device(w, slave)))
    checks = F.gate(fw, ident.model, ident.software_version, force=False)
    forced = F.gate(fw, ident.model, ident.software_version, force=True)
    return ok(
        {
            "id": slave,
            "firmware": schema.firmware_json(fw),
            "unit": {"model": ident.model, "version": ident.software_version},
            "checks": [schema.check_json(c) for c in checks],
            "compatible": not any(c.blocking for c in checks),
            "forcible": not any(c.blocking for c in forced),
        }
    )


def post_firmware_flash(ctx: Context, req: Request) -> Response:
    """Flash an uploaded image, as a job that owns the port outright."""
    slave = int(req.param("id") or 0) or _first_unit(ctx)
    force = req.flag("force")
    path = _spool(req)
    try:
        fw = F.load(path)
    except F.FirmwareError as exc:
        discard(path)
        raise ApiError(400, str(exc)) from None

    def run(worker: BusWorker, job: Job) -> dict[str, Any]:
        try:
            device = _device(worker, slave)
            ident = I.read(device)
            checks = F.gate(fw, ident.model, ident.software_version, force=force)
            blocking = [c for c in checks if c.blocking]
            if blocking:
                raise F.FirmwareError("; ".join(c.detail for c in blocking))
            worker.notify_progress(job, 0.0, "arming the bootloader")
            U.upgrade(
                device.bus,
                slave,
                fw.image,
                report=JobReporter(job, unit="block"),
            )
            # A reboot can leave the RTC at the firmware's default, and the
            # board keeps no time zone, so re-sync it to local time rather than
            # leave the clock a zone offset out.  Best effort: never fail a
            # good flash over a clock.
            worker.notify_progress(job, 1.0, "syncing the clock")
            result = {"id": slave, "version": fw.version}
            synced = C.resync_clock_after_flash(device)
            if synced is not None:
                result["clockSet"] = synced.isoformat(timespec="seconds")
            return result
        finally:
            discard(path)

    job = ctx.worker.start_job(f"flashing BMS {slave}", run, exclusive=True)
    return ok({"job": job.as_dict()})


def _first_unit(ctx: Context) -> int:
    """Return the first unit on the bus, for a request that named no address."""
    units = ctx.worker.units()
    if not units:
        raise ApiError(409, "no unit has been found on this bus yet")
    return units[0].slave


def _spool(req: Request) -> Path:
    """Write an uploaded body to a temporary file and return its path."""
    return spool(
        req,
        max_bytes=MAX_UPLOAD_BYTES,
        prefix="jkctl-ui-",
        suffix=".jkbms",
        too_large="that file is far larger than any JK firmware image",
    )


# --- the live refresh --------------------------------------------------------------------


def publish_live(worker: BusWorker) -> None:
    """Read every known unit once and publish it.

    Called by the worker's own idle tick, so it is already on the worker
    thread and may use the bus directly.  A unit that fails to answer is
    published as a failed row rather than stopping the sweep: one board off
    its DIP switch should not blank the other three.

    Two events come out of the one read.  ``bank`` is the tile per unit, and
    ``runtime`` is the whole reading -- the cells, the alarms, every summary
    field -- which is what the dashboard draws.  The reading was taken either
    way; publishing only the tile meant a dashboard left open showed the
    numbers it was opened with until something asked for them again.
    """
    ctx = _live_context
    if ctx is None:
        return
    rows = []
    readings: dict[str, Any] = {}
    for unit in worker.units():
        try:
            reading = R.read(worker.device(unit.slave))
            rows.append(schema.bank_row_json(unit, reading))
            readings[str(unit.slave)] = schema.runtime_json(reading)
            _record(ctx, unit.slave, reading)
        except JkError as exc:
            rows.append(schema.bank_row_json(unit, None, str(exc)))
    worker.events.publish("bank", {"units": rows}, sticky=True)
    worker.events.publish("runtime", {"units": readings}, sticky=True)


def _record(ctx: Context, slave: int, reading: R.Runtime) -> None:
    """Keep one sample of what the pack was doing, for the history chart."""
    samples = ctx.samples.setdefault(slave, [])
    samples.append(
        {
            "t": time.time(),
            "power": reading.get("batWatt"),
            "current": reading.get("batCurrent"),
            "voltage": reading.get("batVol"),
            "soc": reading.get("socRelativeStateOfCharge"),
            "delta": reading.get("maxVoltDelta"),
            "temp": schema._hottest(reading),
        }
    )
    if len(samples) > RECORDER_SAMPLES:
        del samples[: len(samples) - RECORDER_SAMPLES]


# The context the worker's own refresh publishes through.  The worker is
# started before the server that owns the context, and the refresh has no
# request to carry one, so there is exactly one and it is set here.
_live_context: Context | None = None


def use_context(ctx: Context) -> None:
    """Tell the live refresh which context to record into."""
    global _live_context
    _live_context = ctx


ROUTES: dict[tuple[str, str], Route[Context]] = {
    ("GET", "/api/state"): Route(get_state),
    ("GET", "/api/ports"): Route(get_ports),
    ("GET", "/api/bank"): Route(get_bank),
    ("GET", "/api/dashboard"): Route(get_dashboard),
    ("GET", "/api/registers"): Route(get_registers),
    ("GET", "/api/settings"): Route(get_settings),
    ("GET", "/api/switches"): Route(get_switches),
    ("GET", "/api/alarms"): Route(get_alarms),
    ("GET", "/api/doctor"): Route(get_doctor),
    ("GET", "/api/protocols"): Route(get_protocols),
    ("GET", "/api/export"): Route(get_export),
    ("GET", "/api/firmware/library"): Route(get_firmware_library),
    ("GET", "/api/samples"): Route(get_samples),
    ("GET", "/api/history"): Route(get_history),
    ("GET", "/api/log-codes"): Route(get_log_codes),
    ("GET", "/api/jobs"): Route(get_jobs),
    ("GET", "/api/trace"): Route(get_trace),
    ("POST", "/api/port"): Route(post_port, write=True),
    ("POST", "/api/scan"): Route(post_scan),
    ("POST", "/api/link"): Route(post_link),
    ("POST", "/api/live"): Route(post_live),
    ("POST", "/api/trace"): Route(post_trace),
    ("POST", "/api/settings"): Route(post_settings, write=True),
    ("POST", "/api/switch"): Route(post_switch, write=True),
    ("POST", "/api/control"): Route(post_control, write=True),
    ("POST", "/api/protocol"): Route(post_protocol, write=True),
    ("POST", "/api/action"): Route(post_action, write=True),
    ("POST", "/api/import"): Route(post_import, write=True),
    ("POST", "/api/history/dump"): Route(post_history_dump, raw_body=True),
    ("POST", "/api/firmware/check"): Route(post_firmware_check, raw_body=True),
    ("POST", "/api/firmware/flash"): Route(
        post_firmware_flash, write=True, raw_body=True
    ),
}


__all__ = [
    "ROUTES",
    "Context",
    "list_serial_ports",
    "publish_live",
    "use_context",
]
