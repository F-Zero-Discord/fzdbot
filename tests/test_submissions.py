from datetime import UTC, datetime

import pytest

from fzdbot.api_types import (
    PlayerStandingResponse,
    ScoreboardRowResponse,
    ScoringMethod,
    SlotResponse,
    SlotResultResponse,
)
from fzdbot.cogs.ggp_submissions import active_slot, result_lines, standing_footer
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


def slot(slot_id: int, starts_at: str | None = None, *, short: str = "Knight", multiplier: int = 1) -> SlotResponse:
    return {
        "slot_id": slot_id,
        "position": slot_id,
        "lineup_id": slot_id,
        "lineup_name": None,
        "lineup_short_name": short,
        "mode": "Grand Prix",
        "kind": "prix",
        "max_score": None,
        "multiplier": multiplier,
        "starts_at": starts_at,
        "tracks": [],
        "vote_winner": None,
        "vote_winners": [],
        "lobby": None,
    }


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


def result(
    slot_id: int,
    *,
    score: int | None = None,
    time_cs: int | None = None,
    value: int | None = None,
    submitted: bool = True,
    counted: bool = True,
    open: bool = True,
    loss_cs: int | None = None,
    machine: str | None = None,
) -> SlotResultResponse:
    return {
        "slot_id": slot_id,
        "score": score,
        "time_cs": time_cs,
        "value": value,
        "submitted": submitted,
        "counted": counted,
        "open": open,
        "loss_cs": loss_cs,
        "machine": machine,
    }


def standing(
    method: ScoringMethod,
    results: list[SlotResultResponse] | None,
    *,
    total: int | None = None,
    rank: int | None = None,
    max_time_loss_cs: int | None = None,
) -> PlayerStandingResponse:
    row: ScoreboardRowResponse | None = None
    if results is not None:
        row = {
            "display_name": "lurch",
            "emote": None,
            "group_id": None,
            "results": results,
            "submissions": sum(r["submitted"] for r in results),
            "total": total,
            "rank": rank,
        }
    return {
        "scheduled_event_id": 736,
        "group_kind": None,
        "scoring_method": method,
        "num_mulligans": 1,
        "max_time_loss_cs": max_time_loss_cs,
        "machine_counts_once": False,
        "row": row,
    }


def test_a_points_line_shows_the_multiplier_and_a_dropped_result_is_struck():
    schedule = [slot(1), slot(2, short="MP", multiplier=3), slot(3), slot(4)]
    results = [
        result(1, score=800, value=800, machine="Blue Falcon"),
        result(2, score=250, value=750, machine="Wild Goose"),
        result(3, score=None, value=0, counted=False, machine="Blue Falcon"),
        result(4, value=0, submitted=False, counted=False),
    ]
    assert result_lines(schedule, standing("points", results)) == [
        "#1 Knight — 800 · Blue Falcon",
        "#2 MP — 250 ×3 = 750 · Wild Goose",
        "#3 Knight — ~~DNF · Blue Falcon~~ (dropped)",
        "#4 Knight — ~~not submitted~~ (dropped)",
    ]


def test_a_time_line_shows_the_loss_and_names_the_cap_where_it_bites():
    schedule = [slot(i, short="MC I") for i in range(1, 6)]
    results = [
        result(1, time_cs=14940, value=2000, loss_cs=2504, machine="Blue Falcon"),
        result(2, time_cs=12940, value=504, loss_cs=504),
        result(3, time_cs=None, value=2000),
        result(4, value=0, submitted=False, open=False),
        result(5, value=2000, submitted=False, counted=False),
    ]
    assert result_lines(schedule, standing("time", results, max_time_loss_cs=2000)) == [
        "#1 MC I — 2:29.40 (+25.04s, capped to +20.00s) · Blue Falcon",
        "#2 MC I — 2:09.40 (+5.04s)",
        "#3 MC I — DNF (+20.00s)",
        "#4 MC I — not submitted",
        "#5 MC I — ~~not submitted (+20.00s)~~ (dropped)",
    ]


def test_an_uncapped_no_finish_has_no_loss_to_show():
    assert result_lines([slot(1)], standing("time", [result(1, time_cs=None, value=None)])) == ["#1 Knight — DNF"]


def test_a_player_off_the_board_reads_not_submitted_on_every_slot():
    schedule = [slot(1), slot(2)]
    assert result_lines(schedule, standing("points", None)) == [
        "#1 Knight — not submitted",
        "#2 Knight — not submitted",
    ]
    assert standing_footer(schedule, standing("points", None)) == "0 of 2 slots submitted"


def test_the_footer_counts_submissions_and_names_the_total_and_rank():
    schedule = [slot(1), slot(2), slot(3)]
    points = standing(
        "points", [result(1, score=800, value=800), result(2, submitted=False, value=0)], total=800, rank=2
    )
    assert standing_footer(schedule, points) == "1 of 3 slots submitted · total 800 · rank 2"
    timed = standing("time", [result(1, time_cs=6000, value=4508, loss_cs=4508)], total=4508, rank=1)
    assert standing_footer(schedule, timed) == "1 of 3 slots submitted · total +45.08s · rank 1"
    unranked = standing("time", [result(1, time_cs=None, value=None)])
    assert standing_footer(schedule, unranked) == "1 of 3 slots submitted"
