"""The local web UI: a browser front end onto the same BMS operations.

``jkctl ui`` starts a small HTTP server on this machine -- and, since it is
what ``jkctl`` does when given no arguments at all, it is how most people
will meet this program.  The browser gets the bank, a dashboard per unit, the
configuration, the register browser and the firmware library; the server
keeps the one serial port and tells every open page what that port is doing.

* :mod:`jkctl.web.session` owns the bus and serialises all work,
* :mod:`jkctl.web.schema` is the JSON contract between the two halves,
* :mod:`jkctl.web.api` is the endpoints,
* :mod:`jkctl.web.server` is the HTTP transport,
* ``static/`` is the page itself.

The fan-out to the browsers is :mod:`devicectl.web.events`, and the request,
reply and routing types the endpoints are written against are
:mod:`devicectl.web.http`.

Nothing here re-implements a BMS operation: every endpoint calls the module
the CLI calls, so a value read in the browser is the value ``jkctl`` prints.
"""

from __future__ import annotations

from jkctl.web.server import DEFAULT_HOST, DEFAULT_PORT, serve

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "serve"]
