"""The worker that owns the serial port: its states, its queue, its jobs."""

from __future__ import annotations

import threading
import time

import pytest
from devicectl.errors import DeviceError
from devicectl.web.events import Broadcaster
from webfake import FakeBus

from jkctl.errors import JkError
from jkctl.web.session import (
    LINK_IDLE,
    LINK_RELEASED,
    BusWorker,
    NoPortError,
    Target,
)


def wait_for(predicate, timeout=3.0):
    """Spin until a predicate holds, or fail the test."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def worker():
    """A worker wired to two simulated units, with nothing open yet."""
    fake = FakeBus((1, 2))
    events = Broadcaster()
    made = BusWorker(events, Target(port="fake"), open_bus=fake, idle_timeout=0.2)
    made.fake = fake  # type: ignore[attr-defined]
    made.broadcaster = events  # type: ignore[attr-defined]
    yield made
    made.stop()


def test_nothing_is_opened_until_something_is_asked(worker):
    assert worker.state()["state"] == LINK_RELEASED
    assert worker.fake.opened == 0


def test_a_read_opens_the_port_and_finds_both_units(worker):
    units = worker.run("scan", lambda w: w.rescan())
    assert [u.slave for u in units] == [1, 2]
    assert worker.fake.opened == 1
    assert worker.state()["state"] == LINK_IDLE


def test_a_board_that_answers_but_will_not_say_its_name_is_still_listed(
    worker, monkeypatch
):
    """A nameplate that does not come back must not lose the board with it.

    The board answered the sweep, so it is on the bus.  Dropping it would
    leave the picker saying nothing answered on a port that had just
    answered -- and would send the user back to try another adapter.
    """
    from jkctl import identity as I

    real = I.read

    def refuse(device):
        if device.slave == 2:
            raise JkError("address 2: no/short response")
        return real(device)

    monkeypatch.setattr("jkctl.web.session.I.read", refuse)
    units = worker.run("scan", lambda w: w.rescan())
    assert [u.slave for u in units] == [1, 2]
    assert units[0].model and not units[1].model


def test_the_port_is_given_back_when_nothing_is_using_it(worker):
    worker.run("scan", lambda w: w.rescan())
    assert wait_for(lambda: worker.state()["state"] == LINK_RELEASED)
    assert worker.fake.closed == 1


def test_with_no_port_a_read_says_so(worker):
    worker.set_target(None)
    with pytest.raises(NoPortError):
        worker.run("scan", lambda w: w.rescan())


def test_the_link_state_reaches_a_subscriber(worker):
    with worker.broadcaster.subscribe() as stream:
        worker.run("scan", lambda w: w.rescan())
        names = []
        for _ in range(6):
            event = stream.get(0.4)
            if event is None:
                break
            names.append(event.name)
    assert "link" in names


def test_a_job_reports_progress_and_finishes(worker):
    def work(w, job):
        w.notify_progress(job, 0.5, "halfway")
        return {"flashed": 1}

    job = worker.start_job("a slow thing", work)
    assert wait_for(lambda: job.state == "done")
    assert job.result == {"flashed": 1}
    assert job.progress == 1.0
    assert job.message == "halfway"


def test_a_job_that_fails_carries_its_reason(worker):
    def work(w, job):
        raise ValueError("no")

    job = worker.start_job("a doomed thing", work)
    assert wait_for(lambda: job.state == "failed")
    assert job.error == "no"


def test_the_activity_ticker_remembers_what_ran(worker):
    worker.run("scanning the bus", lambda w: w.rescan())
    assert worker.state()["activity"][0]["op"] == "scanning the bus"


# --- the in-memory bank --------------------------------------------------
#
# `jkctl ui --simulate` is the only way most people will see the whole page,
# and it is what the screenshots and `tools/rendercheck.py` drive, so a
# regression in it breaks the documentation and the render checks at once.


def test_a_simulated_bank_answers_for_every_address_it_was_given():
    """Each simulated board answers on its own address, and no other does."""
    from jkctl import identity as I, simulator
    from jkctl.device import Device
    from jkctl.modbus import Bus
    from jkctl.registers import Catalog

    link = simulator.bank((1, 2, 5))
    bus = Bus(link=link, timeout=0.05, retries=0)
    catalog = Catalog()
    for slave in (1, 2, 5):
        ident = I.read(Device(bus, slave, catalog))
        assert ident.model, f"unit {slave} did not name itself"
    with pytest.raises(JkError):
        I.read(Device(bus, 3, catalog))


def test_a_simulated_bank_keeps_what_was_written_to_it():
    """A demonstration whose settings reset on every reconnect is a poor one."""
    from jkctl import settings as S, simulator
    from jkctl.device import Device
    from jkctl.modbus import Bus
    from jkctl.registers import Catalog

    link = simulator.bank((1,))
    catalog = Catalog()
    device = Device(Bus(link=link, timeout=0.05, retries=0), 1, catalog)
    before = S.read(device, S.SETTINGS)["volCellUV"]
    changes = S.plan(device, {"volCellUV": round(before + 0.05, 3)})
    S.apply(device, changes)
    # A fresh Bus over the same bank is what the worker builds when it
    # reconnects after an idle release.
    again = Device(Bus(link=link, timeout=0.05, retries=0), 1, catalog)
    assert S.read(again, S.SETTINGS)["volCellUV"] == pytest.approx(before + 0.05)


def test_changing_the_port_drops_what_was_queued_for_the_old_one(worker):
    """The wrong port must not cost the right one a queue of failures.

    Choosing a port the battery is not on is the ordinary way a first run
    goes, and a sweep of sixteen addresses that answers nowhere takes
    seconds.  Whatever the page asked for while that was happening was
    asked about the old port; running it against the new one would answer
    the wrong question, and leaving it to time out means the first two
    minutes on the right port are spent reporting the wrong one.
    """
    holding = threading.Event()
    let_go = threading.Event()

    def block(_worker):
        holding.set()
        let_go.wait(5.0)

    worker.run_soon(block)
    assert wait_for(holding.is_set)

    outcomes: list[object] = []

    def ask() -> None:
        try:
            outcomes.append(worker.run("reading the bank", lambda w: "read"))
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            outcomes.append(exc)

    waiting = [threading.Thread(target=ask) for _ in range(3)]
    for thread in waiting:
        thread.start()
    assert wait_for(lambda: worker.state()["queued"] == 3)

    worker.set_target(Target(port="another"))
    for thread in waiting:
        thread.join(5.0)
    let_go.set()

    assert len(outcomes) == 3
    assert all(isinstance(out, DeviceError) for out in outcomes), outcomes
    assert "the port was changed" in str(outcomes[0])


def test_the_task_already_running_is_left_alone(worker):
    """It holds the port, and no thread can be taken off a blocked read."""
    holding = threading.Event()
    let_go = threading.Event()
    finished = threading.Event()

    def block(_worker):
        holding.set()
        let_go.wait(5.0)
        finished.set()

    worker.run_soon(block)
    assert wait_for(holding.is_set)
    assert worker.discard_pending("the port was changed") == 0
    let_go.set()
    assert wait_for(finished.is_set)
