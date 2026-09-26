"""Every endpoint, against the simulated bus the CLI tests use."""

from __future__ import annotations

import json

import pytest
from devicectl.errors import DeviceError
from devicectl.web.events import Broadcaster
from webfake import FakeBus

from jkctl.web import api
from jkctl.web.session import BusWorker, Target


@pytest.fixture
def ctx():
    """A context whose worker talks to two simulated units."""
    fake = FakeBus((1, 2))
    worker = BusWorker(
        Broadcaster(), Target(port="fake"), open_bus=fake, idle_timeout=30
    )
    made = api.Context(worker=worker)
    api.use_context(made)
    made.fake = fake  # type: ignore[attr-defined]
    worker.run("scan", lambda w: w.rescan())
    yield made
    worker.stop()


def call(ctx, method, path, **kwargs):
    """Run one endpoint and return its parsed JSON body."""
    body = kwargs.pop("body", None)
    query = {k: str(v) for k, v in kwargs.items()}
    request = api.Request(
        method=method,
        path=path,
        query=query,
        body=json.dumps(body).encode() if body is not None else b"",
    )
    route = api.ROUTES[(method, path)]
    response = route.handler(ctx, request)
    return json.loads(response.body or b"null")


def test_state_reports_the_link_and_the_units(ctx):
    doc = call(ctx, "GET", "/api/state")
    assert doc["link"]["state"] in ("idle", "released")
    assert [u["id"] for u in doc["link"]["units"]] == [1, 2]


def test_bank_gives_one_row_per_unit(ctx):
    rows = call(ctx, "GET", "/api/bank")["units"]
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[0]["ok"] and rows[0]["soc"] == 87
    assert rows[0]["cellCount"] == 16


def test_dashboard_carries_identity_runtime_and_limits(ctx):
    doc = call(ctx, "GET", "/api/dashboard", id=1)
    assert doc["identity"]["model"] == "JK_PB2A16S20P"
    assert doc["runtime"]["fields"]["batVol"]["value"] == 52.8
    assert len(doc["runtime"]["cells"]) == 16
    names = {r["name"] for r in doc["limits"]}
    assert "volCellOV" in names


def test_the_dashboard_says_how_far_the_clock_is_out(ctx):
    doc = call(ctx, "GET", "/api/dashboard", id=1)
    # The simulator sets its RTC from this host, so the drift is seconds.
    assert abs(doc["clock"]["driftS"]) < 60


def test_registers_describe_themselves(ctx):
    rows = call(ctx, "GET", "/api/registers", id=1, table="01")["registers"]
    one = next(r for r in rows if r["name"] == "volCellUV")
    assert one["unit"] == "V"
    assert one["writable"] and one["kind"] == "number"
    assert one["minimum"] == 1.2 and one["maximum"] == 4.4


def test_settings_plan_is_a_dry_run_until_it_is_not(ctx):
    doc = call(
        ctx,
        "POST",
        "/api/settings",
        body={"id": 1, "dryRun": True, "changes": {"volCellUV": "2.9"}},
    )
    assert doc["changes"][0]["newText"].startswith("2.900")
    # nothing was written
    again = call(
        ctx,
        "POST",
        "/api/settings",
        body={"id": 1, "dryRun": False, "changes": {"volCellUV": "2.9"}},
    )
    assert len(again["changes"]) == 1
    after = call(ctx, "GET", "/api/settings", id=1)["settings"]
    assert next(r for r in after if r["name"] == "volCellUV")["value"] == 2.9


def test_a_value_out_of_range_never_reaches_the_wire(ctx):
    with pytest.raises(Exception) as caught:
        call(
            ctx,
            "POST",
            "/api/settings",
            body={"id": 1, "changes": {"volCellUV": "99"}},
        )
    assert "4.4" in str(caught.value)


def test_a_temperature_below_zero_is_written_and_read_back(ctx):
    # The request of jkctl-trace-20260926-060408: a temperature of -5 degC,
    # which failed with "'B' format requires 0 <= number <= 255" because the
    # datasource gives the byte no type and it was packed unsigned.  The
    # setting in that trace is one the board then turned out not to take
    # (see the next test), so this writes its heater neighbour instead.
    for dry in (True, False):
        doc = call(
            ctx,
            "POST",
            "/api/settings",
            body={"id": 1, "dryRun": dry, "changes": {"tmpStartHeating": "-5"}},
        )
        assert doc["changes"][0]["newText"].startswith("-5")
    after = call(ctx, "GET", "/api/settings", id=1)["settings"]
    assert next(r for r in after if r["name"] == "tmpStartHeating")["value"] == -5


def test_a_setting_the_board_will_not_take_is_said_once_and_then_shown(ctx):
    # jkctl-trace-20260926-074705: firmware 15.41 answers the write of the
    # discharge under-temperature pair at 0x1122 with exception 2, and does so
    # whatever it is sent.
    with pytest.raises(DeviceError) as caught:
        call(
            ctx,
            "POST",
            "/api/settings",
            body={"id": 1, "changes": {"tmpBatDCHUT": "-5"}},
        )
    said = str(caught.value)
    assert "does not take Discharge UTP" in said
    assert "0x1122" in said and "tmpBatDCHUTPR shares the register" in said
    # Its partner shares the word, so it is known to be refused too, and the
    # plan turns both down before anything goes on the wire again.
    for key in ("tmpBatDCHUT", "tmpBatDCHUTPR"):
        with pytest.raises(DeviceError, match="does not take"):
            call(
                ctx,
                "POST",
                "/api/settings",
                body={"id": 1, "dryRun": True, "changes": {key: "-1"}},
            )
    rows = {r["name"]: r for r in call(ctx, "GET", "/api/settings", id=1)["settings"]}
    assert "does not take" in rows["tmpBatDCHUT"]["refused"]
    assert (
        rows["tmpBatDCHUTPR"]["refused"] and rows["tmpStartHeating"]["refused"] is None
    )
    assert rows["tmpBatDCHUT"]["value"] == -20


def test_one_element_of_an_array_can_be_written(ctx):
    doc = call(
        ctx,
        "POST",
        "/api/settings",
        body={"id": 1, "dryRun": False, "changes": {"cellConWireRes[2]": "0.5"}},
    )
    assert doc["changes"][0]["name"] == "cellConWireRes[2]"


def test_switches_are_named_and_flippable(ctx):
    rows = call(ctx, "GET", "/api/switches", id=1)["switches"]
    assert any(r["name"] for r in rows)
    bit = rows[0]["bit"]
    call(ctx, "POST", "/api/switch", body={"id": 1, "bit": bit, "on": True})
    again = call(ctx, "GET", "/api/switches", id=1)["switches"]
    assert next(r for r in again if r["bit"] == bit)["on"]


def test_controls_drive_the_three_main_switches(ctx):
    doc = call(
        ctx, "POST", "/api/control", body={"id": 1, "what": "balance", "on": False}
    )
    assert doc["state"]["balance"] is False


def test_doctor_runs_a_pass(ctx):
    doc = call(ctx, "GET", "/api/doctor", id=1)
    assert "findings" in doc and doc["ok"] is True


def test_protocols_carry_english_names_and_which_are_settable(ctx):
    doc = call(ctx, "GET", "/api/protocols", id=1)
    assert any("Pylontech" in p["name"] for p in doc["lists"]["uart"])
    settable = {s["name"]: s["settable"] for s in doc["selectors"]}
    assert settable["uart1"] and not settable["uart3"]


def test_setting_an_unsettable_port_is_refused(ctx):
    with pytest.raises(api.ApiError):
        call(
            ctx,
            "POST",
            "/api/protocol",
            body={"id": 1, "selector": "uart3", "number": 5},
        )


def test_actions_fire_the_slots(ctx):
    call(ctx, "POST", "/api/action", body={"id": 1, "action": "time-sync"})
    assert any(slot == 0x12 for slot, _ in ctx.fake.sim(1).actions)


def test_export_comes_back_as_a_file(ctx):
    request = api.Request(method="GET", path="/api/export", query={"id": "1"})
    response = api.ROUTES[("GET", "/api/export")].handler(ctx, request)
    assert "attachment" in response.headers["Content-Disposition"]
    assert json.loads(response.body)["model"] == "JK_PB2A16S20P"


def test_import_plans_before_it_writes(ctx):
    request = api.Request(method="GET", path="/api/export", query={"id": "1"})
    text = api.ROUTES[("GET", "/api/export")].handler(ctx, request).body.decode()
    doc = call(ctx, "POST", "/api/import", body={"id": 2, "file": text, "dryRun": True})
    assert doc["changes"] == []  # the two simulated units start identical


def test_an_unknown_address_is_a_clean_failure(ctx):
    with pytest.raises(Exception) as caught:
        call(ctx, "GET", "/api/dashboard", id=9)
    assert "address 9" in str(caught.value)


def test_the_live_refresh_records_samples(ctx):
    api.publish_live(ctx.worker)
    assert ctx.samples[1][0]["soc"] == 87
    assert ctx.samples[1][0]["voltage"] == 52.8
    # Board 2 of a simulated bank is the one near the top of a charge.
    assert ctx.samples[2][0]["voltage"] == 55.2


def test_the_live_refresh_publishes_the_whole_reading(ctx):
    # The dashboard draws the reading, not the bank tile, so a beat that
    # published only the tile left every cell voltage and every alarm on the
    # page as they were when the tab was opened.
    api.publish_live(ctx.worker)
    doc = ctx.worker.events.sticky("runtime")
    assert sorted(doc["units"]) == ["1", "2"]
    one = doc["units"]["1"]
    assert one["cellCount"] == len(one["cells"]) == 16
    assert one["fields"]["batVol"]["value"] == 52.8
    assert "alarms" in one


def test_a_unit_that_stops_answering_leaves_the_others_published(ctx):
    # A failed row still goes out on the bank; the reading of a unit that
    # did not answer is simply absent rather than a half-filled document.
    ctx.fake.link.sims = [ctx.fake.sim(1)]  # unit 2 stops answering
    api.publish_live(ctx.worker)
    assert list(ctx.worker.events.sticky("runtime")["units"]) == ["1"]
    rows = {row["id"]: row for row in ctx.worker.events.sticky("bank")["units"]}
    assert rows[1]["ok"] is True
    assert rows[2]["ok"] is False


# --- the firmware path ---------------------------------------------------------------


def firmware_bytes(monkeypatch, model="JK_PB2A16S20P", version="15.99"):
    """A .jkbms whose container decrypts to a payload we control."""
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_firmware import make_blob, make_payload

    from jkctl import firmware as F

    blob = make_blob(make_payload(model=model, version=version))
    monkeypatch.setattr(F.aes, "decrypt_cbc", lambda key, iv, data: blob)
    return b"\x00" * len(blob)


def upload(ctx, path, body, **query):
    """Post a raw body to an endpoint, the way a file upload arrives."""
    request = api.Request(
        method="POST", path=path, query={k: str(v) for k, v in query.items()}, body=body
    )
    response = api.ROUTES[("POST", path)].handler(ctx, request)
    return json.loads(response.body)


def test_the_gate_comes_back_as_a_checklist(ctx, monkeypatch):
    doc = upload(ctx, "/api/firmware/check", firmware_bytes(monkeypatch), id=1)
    assert doc["firmware"]["model"] == "JK_PB2A16S20P"
    assert doc["compatible"] is True
    names = [c["name"] for c in doc["checks"]]
    assert "major version matches" in names and "model matches" in names


def test_a_file_for_another_board_is_refused_with_every_step_shown(ctx, monkeypatch):
    doc = upload(
        ctx, "/api/firmware/check", firmware_bytes(monkeypatch, model="JK_OTHER"), id=1
    )
    assert doc["compatible"] is False
    assert doc["forcible"] is False  # --force never waives the model
    blocking = [c["name"] for c in doc["checks"] if c["blocking"]]
    assert blocking == ["model matches"]


def test_flashing_delivers_the_image_and_reports_progress(ctx, monkeypatch):
    import time

    body = firmware_bytes(monkeypatch)
    doc = upload(ctx, "/api/firmware/flash", body, id=1)
    job = doc["job"]
    end = time.time() + 20
    while time.time() < end:
        held = ctx.worker.job(job["id"])
        if held and held.state in ("done", "failed"):
            break
        time.sleep(0.02)
    held = ctx.worker.job(job["id"])
    assert held.state == "done", held.error
    # The simulator's XMODEM receiver reassembled what the sender sent.
    assert ctx.fake.sim(1).received


# --- the stored records --------------------------------------------------------------


def test_history_says_a_board_does_not_map_it_rather_than_failing(ctx):
    doc = call(ctx, "GET", "/api/history", id=1)
    assert doc["supported"] is False
    assert "does not answer at register" in doc["why"]
    assert doc["records"] == []


def test_the_log_code_table_is_served(ctx):
    codes = call(ctx, "GET", "/api/log-codes")["codes"]
    assert codes["1"] == "Boot"
    assert codes["231"] == "Cell 32 over discharge protection"


def test_a_board_action_fires_its_slot(ctx):
    call(ctx, "POST", "/api/action", body={"id": 1, "action": "factory-restore"})
    assert (0x18, 1) in ctx.fake.sim(1).actions


def test_a_port_chosen_in_the_page_is_opened_the_way_the_command_line_would(ctx):
    """The picker names a port; everything else comes from the configuration.

    The page asks for a port and, if the person typed one, a baud.  It has
    nothing to say about the timeout, the retry count or the frame offset --
    which the configuration file may well have something to say about.  These
    used to be a second copy of the factory values written into the handler,
    so the same adapter was read one way when it was named with ``--port``
    and another way when it was clicked on in the browser.
    """
    ctx.defaults = Target(
        port="/dev/ttyUSB0", baud=9600, timeout=1.25, retries=5, addr_offset=0x2000
    )
    doc = call(ctx, "POST", "/api/port", body={"port": "fake"})
    assert doc["target"] == {
        "port": "fake",
        "baud": 9600,
        "timeout": 1.25,
        "retries": 5,
        "addrOffset": 0x2000,
        "ids": [],
    }


def test_a_baud_typed_into_the_picker_still_wins(ctx):
    ctx.defaults = Target(port="/dev/ttyUSB0", baud=9600)
    doc = call(ctx, "POST", "/api/port", body={"port": "fake", "baud": 115200})
    assert doc["target"]["baud"] == 115200


def test_the_port_list_says_what_a_chosen_port_will_be_opened_with(ctx):
    """So the picker can show the speed rather than assume one."""
    ctx.defaults = Target(port="/dev/ttyUSB0", baud=19200)
    doc = call(ctx, "GET", "/api/ports")
    assert doc["defaults"]["baud"] == 19200
    assert doc["current"]["port"] == "fake"


def test_a_preset_is_planned_and_written_like_any_other_change(ctx):
    doc = call(
        ctx, "POST", "/api/settings", body={"id": 1, "dryRun": True, "preset": "lto"}
    )
    planned = {c["name"]: c["new"] for c in doc["changes"]}
    assert planned["volCellOV"] == 2.7 and planned["volSysPwrOff"] == 1.7
    call(ctx, "POST", "/api/settings", body={"id": 1, "preset": "lto"})
    again = call(
        ctx, "POST", "/api/settings", body={"id": 1, "dryRun": True, "preset": "lto"}
    )
    assert again["changes"] == []
    with pytest.raises(api.ApiError):
        call(ctx, "POST", "/api/settings", body={"id": 1, "preset": "lead-acid"})


def test_a_pack_at_rest_marks_no_cell_as_balancing(ctx):
    runtime = call(ctx, "GET", "/api/dashboard", id=1)["runtime"]
    assert runtime["balance"] is None
    assert not any(cell["balance"] for cell in runtime["cells"])


def test_a_balancing_pack_names_the_two_cells_it_works_between(ctx):
    """JK's balancer moves charge from the highest cell to the lowest.

    The board says which two those are and not what each cell is doing, so
    the page is told those two -- counted from one, as it counts cells --
    and nothing about the other fourteen.
    """
    runtime = call(ctx, "GET", "/api/dashboard", id=2)["runtime"]
    assert runtime["balance"] == {"from": 5, "to": 12, "current": 0.42}
    roles = {
        cell["cell"]: cell["balance"] for cell in runtime["cells"] if cell["balance"]
    }
    assert roles == {5: "giving", 12: "taking"}


def test_a_setting_is_named_the_same_everywhere_and_keeps_jks_label(ctx):
    regs = {r["name"]: r for r in call(ctx, "GET", "/api/settings", id=1)["settings"]}
    rcv = regs["volCellRCV"]
    assert rcv["label"] == "Cell RCV"
    assert rcv["vendor"] == "Vol. Cell RCV"
    assert "Request charge voltage (RCV)" in rcv["description"]
    plan = call(
        ctx,
        "POST",
        "/api/settings",
        id=1,
        body={"changes": {"volCellRCV": "3.44"}, "dryRun": True},
    )
    assert plan["changes"][0]["label"] == "Cell RCV"
