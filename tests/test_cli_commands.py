"""End to end: argv in, exit code and output out, against a simulated BMS."""

from __future__ import annotations

import json

from jkctl import cli

PORT = ["--port", "/dev/null"]  # the fixture intercepts the open


def run(*argv) -> int:
    """Run one invocation against the simulated unit."""
    return cli.main([*argv, *PORT])


def test_scan_finds_the_unit(cli_device, capsys):
    assert run("scan", "--scan-range", "2") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "JK_PB2A16S20P" in out and "1 unit(s) found" in out


def test_info_reports_the_nameplate(cli_device, capsys):
    assert run("info") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "JK_PB2A16S20P" in out and "15.41" in out and "JK-SIM-0001" in out


def test_info_json(cli_device, capsys):
    assert run("info", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert doc["model"] == "JK_PB2A16S20P"
    assert doc["first_power_on"] == "2024-11-03"
    assert doc["ports"][0]["protocol"].startswith("001 - ")


def test_status_reports_the_pack(cli_device, capsys):
    assert run("status") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "52.80 V" in out and "87 %" in out and "28.5" in out


def test_status_json_carries_full_resolution(cli_device, capsys):
    assert run("status", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert doc["batVol"] == 52.8
    assert doc["batCurrent"] == -12.345  # milliamps, not the displayed -12.35
    assert doc["alarms"] == []


def test_cells_lists_every_present_cell(cli_device, capsys):
    assert run("cells") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "16 cells" in out and "3.304" in out and "3.293" in out


def test_cells_json(cli_device, capsys):
    assert run("cells", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert len(doc["cells"]) == 16
    assert doc["cells"][0]["voltage_v"] == 3.301


def test_alarms_says_so_when_there_are_none(cli_device, capsys):
    assert run("alarms") == cli.EXIT_OK
    assert "No alarms raised" in capsys.readouterr().out


def test_a_raised_alarm_is_named_the_way_the_vendor_names_it(cli_device, sim, capsys):
    from jkctl import protocol as P

    proto = P.Protocol()
    field = proto.field("02", "sysAlarm")
    offset = field.off - P.DATA_OFF
    sim.tables[0x200][offset : offset + 4] = (1 << 11).to_bytes(4, "big")
    assert run("alarms") == cli.EXIT_OK
    assert "Protection - Cell under voltage" in capsys.readouterr().out


def test_settings_show_and_get(cli_device, capsys):
    assert run("settings", "show") == cli.EXIT_OK
    assert "volCellUV" in capsys.readouterr().out
    assert run("settings", "get", "volCellOV") == cli.EXIT_OK
    assert "3.650 V" in capsys.readouterr().out


def test_settings_list_shows_the_range_and_the_default(cli_device, capsys):
    assert run("settings", "list", "volCellUV") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "1.200..4.400" in out and "rw" in out


def test_a_dry_run_prints_the_frame_and_writes_nothing(cli_device, capsys):
    assert run("settings", "set", "volCellUV=2.9", "--dry-run") == cli.EXIT_OK
    out = capsys.readouterr().out
    # register 0x1004, one register pair, 2900 mV big-endian, Modbus CRC
    assert "01 10 10 04 00 02 04 00 00 0b 54" in out
    assert "Dry run; nothing written." in out
    assert cli_device.read("volCellUV", "01") == 2.8


def test_a_write_lands_and_is_idempotent(cli_device, capsys):
    assert run("settings", "set", "volCellUV=2.9") == cli.EXIT_OK
    assert "2.800 V -> 2.900 V" in capsys.readouterr().out
    assert cli_device.read("volCellUV", "01") == 2.9
    assert run("settings", "set", "volCellUV=2.9") == cli.EXIT_OK
    assert "Nothing to change" in capsys.readouterr().out


def test_a_value_out_of_range_is_refused_before_anything_is_written(cli_device, capsys):
    assert run("settings", "set", "volCellUV=9") == cli.EXIT_ERROR
    assert "above the maximum" in capsys.readouterr().err
    assert cli_device.read("volCellUV", "01") == 2.8


def test_settings_export_and_import_round_trip(cli_device, tmp_path, capsys):
    path = tmp_path / "s.json"
    assert run("settings", "export", str(path)) == cli.EXIT_OK
    assert run("settings", "import", str(path)) == cli.EXIT_OK
    assert "Nothing to change" in capsys.readouterr().out


def test_an_import_asks_before_writing(cli_device, tmp_path, monkeypatch, capsys):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"settings": {"volCellUV": 2.9}}))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run("settings", "import", str(path)) == cli.EXIT_ERROR
    assert "Aborted." in capsys.readouterr().err
    assert cli_device.read("volCellUV", "01") == 2.8


def test_an_unattended_import_stops_rather_than_raising(
    cli_device, tmp_path, monkeypatch
):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"settings": {"volCellUV": 2.9}}))

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert run("settings", "import", str(path)) == cli.EXIT_ERROR


def test_the_toggles_read_and_write(cli_device, capsys):
    assert run("charge") == cli.EXIT_OK
    assert "charge: on" in capsys.readouterr().out
    assert run("charge", "off") == cli.EXIT_OK
    assert cli_device.read("batChargeEn", "01") == 0


def test_a_switch_bit_moves_without_disturbing_its_neighbours(cli_device, capsys):
    before = cli_device.read("switchStatus", "01")
    assert run("switches", "set", "Smart Sleep=on") == cli.EXIT_OK
    assert cli_device.read("switchStatus", "01") == before | (1 << 6)


def test_switches_show_names_every_bit(cli_device, capsys):
    assert run("switches", "show") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Smart Sleep" in out and "Multiplexed Port Switching" in out


def test_a_preset_shows_its_plan_and_asks_first(cli_device, sim, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run("preset", "lifepo4") == cli.EXIT_ABORTED
    out = capsys.readouterr().out
    assert "Cell OVP (volCellOV):" in out and "3.600 V" in out
    assert sim.actions == []


def test_a_confirmed_preset_writes_jks_published_values(cli_device, sim, capsys):
    assert run("preset", "lto", "-y") == cli.EXIT_OK
    assert "Wrote" in capsys.readouterr().out
    assert sim.actions == []
    assert run("settings", "show") == cli.EXIT_OK
    shown = capsys.readouterr().out
    assert "2.700" in shown and "1.800" in shown
    assert run("preset", "lto", "-y") == cli.EXIT_OK
    assert "already holds" in capsys.readouterr().out


def test_the_boards_own_one_key_slot_is_still_there(cli_device, sim, capsys):
    assert run("preset", "lifepo4", "--one-key", "-y") == cli.EXIT_OK
    assert sim.actions == [(0x0C, 1)]


def test_shutdown_warns_that_the_unit_stops_answering(
    cli_device, sim, monkeypatch, capsys
):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert run("shutdown") == cli.EXIT_OK
    assert sim.actions == [(0x04, 1)]


def test_the_clock_reads_and_writes(cli_device, sim, capsys):
    # The simulator sets its RTC from this host, so the year it reports is
    # this year rather than whichever one the fixture was written in.
    from datetime import datetime, timezone

    assert run("time", "show") == cli.EXIT_OK
    assert f"{datetime.now(timezone.utc):%Y}-" in capsys.readouterr().out
    assert run("time", "sync") == cli.EXIT_OK
    assert sim.actions and sim.actions[-1][0] == 0x12


def test_protocols_show_names_the_selected_protocol(cli_device, capsys):
    assert run("protocols", "show") == cli.EXIT_OK
    assert "001 - " in capsys.readouterr().out


def test_protocols_set_refuses_a_number_that_is_not_in_the_list(cli_device, capsys):
    assert run("protocols", "set", "uart1", "99") == cli.EXIT_ERROR
    assert "is not in the list" in capsys.readouterr().err


def test_protocols_set_writes_the_selector(cli_device, capsys):
    assert run("protocols", "set", "uart1", "13") == cli.EXIT_OK
    assert cli_device.read("uart1ProtoNo", "03") == 13


def test_protocols_list_needs_no_hardware(capsys):
    assert cli.main(["protocols", "list", "--can"]) == cli.EXIT_OK
    assert "006 - " in capsys.readouterr().out


def test_log_writes_csv_to_stdout(cli_device, capsys):
    assert run("log", "--count", "2", "--interval", "0", "--file", "-") == cli.EXIT_OK
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("time,batVol,")
    assert len(lines) == 3


def test_log_writes_json_when_asked(cli_device, tmp_path, capsys):
    path = tmp_path / "log.json"
    assert (
        run(
            "log",
            "--count",
            "1",
            "--interval",
            "0",
            "--format",
            "json",
            "--file",
            str(path),
        )
        == cli.EXIT_OK
    )
    rows = json.loads(path.read_text())
    assert rows[0]["batVol"] == 52.8


def test_doctor_reports_a_cell_count_that_does_not_match(cli_device, capsys):
    cli_device.write("cellCount", 8, "01")
    assert run("doctor") == cli.EXIT_ERROR
    out = capsys.readouterr().out
    assert "configured for 8 cells but 16 are present" in out


def test_doctor_is_quiet_on_a_healthy_unit(cli_device, capsys):
    assert run("doctor") == cli.EXIT_OK
    assert "reads as healthy" in capsys.readouterr().out


def test_config_show_and_path(tmp_path, capsys):
    path = tmp_path / "jk.toml"
    assert cli.main(["config", "init", "--config", str(path)]) == cli.EXIT_OK
    assert cli.main(["config", "path", "--config", str(path)]) == cli.EXIT_OK
    assert str(path) in capsys.readouterr().out
    assert cli.main(["config", "show", "--config", str(path)]) == cli.EXIT_OK
    # The file init writes is entirely commented out, so it configures nothing.
    assert "Nothing configured" in capsys.readouterr().out


def test_config_init_asks_before_overwriting(tmp_path, monkeypatch, capsys):
    path = tmp_path / "jk.toml"
    path.write_text("port = '/dev/ttyS1'\n")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert cli.main(["config", "init", "--config", str(path)]) == cli.EXIT_ERROR
    assert path.read_text() == "port = '/dev/ttyS1'\n"


def test_firmware_info_needs_no_hardware(tmp_path, capsys, monkeypatch):
    from test_firmware import make_blob, make_payload

    from jkctl import firmware as F

    path = tmp_path / "fw.jkbms"
    blob = make_blob(make_payload())
    path.write_bytes(b"\x00" * len(blob))
    monkeypatch.setattr(F.aes, "decrypt_cbc", lambda key, iv, data: blob)
    assert cli.main(["firmware", "info", str(path)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "JK_PB2A16S20P" in out and "15.42" in out


def test_firmware_check_refuses_an_older_image(
    cli_device, tmp_path, capsys, monkeypatch
):
    from test_firmware import make_blob, make_payload

    from jkctl import firmware as F

    path = tmp_path / "fw.jkbms"
    blob = make_blob(make_payload(version="15.41"))
    path.write_bytes(b"\x00" * len(blob))
    monkeypatch.setattr(F.aes, "decrypt_cbc", lambda key, iv, data: blob)
    assert run("firmware", "check", str(path)) == cli.EXIT_INCOMPATIBLE
    assert "minor version" in capsys.readouterr().err


def test_firmware_flash_delivers_the_image(
    cli_device, sim, tmp_path, capsys, monkeypatch
):
    from test_firmware import make_blob, make_payload

    from jkctl import firmware as F

    path = tmp_path / "fw.jkbms"
    payload = make_payload(version="15.42")
    blob = make_blob(payload)
    path.write_bytes(b"\x00" * len(blob))
    monkeypatch.setattr(F.aes, "decrypt_cbc", lambda key, iv, data: blob)
    assert run("firmware", "flash", str(path), "-y") == cli.EXIT_OK
    image = payload[: -F.TRAILER]
    assert bytes(sim.received) == image + b"\xff" * (-len(image) % 128)
    out = capsys.readouterr().out
    assert "Upload firmware successfully" in out
    # The flash re-syncs the clock so a reboot cannot leave it a time-zone
    # offset out; the RTC-calibration action (slot 0x12) is written last.
    assert "clock set to" in out
    assert sim.actions and sim.actions[-1][0] == 0x12


# --- the register browser -----------------------------------------------------------


def test_registers_shows_every_table(cli_device, capsys):
    assert run("registers") == cli.EXIT_OK
    out = capsys.readouterr().out
    # one field from each of the three tables
    assert "manuDeviceID" in out and "batVol" in out and "volCellUV" in out


def test_registers_takes_a_glob(cli_device, capsys):
    assert run("registers", "temp*") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "tempMos" in out
    assert "volCellUV" not in out


def test_registers_glob_that_matches_nothing_is_an_error(cli_device, capsys):
    assert run("registers", "nothinglikethis*") == cli.EXIT_ERROR
    assert "nothing matches" in capsys.readouterr().err


def test_registers_can_show_only_the_writable_ones(cli_device, capsys):
    assert run("registers", "--writable", "--table", "03") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "uart1ProtoNo" in out
    assert "deviceSN" not in out  # read-only in JK's map


def test_registers_raw_carries_the_address_and_the_bytes(cli_device, capsys):
    assert run("registers", "batVol", "--raw", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    row = next(r for r in doc if r["name"] == "batVol")
    # frame/02 base 0x1200 plus the field's own byte offset, 0x0090 --
    # exactly where the vendor's register map puts BatVol.
    assert row["register"] == "0x1290"
    assert bytes.fromhex(row["bytes"]) == (52800).to_bytes(4, "big")


# --- one element of an array setting ------------------------------------------------


def test_one_wire_resistance_can_be_set_on_its_own(cli_device, capsys):
    assert run("settings", "set", "cellConWireRes[3]=0.42") == cli.EXIT_OK
    assert "Wire resistance (cellConWireRes[3]): 0.00" in capsys.readouterr().out
    assert run("settings", "get", "cellConWireRes[3]", "--json") == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["cellConWireRes[3]"].startswith("0.42")


def test_an_element_out_of_range_is_refused(cli_device, capsys):
    assert run("settings", "set", "cellConWireRes[99]=0.1") == cli.EXIT_ERROR
    assert "0..31" in capsys.readouterr().err


def test_indexing_a_scalar_setting_is_refused(cli_device, capsys):
    assert run("settings", "set", "volCellUV[0]=2.9") == cli.EXIT_ERROR
    assert "single value" in capsys.readouterr().err


# --- the protocol selectors ---------------------------------------------------------


def test_protocols_list_gives_english_names(capsys):
    # `protocols list` needs no unit, so it takes none of the port options.
    assert cli.main(["protocols", "list", "--uart"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Pylontech" in out and "派能" in out


def test_every_settable_selector_is_actually_writable(catalog):
    from jkctl.cli.commands.protocols import SELECTORS, SETTABLE
    from jkctl.registers import INFO

    for word in SETTABLE:
        key = SELECTORS[word][0]
        assert catalog.find(key, INFO).writable, f"{word} is offered but read-only"
    for word in set(SELECTORS) - set(SETTABLE):
        key = SELECTORS[word][0]
        assert not catalog.find(key, INFO).writable, f"{word} could be offered"


def test_setting_a_port_jk_does_not_map_says_why(cli_device, capsys):
    # argparse rejects it before the handler, which is the point: the choice
    # is not offered rather than offered and refused.
    try:
        run("protocols", "set", "uart3", "5")
    except SystemExit as exc:
        assert exc.code == 2
    assert "uart3" in capsys.readouterr().err


# --- more than one unit on the bus --------------------------------------------------


def test_two_units_each_get_a_section(cli_bank, capsys):
    assert run("status", "--id", "1,2") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "=== BMS 1" in out and "=== BMS 2" in out
    assert out.count("State of charge") == 2


def test_two_units_json_is_one_document_keyed_by_address(cli_bank, capsys):
    assert run("info", "--id", "1,2", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert sorted(doc) == ["1", "2"]
    assert doc["1"]["model"] == "JK_PB2A16S20P"


def test_one_unit_prints_exactly_what_it_used_to(cli_bank, capsys):
    assert run("info", "--id", "2", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert "model" in doc  # not wrapped in an address


def test_a_range_of_addresses(cli_bank, capsys):
    assert run("alarms", "--id", "1-2") == cli.EXIT_OK
    assert capsys.readouterr().out.count("No alarms raised") == 2


def test_a_silent_unit_does_not_stop_the_others(cli_bank, capsys):
    # address 3 is not on this bank
    assert run("info", "--id", "1,3") == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "JK_PB2A16S20P" in captured.out  # unit 1 was still read
    assert "BMS 3" in captured.err


def test_address_show_reports_both(cli_device, capsys):
    assert run("address", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert doc == {"answering": 1, "devAddr": 1}


def test_address_set_asks_and_writes(cli_device, sim, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert run("address", "set", "3") == cli.EXIT_OK
    capsys.readouterr()
    assert run("address", "--json") == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["devAddr"] == 3


def test_address_set_refuses_an_impossible_address(cli_device, capsys):
    assert run("address", "set", "99", "-y") == cli.EXIT_ERROR
    assert "0..15" in capsys.readouterr().err


# --- the stored records, and the board actions --------------------------------------


def test_log_codes_needs_no_hardware(capsys):
    assert cli.main(["log-codes"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Reset Watch-Dog" in out and "Cell 32 over discharge protection" in out


def test_history_says_when_a_board_does_not_serve_them(cli_device, capsys):
    assert run("history") == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "does not serve the stored records over Modbus" in err
    assert "--from-flash-dump" in err  # points at the way that works


def test_history_from_a_flash_dump_decodes_the_records(tmp_path, capsys):
    from jkctl import history as H

    record = struct_pack_stored(code=7, rtc=123_456)
    image = bytearray(b"\xff" * 0x20000)
    off = H.STORAGE_BASE - H.FLASH_BASE
    image[off : off + H.RECORD_BYTES] = record
    dump = tmp_path / "full.bin"
    dump.write_bytes(bytes(image))
    assert run("history", "--from-flash-dump", str(dump)) == cli.EXIT_OK
    assert "Remote close charge" in capsys.readouterr().out  # code 7


def struct_pack_stored(**over):
    import struct

    from jkctl import history as H

    values = {
        "rtc": 100_000,
        "code": 29,
        "switches": 0b0011,
        "max_no": 5,
        "min_no": 2,
        "cell_max": 3450,
        "cell_min": 3120,
        "pack_v": 5327,
        "pack_a": 265,
        "remaining": 2968,
        "full": 3120,
        "max_temp": 24,
        "min_temp": -7,
        "mos_temp": 31,
        "heat": 15,
    }
    values.update(over)
    return struct.pack(H.STORED_RECORD_FORMAT, *values.values())


def test_the_board_actions_ask_and_fire_their_slots(cli_device, sim, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert run("restart") == cli.EXIT_OK
    assert run("factory-restore") == cli.EXIT_OK
    assert run("erase-data") == cli.EXIT_OK
    assert [slot for slot, _ in sim.actions] == [0x16, 0x18, 0x1A]


def test_a_declined_board_action_writes_nothing(cli_device, sim, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run("factory-restore") == cli.EXIT_ABORTED
    assert sim.actions == []


def test_settings_carry_the_page_s_names_beside_jk_s(cli_device, capsys):
    # A setting is called the same on the command line as on the page, with
    # JK's label and the key beside it so it can be found in JK's app and typed.
    assert run("settings", "list", "volCellRCV") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Cell RCV" in out and "Vol. Cell RCV" in out and "volCellRCV" in out
    assert run("settings", "list", "Cell RCV", "--json") == cli.EXIT_OK
    (doc,) = json.loads(capsys.readouterr().out)
    assert doc["name"] == "volCellRCV" and doc["title"] == "Cell RCV"
    assert doc["label"] == "Vol. Cell RCV"
    assert "Request charge voltage (RCV)" in doc["description"]
    assert run("settings", "get", "volCellRCV") == cli.EXIT_OK
    assert "Cell RCV" in capsys.readouterr().out
    assert run("registers", "volCellRCV", "--json") == cli.EXIT_OK
    (doc,) = json.loads(capsys.readouterr().out)
    assert doc["title"] == "Cell RCV" and doc["label"] == "Vol. Cell RCV"


def test_status_is_grouped_and_named_as_the_dashboard(cli_bank, capsys):
    # Board 2 of the simulated bank is balancing from cell 5 to cell 12.
    assert run("status", "--id", "2") == cli.EXIT_OK
    out = capsys.readouterr().out
    for heading in ("\n  Pack\n", "\n  Cells\n", "\n  Temperatures\n"):
        assert heading in out
    assert "Spread" in out and "Remaining" in out and "Running for" in out
    assert "cell 5 → cell 12" in out
    assert "Highest cell      cell 5" in out  # counted from one, as on the page
    assert "Battery 3" not in out  # a probe the unit says is not connected
    assert run("cells", "--id", "2", "--json") == cli.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert (doc["highest_cell"], doc["lowest_cell"]) == (5, 12)
