"""The board's clock, counted the way JK's own application counts it."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from jkctl import identity as I


@pytest.fixture
def bratislava(monkeypatch):
    # A zone an hour east of UTC in winter and two in summer, which is where
    # a count from UTC midnight and a count from local midnight part.
    monkeypatch.setenv("TZ", "Europe/Bratislava")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_the_count_starts_at_local_midnight(bratislava):
    # JK BMS Monitor: now.toSecsSinceEpoch() minus QDateTime(2020-01-01
    # 00:00, Qt::LocalTime).toSecsSinceEpoch().  Local midnight in a UTC+1
    # winter was 23:00 UTC the night before, so UTC midnight is an hour in.
    assert I.datetime_to_rtc(datetime(2020, 1, 1, tzinfo=timezone.utc)) == 3600


def test_a_count_reads_back_as_the_instant_it_was_written_at(bratislava):
    summer = datetime(2026, 9, 26, 6, 3, 46, tzinfo=timezone.utc)
    back = I.rtc_to_datetime(I.datetime_to_rtc(summer))
    assert back == summer
    # ... and it is told in the local time the board's count is kept in.
    assert back.replace(tzinfo=None) == datetime(2026, 9, 26, 8, 3, 46)
