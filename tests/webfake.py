"""A simulated bus the web tests can point a :class:`BusWorker` at.

The same in-memory pairing ``conftest`` uses for the CLI tests, wrapped so it
can be handed to the worker as its ``open_bus``: the server then exercises the
real framing, the real chunking and the real decode against as many simulated
boards as a test asks for.
"""

from __future__ import annotations

from conftest import BankEnd, SimEnd

from jkctl.modbus import Bus
from jkctl.simulator import Sim


class FakeBus:
    """A factory that hands the worker a bus backed by simulated units."""

    def __init__(self, slaves=(1, 2)) -> None:
        """Build one simulator per address, all on one in-memory wire."""
        self.slaves = tuple(slaves)
        self.link = BankEnd()
        self.sims = [
            Sim(port=None, slave=s, link=SimEnd(self.link), log=lambda msg: None)
            for s in self.slaves
        ]
        self.link.sims = self.sims
        self.opened = 0
        self.closed = 0

    def __call__(self, target) -> Bus:
        """Open a bus for the worker; ``target`` is accepted and ignored."""
        self.opened += 1
        bus = Bus(link=self.link, timeout=0.02, retries=0)
        fake = self

        def close(self) -> None:
            fake.closed += 1

        bus.close = close.__get__(bus)  # ty: ignore[invalid-assignment]
        return bus

    def sim(self, slave: int) -> Sim:
        """The simulator answering at one address."""
        return self.sims[self.slaves.index(slave)]
