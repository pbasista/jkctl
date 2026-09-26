"""What jkctl tells the shared server, and what that server then serves.

The transport itself -- the Host check, the token, the SSE framing, the
static handling -- belongs to `devicectl.web.server` and is tested there,
against a program invented for the purpose.  What is here is jkctl's own
half: that the real routes are wired up, that `--read-only` refuses the
writes this program actually has, and that `--simulate` reaches `serve`
without opening a port.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest
from devicectl.web.events import Broadcaster
from devicectl.web.server import UI_HEADER, Settings, UIServer
from webfake import FakeBus

from jkctl.web import api, server
from jkctl.web.session import BusWorker, Target


@pytest.fixture
def running():
    """A server on a free loopback port, with two simulated units behind it."""
    fake = FakeBus((1, 2))
    events = Broadcaster()
    worker = BusWorker(events, Target(port="fake"), open_bus=fake, idle_timeout=30)
    ctx = api.Context(worker=worker)
    api.use_context(ctx)
    made = UIServer(
        ("127.0.0.1", 0),
        branding=server.BRANDING,
        routes=api.ROUTES,
        context=ctx,
        events=events,
        settings=Settings(),
        token="",
        allowed_hosts=frozenset({"localhost"}),
    )
    thread = threading.Thread(target=made.serve_forever, daemon=True)
    thread.start()
    made.ctx = ctx  # type: ignore[attr-defined]
    yield made
    made.stopping.set()
    events.shutdown()
    made.shutdown()
    made.server_close()
    worker.stop()


def request(srv, method, path, *, body=None, headers=None, host=None):
    """One HTTP request against the test server."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    send = {"Host": host or f"127.0.0.1:{srv.server_port}"}
    send.update(headers or {})
    if body is not None:
        send.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=body, headers=send)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_about_carries_the_projects_own_urls(running):
    # The wordmark, the version beside it and the licence footer link from
    # here, and these URLs are `[project.urls]` in pyproject.toml rather than
    # three more constants in the JavaScript.  A rename that drops one of them
    # is a link that silently stops being drawn, which is what this catches.
    status, body = request(running, "GET", "/api/about")
    doc = json.loads(body)
    assert status == 200
    assert doc["app"] == "jkctl"
    assert doc["links"]["homepage"].startswith("https://")
    assert doc["links"]["releases"].endswith("/releases")
    assert doc["links"]["license"].endswith("/LICENSE")


def test_the_page_is_served(running):
    status, body = request(running, "GET", "/")
    assert status == 200
    assert b"<title>jkctl</title>" in body


def test_a_read_endpoint_answers_json(running):
    status, body = request(running, "GET", "/api/state")
    assert status == 200
    assert json.loads(body)["version"]


def test_a_write_with_the_header_is_allowed(running):
    status, _ = request(
        running,
        "POST",
        "/api/link",
        body=b'{"action":"connect"}',
        headers={UI_HEADER: "1"},
    )
    assert status == 200


def test_read_only_refuses_a_write_server_side(running):
    running.settings.read_only = True
    status, body = request(
        running,
        "POST",
        "/api/settings",
        body=b'{"id":1,"changes":{"volCellUV":"2.9"}}',
        headers={UI_HEADER: "1"},
    )
    assert status == 403 and b"read-only" in body
    assert b"battery" in body


def test_read_only_still_allows_a_read(running):
    running.settings.read_only = True
    status, _ = request(running, "GET", "/api/bank")
    assert status == 200


def test_focus_names_the_app(running):
    status, body = request(
        running, "POST", "/api/focus", body=b"{}", headers={UI_HEADER: "1"}
    )
    assert status == 200 and json.loads(body)["app"] == "jkctl"


def test_raise_open_tab_finds_this_server(running):
    from devicectl.web.server import raise_open_tab

    reply = raise_open_tab("jkctl", "127.0.0.1", running.server_port)
    assert reply is not None and reply["app"] == "jkctl"


def test_the_event_stream_opens_with_the_current_state(running):
    conn = http.client.HTTPConnection("127.0.0.1", running.server_port, timeout=5)
    conn.request("GET", "/api/events", headers={"Host": "localhost"})
    response = conn.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/event-stream")
    # SSE is newline-delimited, so read lines: read(n) would block until n
    # bytes had arrived, and the whole opening burst is shorter than that.
    seen = b""
    while b"event: link" not in seen and len(seen) < 8192:
        line = response.fp.readline()
        if not line:
            break
        seen += line
    conn.close()
    assert b"retry: " in seen
    assert b"event: hello" in seen
    assert b"event: link" in seen


# --- `jkctl ui --simulate` -----------------------------------------------


def test_simulate_serves_a_bank_and_opens_no_port(monkeypatch):
    """``--simulate`` reaches `serve` with a target and its own bus factory.

    The whole point of the flag is that nothing is opened, so this checks
    that no serial port is named and that the factory it passes really does
    answer for each simulated board.
    """
    from jkctl import identity as I
    from jkctl.cli import main
    from jkctl.device import Device
    from jkctl.registers import Catalog

    seen = {}

    def fake_serve(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("jkctl.web.serve", fake_serve)
    assert main(["ui", "--simulate", "2", "--no-browser"]) == 0

    target = seen["target"]
    assert target.slaves == (1, 2)
    assert not target.port.startswith("/dev/")
    assert "simulated" in seen["note"]

    bus = seen["open_bus"](target)
    catalog = Catalog()
    for slave in target.slaves:
        assert I.read(Device(bus, slave, catalog)).model


def test_simulate_defaults_to_a_bank_worth_looking_at(monkeypatch):
    """A bare ``--simulate`` puts more than one board on the bus.

    The bank view is the reason to open the page, and one tile does not show
    it, so the bare flag is worth a bank rather than a board.
    """
    from jkctl.cli import main

    seen = {}
    monkeypatch.setattr("jkctl.web.serve", lambda **kw: seen.update(kw) or 0)
    assert main(["ui", "--simulate", "--no-browser"]) == 0
    assert len(seen["target"].slaves) > 1
