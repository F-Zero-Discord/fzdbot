from typing import Literal, Self
from datetime import datetime, timezone
import discord
from fzdbot.utils.view_utils import DivTeam, discord_timestamp


def instant_to_naive_utc(value: str | None) -> datetime | None:
    """ An API instant as the naive UTC datetime this module compares against.

        `datetime.now()` and `datetime.timestamp()` both read a naive value as
        local time, and every datetime here is compared or formatted by one of
        them, so the offset is dropped rather than carried. Carrying it would
        make `reg_open > datetime.now()` raise instead of answer.
    """
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def option_id(options: list[dict], text: str | None) -> int | None:
    """ The id of the option carrying `text`, or None.

        The API answers a stored questionnaire answer as text and offers the
        options it came from; the dropdowns work in ids. An answer whose text is
        no longer in its list gives None, and the screen asks again.
    """
    if text is None:
        return None
    return next((option["id"] for option in options if option["text"] == text), None)


def is_full(capacity: int | None, num_registered: int) -> bool:
    """ Whether a division or team has no room left.

        Capacity is nullable, and a NULL one is uncapped: there is no number
        to reach, so it is never full.
    """
    return capacity is not None and num_registered >= capacity


class Event():
    def __init__(self):
        self.event_id: int | None = None
        self.scheduled_event_id: int | None = None
        self.event_name: str | None = None
        self.description: str | None = None
        self.mode: Literal["99", "classic"] | None = None
        self.scoring: Literal["points", "placement"] | None = None
        self.machine_required: bool = False
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None
        self.divisions: list[Division] | None = []
        self.teams: list[Team] | None = []
        self.reg_period_id: int | None = None
        self.reg_open: datetime | None = None
        self.reg_close: datetime | None = None

    @property
    def duration(self) -> int | None:
        if self.start_time and self.end_time:
            return int(round((self.end_time - self.start_time).total_seconds() / 3600, 0))
        else:
            return None

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
        if not self.divisions and not self.teams:
            raise AttributeError("Event object has no division or team defined.")
        if self.divisions:
            capacity_list = []
            for division in self.divisions:
                capacity_list.append(division.at_capacity)
            if all(capacity_list):
                return True
            else:
                return False
        if self.teams:
            capacity_list = []
            for team in self.teams:
                capacity_list.append(team.at_capacity)
            if all(capacity_list):
                return True
            else:
                return False

    @property
    def has_solo_division(self) -> bool:
        if self.divisions and self.teams:
            raise ValueError(f"An event can have teams or divisions, but not both.")
        if not self.divisions:
            return False
        elif len(self.divisions) == 1:
            return True
        else:
            return False


    def __repr__(self) -> str:
        return f"""Event(event_id='{self.event_id}'\n\
                scheduled_event_id='{self.scheduled_event_id}'\n\
                event_name='{self.event_name}'\n\
                description='{self.description}'\n\
                mode='{self.mode}'\n\
                scoring='{self.scoring}'\n\
                machine_required='{self.machine_required}'\n\
                start_time='{self.start_time}'\n\
                end_time='{self.end_time}'\n\
                divisions='{self.divisions}'\n\
                teams='{self.teams}'\n\
                reg_period_id='{self.reg_period_id}'\n\
                reg_open='{self.reg_open}'\n\
                reg_close='{self.reg_close}'\n\
                )"""

    # Make this a __format__ dunder method    
    def __format__(self, format_spec: str) -> Literal["detail"] | None:
        """Provides a detailed description of an event."""
        match format_spec:
            case "detail":
                if not self:
                    return None
                else:
                    event_string = ""
                    event_string += f"### {self.event_name}\n"
                    event_string += f"**Description:** {self.description if self.description is not None else "None"}\n"
                    event_string += f"**Mode:** {self.mode}   **Scoring:** {self.scoring}\n"
                    event_string += f"**Requires users to enter machine when scoring?:** {'Yes' if self.machine_required is self.machine_required else 'No'}\n"
                    event_string += f"**Event Start:** {discord_timestamp(self.start_time, 'long')}\n"
                    event_string += f"**Event End:** {discord_timestamp(self.end_time, 'long')}\n"
                    if self.teams:
                        event_string += f"**Teams:**\n"
                        for team in self.teams:
                            event_string += f"{team:detail}"
                    if self.divisions:
                        # Assume that if only division has same name as event that it is silent division
                        if (len(self.divisions) > 1) and (self.divisions[0].name != self.event_name):
                            event_string += f"**Divisions:**\n"
                            for division in self.divisions:
                                event_string += f"{division:detail}"
                    event_string += f"**Registration Window:**\n"
                    event_string += f"\t**Registration Opens:** {discord_timestamp(self.reg_open, 'long')}\n"
                    event_string += f"\t**Registration Closes:** {discord_timestamp(self.reg_close, 'long')}\n"

                    return event_string

            case _:
                raise ValueError("Unknown format specifier...")
                











    @staticmethod
    def from_api(event: dict) -> "Event":
        """ One event object from `GET /v1/players/{id}/registrations`.

            The whole screen in one payload: the event, its groups, and each
            group's capacity and headcount. An event runs on divisions or on
            teams, so the other list stays empty and `div_or_team` reads which
            from that.
        """
        self = Event()
        self.scheduled_event_id = event["scheduled_event_id"]
        self.event_name = event["display_name"] or event["event"]
        self.description = event["description"]
        self.mode = event["mode"]
        self.scoring = event["scoring_method"]
        self.machine_required = event["machine_input_required"]
        self.start_time = instant_to_naive_utc(event["starts_at"])
        self.end_time = instant_to_naive_utc(event["ends_at"])
        self.reg_open = instant_to_naive_utc(event["registration_opens_at"])
        self.reg_close = instant_to_naive_utc(event["registration_closes_at"])

        groups = [
            _group_from_api(group, event["group_kind"], event["scheduled_event_id"])
            for group in event["groups"]
        ]
        if event["group_kind"] == "team":
            self.teams, self.divisions = groups, []
        else:
            self.divisions, self.teams = groups, []
        return self




    def div_or_team(self) -> str:
        """ Returns string "division" or "team" depending on the whether the event has 
            divisions or teams.
        """
        if self.divisions:
            div_team_str = "division"
        elif self.teams:
            div_team_str = "team"
        else:
            raise ValueError(f"Event must have either divisions or teams, not neither.")

        return div_team_str


class Division():
    def __init__(self):
        self.id: int | None = None
        self.scheduled_event_id = int | None
        self.name: str | None = None
        self.alt_name: str | None = None
        self.capacity: int | None = None
        self.num_registered: int | None = None
        self.emote: str | None = None


    @property
    def at_capacity(self) -> bool:
        return is_full(self.capacity, self.num_registered)
        

    def __repr__(self) -> str:
        return f"""Division(id='{self.id}'\n\
                scheduled_event_id='{self.scheduled_event_id}'\n\
                name='{self.name}'\n\
                alt_name='{self.alt_name}'\n\
                capacity='{self.capacity}'\n\
                emote='{self.emote}'\n\
                )"""
                
    
    def __format__(self, format_spec: str) -> Literal["detail"] | None:
        """Provides a detailed description of a division."""
        match format_spec:
            case "detail":
                if not self:
                    return None
                else:
                    division_string = ""
                    division_string += f"\t**Name:** {self.name}\n"
                    division_string += f"\t\t**Alternate Name:** {self.alt_name}\n"
                    division_string += f"\t\t**Emote:** {self.emote}\n"
                    division_string += f"\t\t**Capacity:** {self.capacity or 'no cap'}\n"
                    return division_string
            case _:
                raise ValueError("Unknown format specifier...")
            
        


class Team():
    def __init__(self):
        self.id: int | None = None
        self.scheduled_event_id = int | None
        self.name: str | None = None
        self.alt_name: str | None = None
        self.capacity: int | None = None
        self.num_registered: int | None = None
        self.emote: str | None = None


    @property
    def at_capacity(self) -> bool:
        return is_full(self.capacity, self.num_registered)
    

    def __repr__(self) -> str:
        return f"""Team(id='{self.id}'\n\
                scheduled_event_id='{self.scheduled_event_id}'\n\
                name='{self.name}'\n\
                alt_name='{self.alt_name}'\n\
                capacity='{self.capacity}'\n\
                emote='{self.emote}'\n\
                )"""


    def __format__(self, format_spec: str) -> Literal["detail"] | None:
        """Provides a detailed description of a team."""
        match format_spec:
            case "detail":
                if not self:
                    return None
                else:
                    team_string = ""
                    team_string += f"\t**Name:** {self.name}\n"
                    team_string += f"\t\t**Alternate Name:** {self.alt_name}\n"
                    team_string += f"\t\t**Emote:** {self.emote}\n"
                    team_string += f"\t\t**Capacity:** {self.capacity}\n"
                    return team_string
            case _:
                raise ValueError("Unknown format specifier...")
            







    



def _group_from_api(group: dict, kind: str | None, scheduled_event_id: int) -> "Division | Team":
    """ One division or team, with the headcount the API counted."""
    div_team = Team() if kind == "team" else Division()
    div_team.id = group["group_id"]
    div_team.scheduled_event_id = scheduled_event_id
    div_team.name = group["name"]
    div_team.alt_name = group["alt_name"]
    div_team.emote = group["emote"]
    div_team.capacity = group["capacity"]
    div_team.num_registered = group["registered"]
    return div_team


class UserRegistrations():
    def __init__(self, interaction: discord.Interaction):
        self.discord_user_id: str = interaction.user.name
        self.registrations: list[dict] | None = None
        """ self.registrations dictionary format:
                {scheduled_event_id: int,
                type: Literal["division", "team],
                div_team_id: int}
            Note that waitlist status in div_team_id not currently implemented
        """

    def __format__(self, format_spec: str) -> Literal["detail"] | None:
            """Provides a detailed description of an event."""
            match format_spec:
                case "detail":
                    if not self:
                        return None
                    else:
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
    

    def is_registered(self, scheduled_event_id):
        if self.registrations:
            if any(r.get("scheduled_event_id") == scheduled_event_id for r in self.registrations):
                return True
            else:
                return False
        else:
            return False
        

    @staticmethod
    def from_api(interaction: discord.Interaction, events: list[dict]) -> "UserRegistrations":
        """ Where this player stands, read off the same payload the events came
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


class UserStats():
    def __init__(self):
        self.scheduled_event_id: int | None = None
        self.self_eval_id: int | None = None # enum
        self.most_recent_id: int | None = None # enum


    @staticmethod
    async def load_from_api(api, discord_user_id: int,
                            scheduled_event_id: int) -> tuple[Self, list[dict], list[dict]]:
        """ This player's answers for one event, and the two lists a form offers.

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


    async def save_to_api(self, api, discord_user_id: int,
                          discord_user_name: str, tag: str) -> bool:
        """ Store both answers against this event. False when there was nothing
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


# Dummy event for testing
from fzdbot.utils.view_utils import time_string_to_datetime
