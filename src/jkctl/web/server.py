"""Starting the web UI: what jkctl tells the shared server about itself.

The server, its guards, the event stream and the static file handling are
:mod:`devicectl.web.server` -- they are the same in every program of this
shape, and none of them knows what a BMS is.  What is here is the part that
does: the bus worker, the handler context, and the words jkctl prints for
itself.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from devicectl.meta import project_links
from devicectl.web.server import DEFAULT_HOST, Branding, Serving, serve as _serve

from jkctl import __version__
from jkctl.web.api import ROUTES, Context, use_context, watcher
from jkctl.web.session import BusWorker, Target

DEFAULT_PORT = 8087

# The cookie the browser keeps the access token in.  Per app, and it has to
# stay that way: cookies are scoped by host and not by port, so jkctl and
# another of these served on localhost would otherwise clobber each other's.
TOKEN_COOKIE = "jkctl_token"

STATIC_DIR = Path(__file__).with_name("static")

# A .jkbms is under 100 kB, so nothing legitimate comes near this; it is
# only the guard against a body that never ends.
MAX_BODY_BYTES = 32 * 1024 * 1024

BRANDING = Branding(
    name="jkctl",
    version=__version__,
    token_cookie=TOKEN_COOKIE,
    default_port=DEFAULT_PORT,
    static_dir=STATIC_DIR,
    read_only_note="nothing on the battery can be changed from here",
    max_body_bytes=MAX_BODY_BYTES,
    # The project's own URLs, out of `[project.urls]` rather than written
    # down a second time here: the page links its wordmark, its version and
    # its licence line from these.
    links=project_links("jkctl"),
)


def serve(
    *,
    target: Target | None,
    defaults: Target | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    read_only: bool = False,
    open_browser: bool = True,
    poll_interval: float | None = None,
    idle_timeout: float | None = None,
    allow_hosts: tuple[str, ...] = (),
    firmware_dir: str = "",
    debug: bool = False,
    open_bus: Callable[[Target], Any] | None = None,
    note: str = "",
) -> int:
    """Run the web UI until interrupted; returns a process exit code."""
    from devicectl.web.events import Broadcaster

    from jkctl.web.session import DEFAULT_IDLE_TIMEOUT_S, DEFAULT_POLL_INTERVAL_S

    events = Broadcaster()
    worker = BusWorker(
        events,
        target,
        poll_interval=poll_interval or DEFAULT_POLL_INTERVAL_S,
        idle_timeout=idle_timeout or DEFAULT_IDLE_TIMEOUT_S,
        open_bus=open_bus,
    )
    context = Context(
        worker=worker,
        read_only=read_only,
        firmware_dir=firmware_dir,
        defaults=defaults,
    )
    use_context(context)

    notes = [line for line in (note,) if line]
    if target is None:
        notes.append("no serial port chosen yet -- the page opens on a port picker")
    return _serve(
        Serving(
            branding=BRANDING,
            routes=ROUTES,
            context=context,
            events=events,
            worker=worker,
            notes=notes,
            watch=watcher(context),
        ),
        host=host,
        port=port,
        token=token,
        read_only=read_only,
        open_browser=open_browser,
        allow_hosts=allow_hosts,
        debug=debug,
    )


__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "STATIC_DIR", "TOKEN_COOKIE", "serve"]
