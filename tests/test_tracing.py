"""The serial recorder: what it captures over a real exchange, and the report."""

from __future__ import annotations

import json

import pytest
from devicectl.web.events import Broadcaster
from webfake import FakeBus

from jkctl import modbus as M, tracing as TR
from jkctl.registers import Catalog
from jkctl.web import api
from jkctl.web.session import BusWorker, Target


@pytest.fixture
def ctx():
    """A context whose worker talks to one simulated unit."""
    fake = FakeBus((1,))
    worker = BusWorker(
        Broadcaster(), Target(port="fake"), open_bus=fake, idle_timeout=30
    )
    made = api.Context(worker=worker)
    api.use_context(made)
    worker.run("scan", lambda w: w.rescan())
    yield made
    worker.stop()


def call(ctx, method, path, **kwargs):
    """Run one endpoint and return the raw response."""
    body = kwargs.pop("body", None)
    request = api.Request(
        method=method,
        path=path,
        query={k: str(v) for k, v in kwargs.items()},
        body=json.dumps(body).encode() if body is not None else b"",
    )
    return api.ROUTES[(method, path)].handler(ctx, request)


def doc(response):
    """The parsed body of a JSON response."""
    return json.loads(response.body or b"null")


# --- decoding a frame -------------------------------------------------------------------


def test_a_read_request_names_the_table_and_the_settings_it_covers():
    catalog = Catalog()
    body = bytes([1, M.FC_READ]) + (0x1000).to_bytes(2, "big") + (4).to_bytes(2, "big")
    frame = body + M.crc16(body).to_bytes(2, "little")
    said = TR.frames("TX", frame, addr_offset=M.FRAME_ADDR_OFFSET, catalog=catalog)
    assert said.startswith("read · slave 1 · 4 register(s) at 0x1000")
    assert "settings byte 0x00" in said
    assert "CRC ok" in said
    assert "covers" in said


def test_a_corrupt_frame_says_its_checksum_does_not_agree():
    catalog = Catalog()
    body = bytes([1, M.FC_READ]) + (0x1000).to_bytes(2, "big") + (4).to_bytes(2, "big")
    said = TR.frames(
        "TX", body + b"\x00\x00", addr_offset=M.FRAME_ADDR_OFFSET, catalog=catalog
    )
    assert "CRC BAD" in said


def test_a_refusal_is_read_as_the_exception_it_carries():
    catalog = Catalog()
    frame = bytes([1, M.FC_READ | 0x80, 2, 0xC0, 0xF1])
    said = TR.frames("RX", frame, addr_offset=M.FRAME_ADDR_OFFSET, catalog=catalog)
    assert "refused" in said
    assert "illegal data address" in said


def test_an_action_slot_is_named_as_one_rather_than_as_a_table():
    catalog = Catalog()
    reg = M.FRAME_ADDR_OFFSET + M.ACTION_BASE + M.ACTION_UPGRADE
    body = bytes([1, M.FC_WRITE]) + reg.to_bytes(2, "big") + (1).to_bytes(2, "big")
    frame = body + b"\x02\x00\x01" + b"\x00\x00"
    said = TR.frames("TX", frame, addr_offset=M.FRAME_ADDR_OFFSET, catalog=catalog)
    assert "action slot 0x26" in said


# --- the recorder over a real exchange --------------------------------------------------


def test_nothing_is_recorded_until_the_page_asks(ctx):
    call(ctx, "GET", "/api/dashboard", id=1)
    assert ctx.worker.trace.state()["frames"] == 0


def test_a_recording_holds_both_directions_of_a_real_read(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    call(ctx, "GET", "/api/dashboard", id=1)
    kept = ctx.worker.trace.entries()
    assert any(e.direction == "TX" for e in kept)
    assert any(e.direction == "RX" and e.data for e in kept)
    # Every frame carries what the program was doing when it went out.
    assert any("reading BMS 1" in e.note for e in kept)


def test_the_report_decodes_what_it_recorded(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    call(ctx, "GET", "/api/dashboard", id=1)
    response = call(ctx, "GET", "/api/trace")
    text = response.body.decode()
    assert response.content_type.startswith("text/plain")
    assert "attachment" in response.headers["Content-Disposition"]
    assert TR.TITLE in text
    assert "BMS 1 (JK_PB2A16S20P" in text
    assert "read · slave 1" in text
    assert "CRC ok" in text
    assert "settings byte" in text or "runtime data byte" in text


def test_a_failed_operation_is_written_into_the_recording(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    with pytest.raises(Exception):
        ctx.worker.run("reading nothing", lambda w: w.device(9).read_info())
    assert any("failed" in e.note for e in ctx.worker.trace.entries())


def test_stopping_keeps_the_recording_and_clearing_throws_it_away(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    call(ctx, "GET", "/api/dashboard", id=1)
    held = doc(call(ctx, "POST", "/api/trace", body={"action": "stop"}))["trace"]
    assert held["on"] is False and held["frames"] > 0
    empty = doc(call(ctx, "POST", "/api/trace", body={"action": "clear"}))["trace"]
    assert empty["frames"] == 0


def test_a_trace_that_is_not_a_trace_action_is_refused(ctx):
    from devicectl.web.http import ApiError

    with pytest.raises(ApiError):
        call(ctx, "POST", "/api/trace", body={"action": "burn"})


def test_the_link_the_page_draws_carries_the_recorder(ctx):
    state = doc(call(ctx, "GET", "/api/state"))["link"]
    assert state["trace"]["on"] is False
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    assert doc(call(ctx, "GET", "/api/state"))["link"]["trace"]["on"] is True


# --- what the page asked for ------------------------------------------------------------


def served(ctx, method, path, **kwargs):
    """Run one endpoint the way the server does: watched, then handled."""
    from devicectl.web.http import ApiError

    watch = api.watcher(ctx)
    body = kwargs.pop("body", None)
    request = api.Request(
        method=method,
        path=path,
        query={k: str(v) for k, v in kwargs.items()},
        body=json.dumps(body).encode() if body is not None else b"",
    )
    watch(request, None, "")
    try:
        response = api.ROUTES[(method, path)].handler(ctx, request)
    except ApiError as exc:
        watch(request, exc.status, exc.message)
        raise
    except Exception as exc:
        watch(request, 500, str(exc))
        raise
    watch(request, response.status, "")
    return response


def test_a_request_is_recorded_as_it_arrives_and_again_when_it_is_answered(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    served(ctx, "GET", "/api/dashboard", id=1)
    said = [e.note for e in ctx.worker.trace.entries() if e.direction == "UI"]
    assert said == ["GET /api/dashboard?id=1", "GET /api/dashboard?id=1  ->  200"]


def test_the_body_the_page_sent_is_kept_beside_the_request(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    with pytest.raises(Exception):
        served(ctx, "POST", "/api/settings", body={"id": 99, "changes": {"x": "1"}})
    entries = [e for e in ctx.worker.trace.entries() if e.direction == "UI"]
    assert '"changes"' in entries[0].detail
    assert entries[-1].note.startswith("POST /api/settings  ->  ")


def test_the_frames_a_request_caused_sit_between_the_two_lines_about_it(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    served(ctx, "GET", "/api/dashboard", id=1)
    kinds = [e.direction for e in ctx.worker.trace.entries()]
    first, last = kinds.index("UI"), len(kinds) - 1 - kinds[::-1].index("UI")
    assert "TX" in kinds[first:last] and "RX" in kinds[first:last]


def test_the_recording_does_not_record_being_asked_about_itself(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    served(ctx, "POST", "/api/trace", body={"action": "clear"})
    served(ctx, "GET", "/api/trace")
    assert [e for e in ctx.worker.trace.entries() if e.direction == "UI"] == []


def test_nothing_is_recorded_while_the_recorder_is_off(ctx):
    served(ctx, "GET", "/api/dashboard", id=1)
    assert ctx.worker.trace.entries() == []


def test_the_report_says_how_long_the_bus_leaves_between_frames(ctx):
    call(ctx, "POST", "/api/trace", body={"action": "start"})
    text = call(ctx, "GET", "/api/trace").body.decode()
    assert "ms between frames" in text
