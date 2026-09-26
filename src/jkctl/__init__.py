"""jkctl -- a cross-platform command-line tool for JK BMS battery management systems.

Everything here was derived from JK BMS Monitor 3.11.0 (the vendor's Windows
application), the encrypted protocol datasource shipped with it, and JK's own
RS485 Modbus register-map documents.  The reverse-engineering record lives in
``research/windows/windows-findings.md``; ``docs/how-it-works.md`` is the
readable summary.

The package is one module per subject, and none of them prints:

* :mod:`jkctl.modbus` -- the Modbus RTU link and the JK register bases;
* :mod:`jkctl.protocol` -- the frame/field layout, from the datasource;
* :mod:`jkctl.registers` -- the catalog: which field lives where, and whether
  it may be written;
* :mod:`jkctl.values` -- turning typed input into wire values and back;
* :mod:`jkctl.device` -- one BMS on the bus, read and written by field name;
* :mod:`jkctl.identity` / :mod:`jkctl.runtime` / :mod:`jkctl.settings` --
  a snapshot of each of the three readable tables;
* :mod:`jkctl.controls` -- the write-only action registers;
* :mod:`jkctl.firmware` / :mod:`jkctl.upgrade` -- the ``.jkbms`` container and
  the XMODEM transfer that flashes it;
* :mod:`jkctl.aes` -- AES-256-CBC with no package to install;
* :mod:`jkctl.probe` -- read-only bus reconnaissance;
* :mod:`jkctl.simulator` -- a fake BMS on a serial port, for testing;
* :mod:`jkctl.config` -- the optional TOML settings file.

What is not specific to a BMS lives in ``devicectl-core`` and is shared with
the other programs of this shape: where slow work reports to
(:mod:`devicectl.report`, :mod:`devicectl.progress`), the subcommand table
(:mod:`devicectl.cli.command`), the event broadcaster
(:mod:`devicectl.web.events`) and the HTTP primitives
(:mod:`devicectl.web.http`).

Printing, prompting and exit codes belong to :mod:`jkctl.cli` alone.
"""

from __future__ import annotations

__version__ = "0.1.0"

from jkctl.device import Device
from jkctl.errors import JkError
from jkctl.firmware import Firmware, FirmwareError
from jkctl.modbus import Bus, ModbusError
from jkctl.protocol import Protocol
from jkctl.registers import Action, Catalog, Register
from jkctl.upgrade import UpgradeError
from jkctl.values import RegisterValueError

__all__ = [
    "Action",
    "Bus",
    "Catalog",
    "Device",
    "Firmware",
    "FirmwareError",
    "JkError",
    "ModbusError",
    "Protocol",
    "Register",
    "RegisterValueError",
    "UpgradeError",
    "__version__",
]
