from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import discord

from fzdbot.utils.view_utils import DivTeam, discord_timestamp


def instant_to_naive_utc(value: str | None) -> datetime | None:
    """An API instant as the naive UTC datetime this module compares against.

    `datetime.now()` and `datetime.timestamp()` both read a naive value as
    local time, and every datetime here is compared or formatted by one of
    them, so the offset is dropped rather than carried. Carrying it would
    make `reg_open > datetime.now()` raise instead of answer.
    """
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC).replace(tzinfo=None)


def option_id(options: list[dict], text: str | None) -> int | None:
    """The id of the option carrying `text`, or None.

    The API answers a stored questionnaire answer as text and offers the
    options it came from; the dropdowns work in ids. An answer whose text is
    no longer in its list gives None, and the screen asks again.
    """
    if text is None:
        return None
    return next((option["id"] for option in options if option["text"] == text), None)


def is_full(capacity: int | None, num_registered: int) -> bool:
    """Whether a division or team has no room left.

    Capacity is nullable, and a NULL one is uncapped: there is no number
    to reach, so it is never full.
    """
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

    def __format__(self, format_spec: str) -> str:
        """Provides a detailed description of a division or team."""
        match format_spec:
            case "detail":
                group_string = ""
                group_string += f"\t**Name:** {self.name}\n"
                group_string += f"\t\t**Alternate Name:** {self.alt_name}\n"
                group_string += f"\t\t**Emote:** {self.emote}\n"
                group_string += f"\t\t**Capacity:** {self.capacity or 'no cap'}\n"
                return group_string
            case _:
                raise ValueError("Unknown format specifier...")


class Division(Group):
    pass


class Team(Group):
    pass


def _group_from_api[G: Group](cls: type[G], group: dict, scheduled_event_id: int) -> G:
    """One division or team, with the headcount the API counted."""
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
    description: str | None
    mode: Literal["99", "classic"] | None
    scoring: Literal["points", "placement"] | None
    machine_required: bool
    start_time: datetime | None
    end_time: datetime | None
    reg_open: datetime | None
    reg_close: datetime | None
    divisions: list[Division] = field(default_factory=list)
    teams: list[Team] = field(default_factory=list)

    # reg_open and reg_close are optional values. if not present, assume event open,
    #   as users only presented with events that are in the future.
    @property
    def reg_period_not_started(self) -> bool:
        if not self.reg_open:
            return False
        else:
            return self.reg_open > datetime.now()

    @property
    def reg_period_open(self) -> bool:
        if not self.reg_open or not self.reg_close:
            return True
        else:
            return (self.reg_open <= datetime.now()) and (self.reg_close > datetime.now())

    @property
    def reg_period_closed(self) -> bool:
        if not self.reg_close:
            return True
        else:
            return self.reg_close < datetime.now()

    @property
    def at_capacity(self) -> bool:
        groups = self.divisions or self.teams
        if not groups:
            raise AttributeError("Event object has no division or team defined.")
        return all(group.at_capacity for group in groups)

    @property
    def has_solo_division(self) -> bool:
        if self.divisions and self.teams:
            raise ValueError("An event can have teams or divisions, but not both.")
        return len(self.divisions) == 1

    def __format__(self, format_spec: str) -> str:
        """Provides a detailed description of an event."""
        match format_spec:
            case "detail":
                event_string = ""
                event_string += f"### {self.event_name}\n"
                event_string += f"**Description:** {self.description if self.description is not None else 'None'}\n"
                event_string += f"**Mode:** {self.mode}   **Scoring:** {self.scoring}\n"
                event_string += (
                    f"**Requires users to enter machine when scoring?:** {'Yes' if self.machine_required else 'No'}\n"
                )
                event_string += f"**Event Start:** {discord_timestamp(self.start_time, 'long')}\n"
                event_string += f"**Event End:** {discord_timestamp(self.end_time, 'long')}\n"
                if self.teams:
                    event_string += "**Teams:**\n"
                    for team in self.teams:
                        event_string += f"{team:detail}"
                # Assume that if only division has same name as event that it is silent division
                if len(self.divisions) > 1 and self.divisions[0].name != self.event_name:
                    event_string += "**Divisions:**\n"
                    for division in self.divisions:
                        event_string += f"{division:detail}"
                event_string += "**Registration Window:**\n"
                event_string += f"\t**Registration Opens:** {discord_timestamp(self.reg_open, 'long')}\n"
                event_string += f"\t**Registration Closes:** {discord_timestamp(self.reg_close, 'long')}\n"
                return event_string

            case _:
                raise ValueError("Unknown format specifier...")

    @staticmethod
    def from_api(event: dict) -> "Event":
        """One event object from `GET /v1/players/{id}/registrations`.

        The whole screen in one payload: the event, its groups, and each
        group's capacity and headcount. An event runs on divisions or on
        teams, so the other list stays empty and `div_or_team` reads which
        from that.
        """
        self = Event(
            scheduled_event_id=event["scheduled_event_id"],
            event_name=event["display_name"] or event["event"],
            description=event["description"],
            mode=event["mode"],
            scoring=event["scoring_method"],
            machine_required=event["machine_input_required"],
            start_time=instant_to_naive_utc(event["starts_at"]),
            end_time=instant_to_naive_utc(event["ends_at"]),
            reg_open=instant_to_naive_utc(event["registration_opens_at"]),
            reg_close=instant_to_naive_utc(event["registration_closes_at"]),
        )
        scheduled_event_id = event["scheduled_event_id"]
        if event["group_kind"] == "team":
            self.teams = [_group_from_api(Team, group, scheduled_event_id) for group in event["groups"]]
        else:
            self.divisions = [_group_from_api(Division, group, scheduled_event_id) for group in event["groups"]]
        return self

    def div_or_team(self) -> DivTeam:
        """Whether the event registers into divisions or into teams."""
        if self.divisions:
            return DivTeam.DIVISION
        if self.teams:
            return DivTeam.TEAM
        raise ValueError("Event must have either divisions or teams, not neither.")


class UserRegistrations:
    def __init__(self, interaction: discord.Interaction):
        self.discord_user_id: str = interaction.user.name
        self.registrations: list[dict] = []
        """ self.registrations dictionary format:
                {scheduled_event_id: int,
                type: Literal["division", "team],
                div_team_id: int}
            Note that waitlist status in div_team_id not currently implemented
        """

    def __format__(self, format_spec: str) -> str:
        """Provides a detailed description of a user's registrations."""
        match format_spec:
            case "detail":
                out_string = "UserRegistrations(\n"
                out_string += f"\tdiscord_user_id: {self.discord_user_id}\n"
                out_string += "\tregistrations:\n"
                if not self.registrations:
                    out_string += "\t\tNone\n"
                else:
                    for i, registration in enumerate(self.registrations):
                        out_string += f"\t\tRegistration {i}\n"
                        out_string += f"\t\t\tscheduled_event_id: {registration['scheduled_event_id']}\n"
                        out_string += f"\t\t\ttype: {registration['type']}\n"
                        out_string += f"\t\t\tdiv_team_id: {registration['div_team_id']}\n"
                return out_string
            case _:
                raise ValueError("Unknown format specifier...")

    def is_registered(self, scheduled_event_id: int) -> bool:
        return any(r.get("scheduled_event_id") == scheduled_event_id for r in self.registrations)

    @staticmethod
    def from_api(interaction: discord.Interaction, events: list[dict]) -> "UserRegistrations":
        """Where this player stands, read off the same payload the events came
        from.

        The API answers one event object per open event, each carrying this
        player's registration or null, so there is no second call and no
        user id to carry: the snowflake in the path is the whole identity.
        """
        self = UserRegistrations(interaction)
        self.registrations = [
            {
                "scheduled_event_id": event["scheduled_event_id"],
                "type": event["group_kind"],
                "div_team_id": event["your_registration"]["group_id"],
            }
            for event in events
            if event["your_registration"] is not None
        ]
        return self


class UserStats:
    def __init__(self):
        self.scheduled_event_id: int | None = None
        self.self_eval_id: int | None = None  # enum
        self.most_recent_id: int | None = None  # enum

    @staticmethod
    async def load_from_api(
        api, discord_user_id: int, scheduled_event_id: int
    ) -> tuple["UserStats", list[dict], list[dict]]:
        """This player's answers for one event, and the two lists a form offers.

        An answer the player gave for some other event arrives filled in
        here, exactly as the database read it filled it in, and counts as
        complete — which is what decides whether the screen appears at all.
        `answered_for_this_event` is what tells the two apart and is
        deliberately not consulted.

        Returns the stats and the two option lists, because the API answers
        all three in one call and the screen needs all three.
        """
        body = await api.evaluations(discord_user_id, scheduled_event_id)
        self_eval_options = body["options"]["self_evaluation"]
        recent_options = body["options"]["most_recent_event"]

        self = UserStats()
        self.scheduled_event_id = scheduled_event_id
        self.self_eval_id = option_id(self_eval_options, body["self_evaluation"])
        self.most_recent_id = option_id(recent_options, body["most_recent_event"])
        return self, recent_options, self_eval_options

    async def save_to_api(self, api, discord_user_id: int, discord_user_name: str, tag: str) -> bool:
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
