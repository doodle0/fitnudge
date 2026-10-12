from datetime import datetime

import pytest

from scheduler.jobs import resolve_followup_time
from utils.time_utils import KST, is_quiet_hour, parse_local_time_today


def kst(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 13, h, m, tzinfo=KST)


@pytest.mark.parametrize("hour,expected", [(21, False), (22, True), (23, True), (0, True), (7, True), (8, False)])
def test_quiet_hours_wrap_midnight(hour, expected):
    assert is_quiet_hour(kst(hour), 22, 8) is expected


def test_quiet_hours_same_day_window():
    assert is_quiet_hour(kst(13), 12, 14)
    assert not is_quiet_hour(kst(14), 12, 14)


def test_parse_local_time_rolls_to_tomorrow():
    assert parse_local_time_today("21:00", now=kst(18, 30)) == kst(21)
    assert parse_local_time_today("09:00", now=kst(18, 30)).day == 14


def test_followup_at_and_in_minutes():
    assert resolve_followup_time(at="20:30", now=kst(18, 35)) == kst(20, 30)
    assert resolve_followup_time(in_minutes=45, now=kst(18, 0)) == kst(18, 45)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({}, "exactly one"),
        ({"at": "20:00", "in_minutes": 30}, "exactly one"),
        ({"in_minutes": 2}, "at least 5 minutes"),
        ({"at": "22:30"}, "quiet hours"),
        ({"at": "25:99"}, "Invalid time"),
    ],
)
def test_followup_rejections(kwargs, message):
    with pytest.raises(ValueError, match=message):
        resolve_followup_time(now=kst(18, 0), **kwargs)
