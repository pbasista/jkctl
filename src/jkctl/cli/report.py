"""A firmware transfer's progress on a terminal.

The scaffolding -- the one live stderr line, the decision not to draw it
into a pipe, closing it off when a failure lands on top of it -- is
:class:`devicectl.cli.report.TerminalReporter`.  What is here is what a JK
flash counts: 128-byte XMODEM blocks, several hundred of them, each waiting
for its own ACK.  It is the only thing in this program slow enough to need
showing.
"""

from __future__ import annotations

import time

from devicectl.cli.report import TerminalReporter as _Terminal
from devicectl.progress import PROGRESS_MIN_INTERVAL_S, bar, fmt_duration

# Without a live line, log one line every this many blocks instead.
BLOCK_LOG_INTERVAL = 64

# Percentages, for the plain lines.
PERCENT = 100.0


class TerminalReporter(_Terminal):
    """Report a firmware transfer's progress to the terminal.

    Use it as a context manager: leaving the block closes off a live line that
    a failure would otherwise have left half-drawn, with the error message
    landing on top of it.
    """

    def __init__(self, *, trace: bool = False) -> None:
        """Report to stderr, drawing a live line unless ``trace`` or a pipe."""
        super().__init__(quiet=trace)
        self.trace = trace
        self._last_logged = -1

    def sending(self, sent: int, total: int, elapsed_s: float, label: str = "") -> None:
        """Redraw the block bar, at most every PROGRESS_MIN_INTERVAL_S.

        A flash is the only transfer jkctl makes, so ``label`` is never given
        and the bar says what it has always said.
        """
        if not self.live:
            # The last block is reported twice -- once when it goes out and
            # once when its ACK arrives -- so only the first of the two lands.
            if sent == self._last_logged:
                return
            if sent == total or sent % BLOCK_LOG_INTERVAL == 0:
                self._last_logged = sent
                print(f"  block {sent}/{total}  {PERCENT * sent / total:5.1f}%")
            return
        now = time.monotonic()
        if sent < total and now - self._last_draw < PROGRESS_MIN_INTERVAL_S:
            return  # throttle mid-stream redraws, but always draw the final 100%
        self._last_draw = now
        frac = sent / total if total else 1.0
        eta = elapsed_s * (total - sent) / sent if sent else 0.0
        self._draw(
            f"  {label or 'Flashing'}  [{bar(frac)}] {frac:4.0%}  block {sent}/{total}  "
            f"elapsed {fmt_duration(elapsed_s)}  eta {fmt_duration(eta)}"
        )


__all__ = ["TerminalReporter"]
