"""Hold the four serial stand-ins to the shape of a serial port.

Nothing here reaches a port: the simulated bank, the scripted pair and the
``jkctl ui --simulate`` loopback all hand :class:`~jkctl.modbus.Bus` an
object of pyserial's shape instead.  Which shape that was lived in a
docstring -- "anything with ``write``/``read``/``in_waiting``/..." -- and
four classes in two directories kept to it by hand.

It is :class:`~jkctl.modbus.SerialLink` now, and pyserial is checked
against it too: a contract the real thing does not meet is a contract that
has drifted, however happily the fakes pass.
"""

from __future__ import annotations

import serial
from conftest import BankEnd, MasterEnd, SimEnd
from devicectl.testing import assert_stands_in_for

from jkctl.modbus import SerialLink, Wire
from jkctl.simulator import Loopback, SimLink


def test_pyserial_is_what_the_protocol_describes():
    """The point of the shape, and the only one of these that is real."""
    assert_stands_in_for(SerialLink, serial.Serial)


def test_the_shipped_loopback_is_a_port():
    """`jkctl ui --simulate` puts a whole bus behind this one."""
    assert_stands_in_for(SerialLink, Loopback)


def test_the_scripted_pairs_master_end_is_a_port():
    """The CLI tests' bus talks to this."""
    assert_stands_in_for(SerialLink, MasterEnd)
    assert_stands_in_for(SerialLink, BankEnd)


def test_a_simulators_own_end_is_enough_wire_to_answer_on():
    """A board writes its reply and is fed frames, so it never reads."""
    assert_stands_in_for(Wire, SimLink)
    assert_stands_in_for(Wire, SimEnd)
