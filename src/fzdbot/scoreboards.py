"""A scoreboard as lines of Discord markdown, from the two reads that describe
it: the event detail (how the board is shaped) and the scoreboard (what is on
it). Which board to draw is decided by `group_kind` and `scoring_method`,
never by the event's name; every value shown, including `rank`, is read from
the scoreboard and nothing is summed or ranked here.
"""

from dataclasses import dataclass, field

from fzdbot.api_types import (
    EventDetailResponse,
    EventGroupResponse,
    EventResponse,
    LobbyVoteWinnerResponse,
    ScoreboardResponse,
    ScoreboardRowResponse,
    SlotResponse,
    SlotResultResponse,
    SlotTrackResponse,
    TrackResponse,
)

PODIUM = {1: "<:1st:1201576405339754546>", 2: "<:2nd:1201576409638903858>", 3: "<:3rd:1201576412444905653>"}
NOT_ENTERED = "—"
DNF = "DNF"


@dataclass
class Board:
    """One embed's worth: what the title says after the event's name, the
    lines above the standings, and the standings one line per player, a
    ranked team block first on a team event. `lines` is empty when nobody
    has a result.
    """

    title: str
    notes: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def event_label(event: EventResponse | EventDetailResponse) -> str:
    return event["display_name"] or event["event"]


def track_label(track: TrackResponse | SlotTrackResponse | LobbyVoteWinnerResponse) -> str:
    """`mirror Big Blue (mBB)`, `Big Blue (BB)`. A name is shared by a standard,
    a mirror and a classic track, so the type is said wherever it is not
    standard, in words and again as the short name where the track has one.
    """
    if "track_name" in track:
        return _track_label(track["track_name"], track["track_type"], track["track_short_name"])
    return _track_label(track["name"], track["type"], track["short_name"])


def _track_label(name: str, kind: str, short_name: str | None) -> str:
    label = name if kind == "standard" else f"{kind} {name}"
    return f"{label} ({short_name})" if short_name else label


def vote_winner(slot: SlotResponse, division_id: int | None = None) -> LobbyVoteWinnerResponse | None:
    """The track one lobby voted in on a race slot. A lobby is a division, and
    `None` is the one lobby of an event that has no divisions.
    """
    return next((vote for vote in slot["vote_winners"] if vote["division_id"] == division_id), None)


def slot_name(slot: SlotResponse, division_id: int | None = None) -> str:
    """`#1 Knight`, or for a race slot with that lobby's vote in, `#3 99 (mSO)`:
    the winner by its short name, and by its name where it has none.
    """
    name = f"#{slot['position']} {slot['lineup_short_name']}"
    winner = vote_winner(slot, division_id)
    if winner:
        name += f" ({winner['track_short_name'] or winner['track_name']})"
    return name


def format_time(time_cs: int) -> str:
    minutes, rest = divmod(time_cs, 6000)
    seconds, centiseconds = divmod(rest, 100)
    return f"{minutes}:{seconds:02d}.{centiseconds:02d}"


def format_loss(loss_cs: int) -> str:
    seconds, centiseconds = divmod(loss_cs, 100)
    return f"+{seconds}.{centiseconds:02d}"


def render_boards(detail: EventDetailResponse, scoreboard: ScoreboardResponse, *, podium: bool = False) -> list[Board]:
    """One board per division when a division event is read unfiltered, since
    four divisions in one embed exceed its field limits; otherwise one board.
    Players a division event lists with no division get a board of their own,
    after the divisions, and only when there are any. `podium` swaps the top
    three ranks for medal emotes and rules a line under them.
    """
    groups = {group["group_id"]: group for group in detail["groups"]}
    if scoreboard["group_kind"] == "division" and scoreboard["filter"]["division_id"] is None:
        boards = [
            _board(
                detail,
                scoreboard,
                _title(group),
                [r for r in scoreboard["rows"] if r["group_id"] == group_id],
                podium,
                division_id=group_id,
            )
            for group_id, group in groups.items()
        ]
        unassigned = [row for row in scoreboard["rows"] if row["group_id"] not in groups]
        if unassigned:
            boards.append(_board(detail, scoreboard, "No division", unassigned, podium))
        return boards

    named = scoreboard["filter"]["division_id"] or scoreboard["filter"]["team_id"]
    title = _title(groups[named]) if named in groups else ""
    return [
        _board(detail, scoreboard, title, scoreboard["rows"], podium, division_id=scoreboard["filter"]["division_id"])
    ]


def _title(group: EventGroupResponse) -> str:
    return group["alt_name"] or group["name"]


def _board(
    detail: EventDetailResponse,
    scoreboard: ScoreboardResponse,
    title: str,
    rows: list[ScoreboardRowResponse],
    podium: bool,
    division_id: int | None = None,
) -> Board:
    """`division_id` is the lobby whose vote winners head the race slots."""
    timed = scoreboard["scoring_method"] == "time"
    slots = detail["slots"]
    multipliers = {slot["slot_id"]: slot["multiplier"] for slot in slots}
    board = Board(title, notes=_notes(scoreboard, slots, timed, division_id))
    if not rows:
        return board

    groups = {group["group_id"]: group for group in detail["groups"]}
    if scoreboard["group_kind"] == "team" and scoreboard["filter"]["team_id"] is None:
        for team in scoreboard["team_totals"]:
            group = groups[team["group_id"]]
            name = f"{group['emote'] or ''} {group['name']}".strip()
            board.lines.append(f"**{_rank(team['rank'], podium)} {name} - {_total(team['total'], timed)}**")
        board.lines.append("\n INDIVIDUAL RESULTS:")

    below_podium = False
    for row in rows:
        if podium and not below_podium and (row["rank"] or 4) > 3:
            board.lines.append("======================")
            below_podium = True
        prefix = _rank(row["rank"], podium)
        if scoreboard["group_kind"] == "team":
            prefix = _team_prefix(groups, row["group_id"])
        line = f"{prefix} **{row['display_name']}** - **{_total(row['total'], timed)}**"
        if slots:
            tokens = [
                _token(r, multipliers[slot_id], timed) for r in row["results"] if (slot_id := r["slot_id"]) is not None
            ]
            line += f" [{row['submissions']}/{len(slots)}] " + " · ".join(tokens).rstrip(" ·")
        board.lines.append(line)
    return board


def _notes(
    scoreboard: ScoreboardResponse, slots: list[SlotResponse], timed: bool, division_id: int | None
) -> list[str]:
    notes = []
    if slots:
        notes.append(" · ".join(_slot_heading(slot, division_id) for slot in slots))
    if timed:
        cap = scoreboard["max_time_loss_cs"]
        capped = f"; {DNF} and {NOT_ENTERED} count {format_loss(cap)}" if cap is not None else ""
        notes.append(f"*Loss to the slot's leader as +s.cc{capped}*")
    dropped = scoreboard["num_mulligans"]
    if dropped:
        which = "result" if dropped == 1 else "results"
        notes.append(f"*Lowest {dropped} {which} dropped, shown ~~struck~~*")
    return notes


def _team_prefix(groups: dict[int, EventGroupResponse], group_id: int | None) -> str:
    """`?` for a player on no team: the result write accepts anyone, registered or not."""
    if group_id is None:
        return "?:"
    group = groups[group_id]
    return f"{group['emote'] or group['name']}:"


def _slot_heading(slot: SlotResponse, division_id: int | None) -> str:
    name = slot_name(slot, division_id)
    return f"{name} ×{slot['multiplier']}" if slot["multiplier"] != 1 else name


def _rank(rank: int | None, podium: bool) -> str:
    """Escaped, since `1.` at the start of a line is a Markdown list."""
    if rank is None:
        return "-\\."
    if podium and rank in PODIUM:
        return PODIUM[rank]
    return f"{rank}\\."


def _total(total: int | None, timed: bool) -> str:
    if total is None:
        return NOT_ENTERED
    return format_loss(total) if timed else str(total)


def _token(result: SlotResultResponse, multiplier: int, timed: bool) -> str:
    """One slot's result as a player's line shows it: the score with its
    multiplier, or the time with its loss; a DNF and a missing submission
    told apart; a time slot nobody has finished left blank.
    """
    if timed and not result["open"]:
        return ""
    if not result["submitted"]:
        shown = NOT_ENTERED
    elif timed:
        shown = format_time(result["time_cs"]) if result["time_cs"] is not None else DNF
    else:
        shown = str(result["score"]) if result["score"] is not None else DNF
    if timed:
        shown += f" {format_loss(result['value'])}" if result["value"] is not None else ""
    elif multiplier != 1:
        shown += f" ×{multiplier}"
    return shown if result["counted"] else f"~~{shown}~~"
