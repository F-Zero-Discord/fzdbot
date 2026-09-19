from datetime import UTC, datetime

import pytest

from fzdbot.cogs.ggp_submissions import active_slot
from fzdbot.cogs.submissions import format_time, parse_score, parse_time


@pytest.mark.parametrize("text", ["1:30.44", "1.30.44", "1 30 44", "01:30:44", " 1:30.44 "])
def test_a_time_entered_any_way_is_the_same_centiseconds(text):
    assert parse_time(text) == 9044


def test_a_time_round_trips_through_its_display_form():
    assert parse_time("0:05.07") == 507
    assert format_time(507) == "0:05.07"
    assert parse_time("9:59.99") == 59999
    assert format_time(59999) == "9:59.99"
    assert parse_time("0:00.00") == 0


@pytest.mark.parametrize(
    "text", ["", "90.44", "1:60.00", "10:00.00", "1:30", "1:30.4", "1:30.444", "1::30.44", "-1:30.44"]
)
def test_anything_else_is_refused(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_dnf_is_no_value():
    assert parse_time("dnf") is None
    assert parse_time("DNF") is None
    assert parse_score("dnf") is None


def test_a_score_is_a_whole_number():
    assert parse_score("87") == 87
    assert parse_score(" 0 ") == 0
    with pytest.raises(ValueError):
        parse_score("87.5")
    with pytest.raises(ValueError):
        parse_score("lots")


def slot(slot_id, starts_at):
    return {"slot_id": slot_id, "starts_at": starts_at}


NOW = datetime(2026, 9, 25, 20, 10, tzinfo=UTC)


def test_the_active_slot_is_the_one_that_started_last():
    schedule = [slot(1, "2026-09-25T19:50:00Z"), slot(2, "2026-09-25T20:00:00Z"), slot(3, "2026-09-25T20:20:00Z")]
    assert active_slot(schedule, NOW) == schedule[1]


def test_a_slot_starting_now_is_already_active():
    schedule = [slot(1, "2026-09-25T20:00:00Z"), slot(2, "2026-09-25T20:10:00Z")]
    assert active_slot(schedule, NOW) == schedule[1]


def test_a_slot_with_no_start_is_never_active():
    schedule = [slot(1, "2026-09-25T20:00:00Z"), slot(2, None)]
    assert active_slot(schedule, NOW) == schedule[0]
    assert active_slot([slot(2, None)], NOW) is None


def test_nothing_is_active_before_the_first_slot_or_on_an_empty_schedule():
    assert active_slot([slot(1, "2026-09-25T20:20:00Z")], NOW) is None
    assert active_slot([], NOW) is None
