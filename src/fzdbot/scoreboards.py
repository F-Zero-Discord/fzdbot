"""A scoreboard as lines of Discord markdown, from the two reads that describe
it: the event detail (how the board is shaped) and the scoreboard (what is on
it). Which board to draw is decided by `group_kind` and `scoring_method`,
never by the event's name. `rank`, `total` and each result's `value` are read
from the scoreboard and nothing is ranked here. A time board shows each
player's gap to the leader, their `total` less the rank 1 row's, so the leader
reads +0.00s where `total` would carry their own losses to other players' best
slots.
"""

from dataclasses import dataclass, field
from typing import TypedDict

from fzdbot.api_types import (
    EventDetailResponse,
    EventGroupResponse,
    LobbyVoteWinnerResponse,
    ScoreboardResponse,
    ScoreboardRowResponse,
    SlotResponse,
    SlotResultResponse,
)

PODIUM = {1: "<:1st:1201576405339754546>", 2: "<:2nd:1201576409638903858>", 3: "<:3rd:1201576412444905653>"}
NOT_ENTERED = "—"
DNF = "DNF"


@dataclass
class Board:
    """One embed's worth: what the title says after the event's name, the
    lines above the standings, and the standings one line per player, a
    ranked team block first on a team event. `lines` is empty when nobody
    has a result. `group_id` is the division the board is of, and `None` on
    the one board of an event that has no divisions and on the board of
    players a division event lists with no division.
    """

    title: str
    group_id: int | None = None
    notes: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


class ScheduledEvent(TypedDict):
    """What every scheduled-event payload shares, and all a label needs."""

    starts_at: str
    event: str


def event_label(event: ScheduledEvent) -> str:
    return f"{event['event']}"


def vote_winner(slot: SlotResponse, division_id: int | None = None) -> LobbyVoteWinnerResponse | None:
    """The track one lobby voted in on a race slot. A lobby is a division, and
    `None` is the one lobby of an event that has no divisions.
    """
    return next((vote for vote in slot["vote_winners"] if vote["division_id"] == division_id), None)


def slot_name(slot: SlotResponse, division_id: int | None = None) -> str:
    """`#1 Knight`, or for a race slot with that lobby's vote in, `#3 99 (mirror Sand Ocean)`.

    The track's `name` is printed as the API answers it: it is the whole of the
    track's name, `mirror Sand Ocean` for the mirror row.
    """
    name = f"#{slot['position']} {slot['lineup_short_name']}"
    winner = vote_winner(slot, division_id)
    if winner:
        name += f" ({winner['track']['name']})"
    return name


def format_time(time_cs: int) -> str:
    minutes, rest = divmod(time_cs, 6000)
    seconds, centiseconds = divmod(rest, 100)
    return f"{minutes}:{seconds:02d}.{centiseconds:02d}"


def format_loss(loss_cs: int) -> str:
    seconds, centiseconds = divmod(loss_cs, 100)
    return f"+{seconds}.{centiseconds:02d}s"


def render_boards(
    detail: EventDetailResponse, scoreboard: ScoreboardResponse, *, podium: bool = False, debug: bool = False
) -> list[Board]:
    """One board per division on a division event, since four divisions in one
    embed exceed its field limits; one board otherwise, a team event included,
    where the teams are ranked above the individuals on the single board.
    Players a division event lists with no division get a board of their own,
    after the divisions, and only when there are any. `podium` swaps the top
    three ranks for medal emotes and rules a line under them. `debug` appends
    each player's submission count and per-slot results to their line.
    """
    groups = {group["group_id"]: group for group in detail["groups"]}
    if scoreboard["group_kind"] == "division":
        boards = [
            _board(
                detail,
                scoreboard,
                group_label(group),
                [r for r in scoreboard["rows"] if r["group_id"] == group_id],
                podium,
                debug,
                group_id=group_id,
            )
            for group_id, group in groups.items()
        ]
        unassigned = [row for row in scoreboard["rows"] if row["group_id"] not in groups]
        if unassigned:
            boards.append(_board(detail, scoreboard, "No division", unassigned, podium, debug))
        return boards

    return [_board(detail, scoreboard, "", scoreboard["rows"], podium, debug)]


def group_label(group: EventGroupResponse) -> str:
    """The alternative name where staff set one, else the name."""
    return group["alt_name"] or group["name"]


def _board(
    detail: EventDetailResponse,
    scoreboard: ScoreboardResponse,
    title: str,
    rows: list[ScoreboardRowResponse],
    podium: bool,
    debug: bool,
    *,
    group_id: int | None = None,
) -> Board:
    timed = scoreboard["scoring_method"] == "time"
    slots = detail["slots"]
    multipliers = {slot["slot_id"]: slot["multiplier"] for slot in slots}
    board = Board(title, group_id, notes=_notes(scoreboard, timed))
    if not rows:
        return board

    groups = {group["group_id"]: group for group in detail["groups"]}
    if scoreboard["group_kind"] == "team":
        for team in scoreboard["team_totals"]:
            group = groups[team["group_id"]]
            name = f"{group['emote'] or ''} {group['name']}".strip()
            board.lines.append(f"**{_rank(team['rank'], podium)} {name} - {_total(team['total'], timed)}**")
        board.lines.append("\n INDIVIDUAL RESULTS:")

    leader_total = next((row["total"] for row in rows if row["rank"] == 1), None)
    below_podium = False
    for row in rows:
        if podium and not below_podium and (row["rank"] is None or row["rank"] > 3):
            board.lines.append("======================")
            below_podium = True
        prefix = _rank(row["rank"], podium)
        if scoreboard["group_kind"] == "team":
            prefix = _team_prefix(groups, row["group_id"])
        if timed:
            line = f"{prefix} **{row['display_name']}** - **{_gap(row['total'], leader_total)}**"
        else:
            line = f"{prefix} **{row['display_name']}** - **{_total(row['total'], timed)}**"
        if debug and slots:
            tokens = [
                _token(r, multipliers[slot_id], timed) for r in row["results"] if (slot_id := r["slot_id"]) is not None
            ]
            line += f" [{row['submissions']}/{len(slots)}] " + " · ".join(tokens).rstrip(" ·")
        board.lines.append(line)
    return board


def _notes(scoreboard: ScoreboardResponse, timed: bool) -> list[str]:
    notes = []
    if timed:
        cap = scoreboard["max_time_loss_cs"]
        capped = (
            f" Max time loss per track is +{cap / 100:g} sec." if cap is not None else " There is no maximum time loss."
        )
        notes.append(
            f"*This event is scored by your submitted time.{capped}"
            " Your score is how many seconds behind the leader you are.*"
        )
    if scoreboard["machine_counts_once"]:
        notes.append("*Machine Mastery: each machine counts once, best score kept; the rest shown ~~struck~~*")
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


def _gap(total: int | None, leader_total: int | None) -> str:
    if total is None or leader_total is None:
        return NOT_ENTERED
    return format_loss(total - leader_total)


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
