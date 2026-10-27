from dataclasses import dataclass, field
from datetime import UTC, datetime

from fzdbot.api_types import (
    RegistrationEventResponse,
    RegistrationGroupResponse,
    StatOptionResponse,
    TournamentSummaryResponse,
)
from fzdbot.fzd_api import FzdApi
from fzdbot.utils.view_utils import DivTeam


def naive_utc(value: str) -> datetime:
    """An API instant as the naive UTC datetime this module compares against.

    `datetime.now()` and `datetime.timestamp()` both read a naive value as
    local time, and every datetime here is compared or formatted by one of
    them, so the offset is dropped rather than carried. Carrying it would
    make `registration_opens_at > datetime.now()` raise instead of answer.
    """
    return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None)


def instant_to_naive_utc(value: str | None) -> datetime | None:
    return naive_utc(value) if value is not None else None


def option_id(options: list[StatOptionResponse], text: str | None) -> int | None:
    """The id of the option carrying `text`, or None.

    The API answers a stored questionnaire answer as text and offers the
    options it came from; the dropdowns work in ids. An answer whose text is
    no longer in its list gives None, and the screen asks again.
    """
    if text is None:
        return None
    return next((option["id"] for option in options if option["text"] == text), None)


def is_full(capacity: int | None, num_registered: int) -> bool:
    """A NULL capacity is uncapped: there is no number to reach, so it is never full."""
    return capacity is not None and num_registered >= capacity


@dataclass
class Group:
    id: int
    scheduled_event_id: int
    name: str
    alt_name: str | None
    capacity: int | None
    num_registered: int
    emote: str | None

    @property
    def at_capacity(self) -> bool:
        return is_full(self.capacity, self.num_registered)


class Division(Group):
    pass


WAITLIST_DIVISION_NAME = "Waitlist"


def without_idle_waitlist(divisions: list[Division]) -> list[Division]:
    """The divisions a player is offered: the waitlist only once the rest are full.

    The API lists an event's waitlist as one more division, named
    `WAITLIST_DIVISION_NAME`, beside the ones it waits for. Offered as a
    peer, it is a choice nobody should make while a real division has room,
    so it is held back until every other division is at capacity. Dropping
    it leaves such an event with one division, which the screens treat as
    no choice to make. Delete this function to offer every division the API
    lists.
    """
    others = [division for division in divisions if division.name != WAITLIST_DIVISION_NAME]
    if others and not all(division.at_capacity for division in others):
        return others
    return divisions


class Team(Group):
    pass


def _group_from_api[G: Group](cls: type[G], group: RegistrationGroupResponse, scheduled_event_id: int) -> G:
    return cls(
        id=group["group_id"],
        scheduled_event_id=scheduled_event_id,
        name=group["name"],
        alt_name=group["alt_name"],
        capacity=group["capacity"],
        num_registered=group["registered"],
        emote=group["emote"],
    )


@dataclass
class Event:
    scheduled_event_id: int
    event_name: str
    tournament: TournamentSummaryResponse | None
    starts_at: datetime
    registration_open: bool
    registration_opens_at: datetime | None
    registration_closes_at: datetime | None
    divisions: list[Division] = field(default_factory=list)
    teams: list[Team] = field(default_factory=list)

    @property
    def registration_not_started(self) -> bool:
        """False with no opening time entered: nothing says it is still to come."""
        return self.registration_opens_at is not None and self.registration_opens_at > datetime.now()

    @property
    def registration_closed(self) -> bool:
        """True with no closing time entered."""
        return self.registration_closes_at is None or self.registration_closes_at < datetime.now()

    @property
    def at_capacity(self) -> bool:
        groups = self.divisions or self.teams
        if not groups:
            raise AttributeError("Event object has no division or team defined.")
        return all(group.at_capacity for group in groups)

    @property
    def has_solo_division(self) -> bool:
        """A single division is a registration with no choice to make: the
        screens skip the picker and name no division.
        """
        if self.divisions and self.teams:
            raise ValueError("An event can have teams or divisions, but not both.")
        return len(self.divisions) == 1

    @staticmethod
    def from_api(event: RegistrationEventResponse, tournaments: dict[str, TournamentSummaryResponse]) -> "Event":
        """An event runs on divisions or on teams, so the other list stays
        empty and `div_or_team` reads which from that. The event row names its
        tournament by slug only; `tournaments`, keyed by slug, supplies the row.
        A weekly flagged for registration belongs to none.
        """
        scheduled_event_id = event["scheduled_event_id"]
        slug = event["tournament"]
        self = Event(
            scheduled_event_id=scheduled_event_id,
            event_name=event["event"],
            tournament=tournaments[slug] if slug is not None else None,
            starts_at=naive_utc(event["starts_at"]),
            registration_open=event["registration_open"],
            registration_opens_at=instant_to_naive_utc(event["registration_opens_at"]),
            registration_closes_at=instant_to_naive_utc(event["registration_closes_at"]),
        )
        if event["group_kind"] == "team":
            self.teams = [_group_from_api(Team, group, scheduled_event_id) for group in event["groups"]]
        else:
            divisions = [_group_from_api(Division, group, scheduled_event_id) for group in event["groups"]]
            self.divisions = without_idle_waitlist(divisions)
        return self

    def div_or_team(self) -> DivTeam:
        if self.divisions:
            return DivTeam.DIVISION
        if self.teams:
            return DivTeam.TEAM
        raise ValueError("Event must have either divisions or teams, not neither.")


@dataclass
class UserRegistrations:
    """The group this player holds in each event they are registered for, by event id."""

    group_ids: dict[int, int] = field(default_factory=dict)

    def is_registered(self, scheduled_event_id: int) -> bool:
        return scheduled_event_id in self.group_ids

    @staticmethod
    def from_api(events: list[RegistrationEventResponse]) -> "UserRegistrations":
        """Read off the same payload the events came from: each event carries
        this player's registration or null, so there is no second call.
        """
        return UserRegistrations(
            {
                event["scheduled_event_id"]: event["your_registration"]["group_id"]
                for event in events
                if event["your_registration"] is not None
            }
        )


@dataclass
class UserStats:
    scheduled_event_id: int
    self_eval_id: int | None
    most_recent_id: int | None

    @staticmethod
    async def load_from_api(
        api: FzdApi, discord_user_id: int, scheduled_event_id: int
    ) -> tuple["UserStats", list[StatOptionResponse], list[StatOptionResponse]]:
        """This player's answers for one event, and the two lists a form offers.

        An answer the player gave for some other event arrives filled in
        here, exactly as the database read it filled it in, and counts as
        complete — which is what decides whether the screen appears at all.
        `answered_for_this_event` is what tells the two apart and is
        deliberately not consulted.
        """
        body = await api.evaluations(discord_user_id, scheduled_event_id)
        self_eval_options = body["options"]["self_evaluation"]
        recent_options = body["options"]["most_recent_event"]
        stats = UserStats(
            scheduled_event_id,
            self_eval_id=option_id(self_eval_options, body["self_evaluation"]),
            most_recent_id=option_id(recent_options, body["most_recent_event"]),
        )
        return stats, recent_options, self_eval_options

    async def save_to_api(self, api: FzdApi, discord_user_id: int, discord_user_name: str, tag: str) -> bool:
        """Store both answers against this event. False when there was nothing
        complete to store: the write takes both answers, and the screen's
        Continue button is disabled until both are chosen.
        """
        if not (self.self_eval_id and self.most_recent_id):
            return False
        await api.save_evaluations(
            discord_user_id,
            discord_user_name,
            tag,
            self.scheduled_event_id,
            self.self_eval_id,
            self.most_recent_id,
        )
        return True
