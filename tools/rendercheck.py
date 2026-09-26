#!/usr/bin/env python3
"""Draw jkctl's UI in a real browser and check the layout survives.

The checks themselves are :mod:`devicectl.devtools.rendercheck`, shared with
the other programs built on the same frontend.  What is here is jkctl's half:
the page comes from ``jkctl ui --simulate``, so it needs no hardware, and the
tabs are the ones a bank of three simulated boards has.

    uv sync --group browser                        # once: playwright
    uv run python -m playwright install chromium   # once: the browser
    uv run tools/rendercheck.py
    uv run tools/rendercheck.py --width 1440
"""

from __future__ import annotations

import os
import sys

from devicectl.devtools import rendercheck

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from screenshot import ROOT, TABS, serve_simulated


def main(argv: list[str] | None = None) -> int:
    """Run the shared checks over a simulated bank."""
    return rendercheck.main(
        lambda port: serve_simulated(port, boards=3),
        TABS,
        program="jkctl",
        argv=argv,
    )


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
