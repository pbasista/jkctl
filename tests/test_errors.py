"""Every deliberate failure must be one class, so main() needs one handler."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import jkctl
from jkctl.errors import JkError


def _declared_errors() -> list[type]:
    """Return every ``*Error`` class this package defines itself."""
    found = []
    for module in pkgutil.walk_packages(jkctl.__path__, "jkctl."):
        # __main__ runs the CLI on import; walking into it would run jkctl
        # with pytest's own argv.
        if module.name.endswith(".__main__"):
            continue
        imported = importlib.import_module(module.name)
        for name, obj in vars(imported).items():
            if (
                inspect.isclass(obj)
                and name.endswith("Error")
                and obj.__module__ == module.name
            ):
                found.append(obj)
    return found


def test_the_package_declares_some_errors_to_check():
    assert len(_declared_errors()) >= 5


def test_every_error_class_derives_from_jk_error():
    stray = sorted(c.__name__ for c in _declared_errors() if not issubclass(c, JkError))
    assert stray == []


def test_a_value_error_is_also_a_value_error():
    from jkctl.values import RegisterValueError

    assert issubclass(RegisterValueError, ValueError)


def test_only_the_board_and_the_wire_start_a_recording():
    # A traceable failure is one a serial trace can explain; the page starts
    # recording on one of these and on nothing else.
    traceable = {
        cls.__name__ for cls in _declared_errors() if getattr(cls, "traceable", False)
    }
    assert traceable == {"ModbusError", "DeviceError"}
