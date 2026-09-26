#!/usr/bin/env python3
"""Photograph the web UI against simulated boards.

The page is what most people meet first, so the README ought to show it, and
a screenshot is also the cheapest way to see what a change did to something
no assertion covers -- a column that pushes its neighbours off-screen, a unit
that wraps under its input, a card that draws a rule under nothing.  Every
one of those got past ``pytest``, Biome, ``frontlint`` and ``htmcheck`` and
was found by looking.

It needs no hardware: ``jkctl ui --simulate`` puts a bank of fake boards on
an in-memory bus, and this drives a real browser against it.

    tools/screenshot.py                     # every tab, to docs/img/
    tools/screenshot.py bank dashboard      # just these
    tools/screenshot.py --theme light --width 1280

Chromium comes from Playwright, which is in the ``browser`` dependency
group rather than in the default dev set or in the wheel:

    uv sync --group browser
    uv run python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "docs", "img")

# Every tab, in the order the page shows them.  A dashboard tab needs a unit,
# and unit 1 is the first board `--simulate` puts on the bus.
TABS = {
    "bank": "bank",
    "dashboard": "1/dashboard",
    "settings": "1/settings",
    "cells": "1/cells",
    "ports": "1/ports",
    "history": "1/history",
    "registers": "1/registers",
    "firmware": "1/firmware",
    "tools": "1/tools",
}

# Long enough for the tab's own reads to land on a bus that answers in
# microseconds.  A tab that is still fetching photographs as a spinner.
SETTLE_S = 2.5


def free_port() -> int:
    """Ask the kernel for a port nothing else is on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def serve_simulated(port: int, boards: int = 3):
    """Run ``jkctl ui --simulate`` on this port for as long as the block lasts.

    Shared with ``tools/rendercheck.py``: both tools want the same page over
    the same fake bank, and only one of them should know how to start it.
    """
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "jkctl",
            "ui",
            "--simulate",
            str(boards),
            "--no-browser",
            "--listen",
            f"127.0.0.1:{port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=ROOT,
    )
    try:
        wait_for(f"http://127.0.0.1:{port}/", server)
        yield server
    finally:
        server.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            server.wait(timeout=5)


def wait_for(url: str, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    """Block until the server answers, or until it gives up and says why."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() if proc.stdout else "") or ""
            raise SystemExit(f"jkctl ui exited before serving:\n{out}")
        with contextlib.suppress(urllib.error.URLError, ConnectionError, OSError):
            urllib.request.urlopen(url, timeout=0.5).read()
            return
        time.sleep(0.1)
    raise SystemExit(f"{url} did not answer within {timeout:.0f}s")


def shoot(args: argparse.Namespace, wanted: list[str]) -> int:
    """Serve simulated boards, drive a browser over them, write the PNGs."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "playwright is not installed; it lives in the browser group:\n"
            "  uv sync --group browser\n"
            "  uv run python -m playwright install chromium   # once\n"
            "  uv run tools/screenshot.py"
        ) from None

    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    problems: list[str] = []
    with serve_simulated(port, boards=args.boards):
        os.makedirs(args.out, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(
                viewport={"width": args.width, "height": args.height},
                device_scale_factor=args.scale,
                color_scheme=args.theme,
            )
            page.on("pageerror", lambda exc: problems.append(f"{page.url}: {exc}"))
            for name in wanted:
                page.goto(url + "#" + TABS[name])
                page.wait_for_timeout(int(SETTLE_S * 1000))
                path = os.path.join(args.out, f"{name}.png")
                page.screenshot(path=path, full_page=args.full_page)
                print(f"  {os.path.relpath(path, ROOT)}")
            browser.close()

    for line in problems:
        print(f"page error: {line}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments and take the pictures."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "tabs", nargs="*", choices=[*TABS, []], help="which tabs (default: all)"
    )
    ap.add_argument("--out", default=OUT, metavar="DIR", help=f"default {OUT}")
    ap.add_argument("--theme", default="dark", choices=("dark", "light"))
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument(
        "--scale", type=float, default=2.0, metavar="N", help="device pixel ratio"
    )
    ap.add_argument(
        "--boards", type=int, default=3, metavar="N", help="how many to simulate"
    )
    ap.add_argument(
        "--viewport-only",
        dest="full_page",
        action="store_false",
        help="crop to the viewport instead of the whole page",
    )
    args = ap.parse_args(argv)
    return shoot(args, list(args.tabs) or list(TABS))


if __name__ == "__main__":
    sys.exit(main())
