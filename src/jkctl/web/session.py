"""The one serial port, and everything that queues behind it.

An RS485 bus is a single wire with up to sixteen boards on it, and one
program at a time may hold the port.  So the server does not let request
handlers touch it: every read and every write is a task submitted to this
worker, which owns the one :class:`~jkctl.modbus.Bus` and runs tasks one at a
time on its own thread.

The queue, the job registry, the activity ticker and the published link
state are :class:`devicectl.web.worker.Worker`, which is that machine
without a bus in it.  What is here is what a bus adds: opening the port,
the boards found on it, the per-address :class:`~jkctl.device.Device`
cache, and the flash that owns the wire outright.

Three ways in:

* :meth:`BusWorker.run` for the short reads a page needs, which blocks the
  calling request thread until the worker gets to it;
* :meth:`BusWorker.start_job` for the slow ones, which returns immediately
  and reports progress as events;
* the same with ``exclusive=True`` for a firmware flash, which is not a
  Modbus operation at all: after the arming write the link becomes raw
  XMODEM and stays that way until the transfer ends, so nothing else may go
  near the port and the poll timer has to stop.

A task here is handed *the worker*, not the bus, because what it usually
wants is a :class:`~jkctl.device.Device` for one address rather than the
wire itself.

When nothing is going on the worker closes the port
(:data:`DEFAULT_IDLE_TIMEOUT_S`), handing it back to whatever else wants it
-- a terminal running ``jkctl``, or another program on the same adapter.
That matters more here than it would over a network: a held ``/dev/ttyUSB0``
is a locked door.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from devicectl.trace import Recorder
from devicectl.web.events import Broadcaster
from devicectl.web.http import HTTP_CONFLICT
from devicectl.web.worker import (
    ACTIVITY_HISTORY,
    DEFAULT_IDLE_TIMEOUT_S,
    DEFAULT_POLL_INTERVAL_S,
    JOB_NOTIFY_INTERVAL_S,
    JOB_RETENTION_S,
    LOOP_TICK_S,
    MAX_POLL_INTERVAL_S,
    MIN_POLL_INTERVAL_S,
    STOP_JOIN_S,
    Job,
    Task,
    Worker,
)

from jkctl import identity as I, modbus as M
from jkctl.device import Device, scan
from jkctl.errors import JkError
from jkctl.registers import Catalog

# devicectl-core is as often the checkout next door as it is an installed
# wheel, and a checkout can simply be older than this one.  `pyproject.toml`
# asks for `devicectl-core>=0.2.0`, but uv does not re-check that against a
# path source it has already locked, so the mismatch survives `uv sync` and
# surfaces when somebody picks a serial port -- as an AttributeError naming a
# method they have never heard of.  Said here instead, once, at the start.
# The capability rather than the version string, because that is the thing
# actually needed.
if not hasattr(Worker, "discard_pending"):  # pragma: no cover - a mismatched pair
    from devicectl import __version__ as _core_version

    raise JkError(
        f"the devicectl-core beside this jkctl is {_core_version}, which is "
        "too old: this needs 0.2.0 or newer.  Update that checkout (or "
        "`uv sync` against a released one) and start jkctl again."
    )

# Link states, as published to the UI.  The shared worker publishes whatever
# string it is given; these are jkctl's words for them, and the page's
# stylesheet is written against them.
LINK_RELEASED = Worker.RELEASED  # the port is not held; anything else may use it
LINK_OPENING = Worker.OPENING  # opening the port and settling the adapter
LINK_IDLE = Worker.IDLE  # the port is held, nothing in flight
LINK_BUSY = Worker.BUSY  # a task is using the bus right now
LINK_FLASHING = "flashing"  # a firmware transfer owns the port outright
LINK_ERROR = Worker.ERROR  # the last attempt failed; nothing is held

# How long a request thread waits for its turn.  Shorter than a network
# program would allow: a Modbus round trip is milliseconds, so a queue that
# has not moved in two minutes is stuck rather than slow.
DEFAULT_TASK_TIMEOUT_S = 120.0

# How many addresses a sweep tries when the target named none.
BUS_ADDRESSES = 16


class WorkerBusyError(JkError):
    """A task waited for its turn longer than the caller allowed.

    ``status`` is what the web server answers with: not a bad request --
    the request was fine -- but a conflict with whatever holds the bus.
    """

    status = HTTP_CONFLICT


class NoPortError(JkError):
    """No serial port has been chosen yet."""


@dataclass
class Target:
    """Which port to open, at what speed, and which units to expect on it."""

    port: str
    baud: int = M.DEFAULT_BAUD
    timeout: float = M.QUERY_TIMEOUT
    retries: int = 2
    addr_offset: int = M.FRAME_ADDR_OFFSET
    slaves: tuple[int, ...] = ()
    """The addresses named up front.  Empty means "whatever the scan finds"."""
    label: str = ""

    @property
    def name(self) -> str:
        """A human label for this bus."""
        return self.label or self.port


@dataclass
class Unit:
    """One board found on the bus, as the UI knows it."""

    slave: int
    model: str = ""
    version: str = ""
    serial: str = ""
    hardware: str = ""

    def json(self) -> dict[str, Any]:
        """Render the unit for the header's picker and the event stream."""
        return {
            "id": self.slave,
            "model": self.model,
            "version": self.version,
            "serial": self.serial,
            "hardware": self.hardware,
        }


class BusWorker(Worker[M.Bus]):
    """Owns the serial port and runs everything that touches it, one at a time."""

    thread_name = "jkctl-bus"
    task_timeout_s = DEFAULT_TASK_TIMEOUT_S
    poll_task_name = "refresh"

    def __init__(
        self,
        broadcaster: Broadcaster,
        target: Target | None = None,
        *,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        open_bus: Callable[[Target], M.Bus] | None = None,
    ) -> None:
        """Start the worker thread; it opens nothing until it is asked to."""
        super().__init__(
            broadcaster, poll_interval=poll_interval, idle_timeout=idle_timeout
        )
        self.target = target
        self.catalog = Catalog()
        self.trace = Recorder()
        """The serial recorder, off until the page turns it on.

        It belongs to the worker rather than to the bus because the bus
        comes and goes -- the port is handed back after a few seconds idle
        and opened again on the next read -- and a recording that stopped
        every time the port closed would record only the thing that was
        already working.
        """
        self._turnaround: float | None = None
        """How long this board wants between frames, as the last bus found out."""
        self._open_bus = open_bus or _open_serial
        self._devices: dict[int, Device] = {}
        self._units: list[Unit] = []
        self.set_poll_fn(_poll)
        self.start()

    # --- what the shared worker asks of a bus -------------------------------------------

    def _check_ready(self) -> None:
        """Refuse a task when no port has been chosen yet."""
        if self.target is None:
            raise NoPortError("no serial port has been chosen yet")

    def _busy_error(self, name: str, timeout: float) -> BaseException:
        """Say what is in front of the caller in the queue."""
        return WorkerBusyError(
            f"the bus is still busy after {timeout:g}s; "
            f"{self._op or 'something'} is in front of this"
        )

    def _busy_state(self, task: Task) -> str:
        """Say when the port has stopped speaking Modbus altogether.

        A flash is not a busy bus: after the arming write the wire is raw
        XMODEM until the transfer ends, so the page shows it as its own
        state rather than as one more queued read.
        """
        return LINK_FLASHING if task.exclusive else LINK_BUSY

    def _opening_note(self) -> str:
        """Name the port while it is being opened."""
        return self.target.port if self.target else "opening"

    def _open_link(self) -> M.Bus:
        """Open the serial port this worker is aimed at."""
        target = self.target
        if target is None:  # pragma: no cover - _check_ready ran first
            raise NoPortError("no serial port has been chosen yet")
        bus = self._open_bus(target)
        # Whatever the last bus learned about how long this board wants
        # between frames, carried across the port being released and
        # reopened -- which this worker does every time the page is left
        # alone for a minute.  Without it every wake-up relearns the same
        # thing at the cost of a timeout apiece.
        if self._turnaround is not None:
            bus.turnaround = self._turnaround
        # Attached here rather than passed to the constructor so that an
        # injected bus -- which is how the tests and the simulator drive
        # this -- is recorded too, and so that turning the recorder on does
        # not have to close and reopen the port to take effect.
        bus.log = self.trace.hook(lambda: self._op)
        bus.note = self.trace.say
        self.trace.say(f"opened {target.port} at {target.baud} baud")
        self._devices = {}
        return bus

    def _close_link(self, link: M.Bus) -> None:
        """Give the port back, and forget the devices addressed over it."""
        self._turnaround = link.turnaround
        self.trace.say("closed the port")
        try:
            link.close()
        except Exception:  # noqa: BLE001 - a port that is already gone
            pass
        self._devices = {}

    def _invoke(self, task: Task, link: M.Bus) -> Any:
        """Hand a task the worker rather than the bus.

        What a task usually wants is :meth:`device` for one address, which
        needs the catalog and the per-address cache as well as the wire.

        A failure is written into the recording on the way past.  The frames
        say what was asked and what came back; only this says which of them
        was the one the page raised a toast about, and in the program's own
        words rather than as a silence.
        """
        try:
            if task.job is not None:
                return task.fn(self, task.job)
            return task.fn(self)
        except BaseException as exc:
            self.trace.say(f"{task.name} failed: {exc}")
            raise

    @property
    def turnaround(self) -> float:
        """How long the bus is leaving between frames, open or not."""
        link = self._link
        if isinstance(link, M.Bus):
            return link.turnaround
        if self._turnaround is not None:
            return self._turnaround
        return M.TURNAROUND_START_S

    def _details(self) -> dict[str, Any]:
        """Name the port and the boards on it, for the header."""
        with self._lock:
            units = [u.json() for u in self._units]
        return {
            "port": self.target.port if self.target else None,
            "baud": self.target.baud if self.target else None,
            "units": units,
            # On the link rather than behind an endpoint of its own: it is a
            # property of the port, it changes while nobody is asking, and
            # every browser has to agree about it -- two tabs with one
            # recording between them.
            "trace": self.trace.state(),
        }

    # --- the bus itself -----------------------------------------------------------------

    def device(self, slave: int) -> Device:
        """Return the :class:`~jkctl.device.Device` for one address on this bus.

        Only ever called from the worker thread, from inside a task.
        """
        bus = self._require_link()
        if slave not in self._devices:
            self._devices[slave] = Device(bus, slave, self.catalog)
        return self._devices[slave]

    @property
    def bus(self) -> M.Bus:
        """The open bus.  Only valid on the worker thread, inside a task."""
        return self._require_link()

    def units(self) -> list[Unit]:
        """Return the units the last scan found, in address order."""
        with self._lock:
            return list(self._units)

    def rescan(self) -> list[Unit]:
        """Sweep the bus for boards and remember what answered.

        Runs on the worker thread.  When the target named its addresses this
        only reads their nameplates; otherwise it sweeps all sixteen, which
        is what somebody who has just plugged in an adapter wants.
        """
        bus = self._require_link()
        wanted = self.target.slaves if self.target else ()
        found: list[Unit] = []
        if wanted:
            for slave in wanted:
                device = self.device(slave)
                try:
                    ident = I.read(device)
                except JkError:
                    continue
                if ident.model:
                    found.append(_unit_of(slave, ident))
        else:
            for slave, device in scan(bus, range(BUS_ADDRESSES), self.catalog):
                self._devices[slave] = device
                try:
                    ident = I.read(device)
                except JkError:
                    # It answered the sweep, so it is on the bus; only its
                    # nameplate failed to come back.  Keeping it beats losing
                    # it: the page has a name for a board in that state
                    # ("nameplate unreadable") and every other read still
                    # works, whereas dropping it would leave the picker
                    # saying nothing answered on a bus that had just answered.
                    ident = I.from_fields({})
                found.append(_unit_of(slave, ident))
        with self._lock:
            self._units = found
        self.events.publish("units", {"units": [u.json() for u in found]}, sticky=True)
        return found

    def set_target(self, target: Target | None) -> None:
        """Point the worker at a different port, closing whatever is open.

        Whatever was queued for the old port is dropped first.  A port
        chosen by mistake is the ordinary way this is reached -- a sweep of
        sixteen addresses that answers nowhere takes seconds, and a page
        that asked for a few of them while waiting would otherwise spend the
        first minute on the right port working through the wrong one.
        """
        self.discard_pending("the port was changed before this ran")
        self.run_soon(lambda worker: worker._close("switching port"))
        with self._lock:
            self.target = target
            self._units = []
            self._devices = {}
        self.events.publish("units", {"units": []}, sticky=True)
        self._publish_link()

    def connect(self) -> None:
        """Open the port now, so the pill turns green before anything is asked."""
        self.run_soon(lambda worker: worker._require_link() and None)

    # --- the names the API layer uses ---------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Everything the header needs to draw itself."""
        return self.link_state()

    def set_live(self, live: bool, interval: float | None = None) -> None:
        """Turn the shared live refresh on or off, and set its beat."""
        self.set_poll(live=live, interval=interval)

    def notify_progress(
        self, job: Job, fraction: float | None, detail: str = ""
    ) -> None:
        """Record a job's progress, rate-limited on the way to the browsers."""
        job.report(fraction, detail or None)


def _poll(worker: BusWorker) -> None:
    """One live refresh of every known unit."""
    from jkctl.web import api

    api.publish_live(worker)


def _unit_of(slave: int, ident: I.Identity) -> Unit:
    """Turn a nameplate read into the small record the UI keeps per board."""
    return Unit(
        slave=slave,
        model=ident.model,
        version=ident.software_version,
        serial=ident.serial_number,
        hardware=ident.hardware_version,
    )


def _open_serial(target: Target) -> M.Bus:
    """Open the real serial port a target names."""
    return M.Bus(
        target.port,
        target.baud,
        timeout=target.timeout,
        addr_offset=target.addr_offset,
        retries=target.retries,
    )


__all__ = [
    "ACTIVITY_HISTORY",
    "DEFAULT_IDLE_TIMEOUT_S",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_TASK_TIMEOUT_S",
    "JOB_NOTIFY_INTERVAL_S",
    "JOB_RETENTION_S",
    "LINK_BUSY",
    "LINK_RELEASED",
    "LINK_ERROR",
    "LINK_FLASHING",
    "LINK_IDLE",
    "LINK_OPENING",
    "LOOP_TICK_S",
    "MAX_POLL_INTERVAL_S",
    "MIN_POLL_INTERVAL_S",
    "STOP_JOIN_S",
    "BusWorker",
    "Job",
    "NoPortError",
    "Target",
    "Unit",
    "WorkerBusyError",
]
