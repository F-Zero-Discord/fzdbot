from fzdbot.scoreboards import Board, format_loss, format_time, render_boards


def slot(slot_id, position, short, multiplier=1):
    return {
        "slot_id": slot_id,
        "position": position,
        "lineup_short_name": short,
        "multiplier": multiplier,
        "vote_winner": None,
        "vote_winners": [],
    }


def result(slot_id, *, score=None, time_cs=None, value=None, submitted=True, counted=True, open=True):
    return {
        "slot_id": slot_id,
        "score": score,
        "time_cs": time_cs,
        "value": value,
        "submitted": submitted,
        "counted": counted,
        "open": open,
    }


def row(name, results, *, total, rank, group_id=None, submissions=None, emote=None):
    return {
        "display_name": name,
        "emote": emote,
        "group_id": group_id,
        "results": results,
        "submissions": sum(r["submitted"] for r in results) if submissions is None else submissions,
        "total": total,
        "rank": rank,
    }


def detail(*, group_kind=None, groups=(), slots=(), num_mulligans=0, max_time_loss_cs=None, method="points"):
    return {
        "scheduled_event_id": 1,
        "event": "Ashes",
        "display_name": None,
        "starts_at": "2026-09-26T18:00:00Z",
        "scoring_method": method,
        "group_kind": group_kind,
        "groups": list(groups),
        "slots": list(slots),
        "scoring": {"num_mulligans": num_mulligans, "max_time_loss_cs": max_time_loss_cs},
    }


def scoreboard(d, rows, *, division_id=None, team_id=None, team_totals=()):
    return {
        "scheduled_event_id": 1,
        "group_kind": d["group_kind"],
        "scoring_method": d["scoring_method"],
        "filter": {"division_id": division_id, "team_id": team_id},
        "num_mulligans": d["scoring"]["num_mulligans"],
        "max_time_loss_cs": d["scoring"]["max_time_loss_cs"],
        "rows": rows,
        "team_totals": list(team_totals),
    }


def test_time_and_loss_formats():
    assert format_time(9044) == "1:30.44"
    assert format_time(5) == "0:00.05"
    assert format_loss(52) == "+0.52"
    assert format_loss(6000) == "+60.00"


def test_points_board_marks_multiplier_drops_dnf_and_not_entered():
    d = detail(
        slots=[slot(1, 1, "Knight"), slot(2, 2, "MP", 3), slot(3, 3, "Queen"), slot(4, 4, "King")],
        num_mulligans=1,
    )
    rows = [
        row(
            "Pilot",
            [
                result(1, score=300),
                result(2, score=100, value=300),
                result(3, submitted=False, counted=False),
                result(4, score=None),
            ],
            total=600,
            rank=1,
        )
    ]
    boards = render_boards(d, scoreboard(d, rows))

    assert boards == [
        Board(
            "",
            notes=["#1 Knight · #2 MP ×3 · #3 Queen · #4 King", "*Lowest 1 result dropped, shown ~~struck~~*"],
            lines=["1\\. **Pilot** - **600** [3/4] 300 · 100 ×3 · ~~—~~ · DNF"],
        )
    ]


def test_time_board_shows_loss_cap_and_leaves_an_unopened_slot_blank():
    d = detail(slots=[slot(1, 1, "MC"), slot(2, 2, "BB"), slot(3, 3, "SO")], max_time_loss_cs=2000, method="time")
    rows = [
        row(
            "Leader",
            [
                result(1, time_cs=9044, value=0),
                result(2, submitted=False, value=2000),
                result(3, submitted=False, open=False, value=0),
            ],
            total=2000,
            rank=1,
        ),
        row(
            "Chaser",
            [
                result(1, time_cs=9096, value=52),
                result(2, time_cs=None, value=2000),
                result(3, submitted=False, open=False, value=0),
            ],
            total=2052,
            rank=2,
        ),
    ]
    boards = render_boards(d, scoreboard(d, rows))

    assert boards[0].notes[1] == "*Loss to the slot's leader as +s.cc; DNF and — count +20.00*"
    assert boards[0].lines == [
        "1\\. **Leader** - **+20.00** [1/3] 1:30.44 +0.00 · — +20.00",
        "2\\. **Chaser** - **+20.52** [2/3] 1:30.96 +0.52 · DNF +20.00",
    ]


def test_division_event_unfiltered_is_one_board_per_division_in_order():
    groups = [
        {"group_id": 27, "name": "Ashes - Standard", "alt_name": "Standard", "emote": None, "display_order": 2},
        {"group_id": 26, "name": "Novice", "alt_name": None, "emote": None, "display_order": 1},
    ]
    d = detail(group_kind="division", groups=groups, slots=[slot(1, 1, "Knight")])
    rows = [
        row("Ann", [result(1, score=10, value=10)], total=10, rank=1, group_id=27),
        row("Bob", [result(1, score=20, value=20)], total=20, rank=1, group_id=26),
        row("Cid", [result(1, score=5, value=5)], total=5, rank=1, group_id=None),
    ]
    boards = render_boards(d, scoreboard(d, rows))

    assert [b.title for b in boards] == ["Standard", "Novice", "No division"]
    assert [b.lines for b in boards] == [
        ["1\\. **Ann** - **10** [1/1] 10"],
        ["1\\. **Bob** - **20** [1/1] 20"],
        ["1\\. **Cid** - **5** [1/1] 5"],
    ]

    narrowed = render_boards(d, scoreboard(d, rows[1:2], division_id=26))
    assert [(b.title, b.lines) for b in narrowed] == [("Novice", ["1\\. **Bob** - **20** [1/1] 20"])]


def test_team_event_ranks_teams_above_individuals():
    groups = [
        {"group_id": 12, "name": "Blue", "alt_name": None, "emote": "<:b:1>", "display_order": None},
        {"group_id": 13, "name": "Green", "alt_name": None, "emote": None, "display_order": None},
    ]
    d = detail(group_kind="team", groups=groups, slots=[slot(1, 1, "Yahtzee")])
    rows = [
        row("Ann", [result(1, score=90, value=90)], total=90, rank=1, group_id=13),
        row("Bob", [result(1, score=50, value=50)], total=50, rank=2, group_id=12),
        row("Cid", [result(1, score=45, value=45)], total=45, rank=3, group_id=12),
    ]
    totals = [{"group_id": 12, "total": 95, "rank": 1}, {"group_id": 13, "total": 90, "rank": 2}]
    boards = render_boards(d, scoreboard(d, rows, team_totals=totals))

    assert boards[0].lines == [
        "**1\\. <:b:1> Blue - 95**",
        "**2\\. Green - 90**",
        "\n INDIVIDUAL RESULTS:",
        "Green: **Ann** - **90** [1/1] 90",
        "<:b:1>: **Bob** - **50** [1/1] 50",
        "<:b:1>: **Cid** - **45** [1/1] 45",
    ]


def test_a_narrowed_board_is_titled_by_its_group():
    groups = [{"group_id": 25, "name": "Ashes", "alt_name": None, "emote": None, "display_order": 1}]
    d = detail(group_kind="division", groups=groups)
    assert [b.title for b in render_boards(d, scoreboard(d, [], division_id=25))] == ["Ashes"]


def test_weekly_with_no_slots_shows_totals_only():
    d = detail()
    rows = [row("Ann", [result(None, score=800, value=800)] * 3, total=2400, rank=1)]
    boards = render_boards(d, scoreboard(d, rows))

    assert boards == [Board("", notes=[], lines=["1\\. **Ann** - **2400**"])]


def test_nobody_on_the_board():
    d = detail(slots=[slot(1, 1, "Knight")])
    assert render_boards(d, scoreboard(d, [])) == [Board("", notes=["#1 Knight"])]
