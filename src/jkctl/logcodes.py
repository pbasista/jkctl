"""What the codes in a stored fault record mean.

A JK board keeps its own history: ``detailLogsCount`` in the runtime table
says how many records it holds, and the datasource's table 06 describes each
one -- an RTC timestamp, a code, the switch positions, and a snapshot of the
whole pack at the moment it tripped.  The code is a number, and the number on
its own is useless.

The names are the vendor application's own.  ``jk-bms-monitor.exe``
``FUN_140008870`` builds a map from code to display string, one
``QMetaObject::tr`` per entry; :data:`_TABLE` is that map, extracted rather
than translated, along with the two loops at the end of it that name the
per-cell protections (codes 100-131 and 200-231, one per cell).  See
``research/windows/windows-findings.md`` §36.

Having the names is not the same as being able to read the records: which
register window serves table 06, if any, is still unknown (§37).  This module
is what turns them into sentences once they can be read, and it is useful on
its own -- the codes and the ``sysAlarm`` bits name the same events, so this
is also the vendor's own wording for what a protection is called.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logcodes.json")


@lru_cache(maxsize=1)
def _table() -> tuple[dict[int, str], tuple[dict, ...]]:
    """Load the extracted table once."""
    with open(_PATH, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    codes = {int(k): v for k, v in doc["codes"].items()}
    return codes, tuple(doc.get("ranges", ()))


def name(code: int) -> str:
    """Return what the vendor's application calls this log code.

    A code neither the fixed table nor the per-cell ranges cover comes back as
    ``code 57`` rather than as a guess: the map was extracted from one version
    of one application, and a firmware that adds an event is not something to
    invent a name for.
    """
    codes, ranges = _table()
    if code in codes:
        return codes[code]
    for span in ranges:
        if span["from"] <= code <= span["to"]:
            return span["template"].format(n=code - span["subtract"])
    return f"code {code}"


def known(code: int) -> bool:
    """Whether this code has a name, as opposed to a placeholder."""
    codes, ranges = _table()
    return code in codes or any(s["from"] <= code <= s["to"] for s in ranges)


def all_names() -> dict[int, str]:
    """Every code the table names, the per-cell ranges expanded."""
    codes, ranges = _table()
    out = dict(codes)
    for span in ranges:
        for code in range(span["from"], span["to"] + 1):
            out[code] = span["template"].format(n=code - span["subtract"])
    return dict(sorted(out.items()))


__all__ = ["all_names", "known", "name"]
