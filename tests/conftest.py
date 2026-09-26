"""Shared fixtures: an in-memory BMS the whole tool can be driven against.

The simulator in :mod:`jkctl.simulator` already models the device faithfully --
byte addressing, big-endian numbers, the FC03 quantity cap, the refusal of odd
offsets and of reads past a table's end.  So rather than writing a second,
poorer model for the tests, the two ends are wired together in memory: a
:class:`jkctl.modbus.Bus` writes into a :class:`~jkctl.simulator.Sim`, which
writes its answers back into the buffer the bus reads from.

That means a CLI test exercises the real framing, the real chunking and the
real decode, and nothing here needs a serial port.
"""

from __future__ import annotations

import pytest

from jkctl.modbus import Bus
from jkctl.registers import Catalog
from jkctl.simulator import Sim


class SimEnd:
    """The simulator's end of the pair: everything it writes goes to the master."""

    def __init__(self, master: "MasterEnd"):
        self.master = master

    def write(self, data: bytes) -> None:
        """Hand bytes to the master's receive buffer."""
        self.master.rx += bytes(data)

    def flush(self) -> None:
        """Nothing is buffered in memory."""

    def read(self, n: int = 1) -> bytes:
        """The simulator is driven by feed(), never by its own read loop."""
        return b""


class MasterEnd:
    """The master's end of the pair: pyserial's shape, backed by a Sim."""

    def __init__(self):
        self.rx = bytearray()
        self.sim: Sim | None = None
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        """Send to the simulator, which answers synchronously."""
        self.sent.append(bytes(data))
        assert self.sim is not None
        self.sim.feed(bytes(data))

    def flush(self) -> None:
        """Nothing is buffered in memory."""

    @property
    def in_waiting(self) -> int:
        """How many answer bytes are waiting."""
        return len(self.rx)

    def read(self, n: int = 1) -> bytes:
        """Take up to ``n`` answer bytes."""
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def reset_input_buffer(self) -> None:
        """Drop anything unread."""
        self.rx.clear()

    def reset_output_buffer(self) -> None:
        """Nothing is buffered in memory."""

    def close(self) -> None:
        """Nothing to close."""


def make_pair(slave: int = 1, **sim_kwargs):
    """Return ``(bus, sim, link)`` wired to each other in memory."""
    link = MasterEnd()
    sim = Sim(
        port=None, slave=slave, link=SimEnd(link), log=lambda msg: None, **sim_kwargs
    )
    link.sim = sim
    # The simulator answers synchronously, so a short timeout only shortens
    # the waits on addresses that will never answer.
    bus = Bus(link=link, timeout=0.02, retries=0)
    return bus, sim, link


@pytest.fixture
def catalog() -> Catalog:
    """One shared register catalog; building it parses the datasource."""
    return Catalog()


@pytest.fixture
def pair():
    """A bus wired to a simulated BMS at address 1."""
    bus, sim, link = make_pair()
    yield bus, sim, link
    bus.close()


@pytest.fixture
def bus(pair):
    """Just the bus half of :func:`pair`."""
    return pair[0]


@pytest.fixture
def sim(pair):
    """Just the simulator half of :func:`pair`."""
    return pair[1]


@pytest.fixture
def device(pair, catalog):
    """A :class:`jkctl.device.Device` bound to the simulated BMS."""
    from jkctl.device import Device

    return Device(pair[0], 1, catalog)


class BankEnd(MasterEnd):
    """A master's end backed by several simulators, one per address.

    Every simulator sees every frame and only the addressed one answers, which
    is what a real RS485 bus does and what makes ``--id all`` worth testing:
    a scan of sixteen addresses has to find exactly the boards that are there.
    """

    def __init__(self):
        super().__init__()
        self.sims: list[Sim] = []

    def write(self, data: bytes) -> None:
        """Send to every simulator; the addressed one answers."""
        self.sent.append(bytes(data))
        for sim in self.sims:
            sim.feed(bytes(data))


def make_bank(slaves=(1, 2), **sim_kwargs):
    """Return ``(bus, sims, link)`` for several simulated units on one bus."""
    link = BankEnd()
    sims = [
        Sim(port=None, slave=s, link=SimEnd(link), log=lambda msg: None, **sim_kwargs)
        for s in slaves
    ]
    link.sims = sims
    bus = Bus(link=link, timeout=0.02, retries=0)
    return bus, sims, link


@pytest.fixture
def cli_bank(monkeypatch, catalog):
    """Make every ``cli.main([...])`` call reach a two-unit simulated bank."""
    from jkctl.device import Device

    bus, sims, link = make_bank((1, 2))

    def _open_bus(args):
        from jkctl.cli.target import resolve_target
        from jkctl.config import load_config

        args.target = resolve_target(args, load_config(args.config))
        return bus, args.target

    def _open_device(args):
        _open_bus(args)
        wanted = args.target.slaves or (1, 2)
        args.devices = [Device(bus, s, catalog) for s in wanted]
        return bus, args.devices[0]

    import sys

    main_module = sys.modules["jkctl.cli.main"]
    monkeypatch.setattr(main_module, "open_bus", _open_bus)
    monkeypatch.setattr(main_module, "open_device", _open_device)
    monkeypatch.setattr(type(bus), "close", lambda self: None)
    return bus, sims


@pytest.fixture
def cli_device(monkeypatch, pair, catalog):
    """Make every ``cli.main([...])`` call reach the simulated BMS.

    The CLI opens its own bus from the resolved target; this replaces that one
    step, so the tests exercise the whole path from argv down to the framing.
    """
    from jkctl.device import Device

    bus, sim, link = pair
    device = Device(bus, 1, catalog)

    def _open_bus(args):
        from jkctl.cli.target import resolve_target
        from jkctl.config import load_config

        args.target = resolve_target(args, load_config(args.config))
        return bus, args.target

    def _open_device(args):
        _open_bus(args)
        # open_device fills this in for the fan-out reads; the fixture stands
        # in for it, so it has to fill it in too.
        args.devices = [device]
        return bus, device

    # The module has to be fetched from sys.modules: `jkctl.cli` re-exports
    # `main`, so both the attribute and the dotted path resolve to the
    # function rather than to the module it lives in.
    import sys

    main_module = sys.modules["jkctl.cli.main"]
    monkeypatch.setattr(main_module, "open_bus", _open_bus)
    monkeypatch.setattr(main_module, "open_device", _open_device)
    # The bus must survive the `with` block main() puts it in, because one
    # test may run several commands against the same simulated unit.
    monkeypatch.setattr(type(bus), "close", lambda self: None)
    return device
