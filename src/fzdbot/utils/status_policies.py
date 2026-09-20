from typing import TypedDict

import discord

from fzdbot.utils.event_class import Event, UserRegistrations
from fzdbot.utils.view_utils import DivTeam, NextStep, discord_timestamp


class EventStatus(TypedDict):
    """The menu line and the button for one event, given where the player stands."""

    label: str
    button_label: str
    button_color: discord.ButtonStyle
    button_disabled: bool
    next_step: NextStep


def user_event_status(event: Event, user: UserRegistrations) -> EventStatus:
    # Case: user is registered
    #   Note: Present logic allows user to edit a registration after the
    #       registration period closes
    if user.is_registered(event.scheduled_event_id):
        if event.has_solo_division:
            status: EventStatus = {
                "label": "Registered",
                "button_label": "Withdraw",
                "button_color": discord.ButtonStyle.red,
                "button_disabled": False,
                "next_step": NextStep.WITHDRAW_CONF,
            }
        else:
            status: EventStatus = {
                "label": "Registered",
                "button_label": "Edit",
                "button_color": discord.ButtonStyle.blurple,
                "button_disabled": False,
                "next_step": NextStep.EDIT,
            }
        return status

    # Case: user not registered, but registration is open
    if (not user.is_registered(event.scheduled_event_id)) and (event.registration_open) and (not event.at_capacity):
        if event.has_solo_division:
            status: EventStatus = {
                "label": "Registration Open!",
                "button_label": "Register",
                "button_color": discord.ButtonStyle.green,
                "button_disabled": False,
                "next_step": NextStep.CONFIRM,
            }
        else:
            status: EventStatus = {
                "label": "Registration Open!",
                "button_label": "Register",
                "button_color": discord.ButtonStyle.green,
                "button_disabled": False,
                "next_step": NextStep.ADD,
            }
        return status

    # Case: user not registered, but registration not yet open
    if (not user.is_registered(event.scheduled_event_id)) and (event.registration_not_started):
        status: EventStatus = {
            "label": f"Registration opens {discord_timestamp(event.registration_opens_at, 'long')}",
            "button_label": "Register",
            "button_color": discord.ButtonStyle.green,
            "button_disabled": True,
            "next_step": NextStep.NULL,
        }
        return status

    # Case: user not registered, but registration has closed
    if (not user.is_registered(event.scheduled_event_id)) and (event.registration_closed):
        status: EventStatus = {
            "label": "Registration period has ended",
            "button_label": "-----",
            "button_color": discord.ButtonStyle.gray,
            "button_disabled": True,
            "next_step": NextStep.NULL,
        }
        return status

    # Remaining case: user not registered, registration period has not closed, but event is full
    #   Note: to be modified if waitlist implemented
    return {
        "label": "Event Full!",
        "button_label": "-----",
        "button_color": discord.ButtonStyle.gray,
        "button_disabled": True,
        "next_step": NextStep.NULL,
    }


def registered_summary(events: list[Event], user: UserRegistrations) -> list[tuple[Event, DivTeam | None, str | None]]:
    """What the user is signed up for, in the order they will race it.

    Built by walking `events` rather than user.registrations, because
    registrations carry no date and reach back over every event the user has
    ever entered. `events` is already the list of events still to come.

    The division/team is reported only when the user actually chose it: an
    event with a single division did not ask, so naming it back is noise.
    """
    summary: list[tuple[Event, DivTeam | None, str | None]] = []

    for event in events:
        if not user.is_registered(event.scheduled_event_id):
            continue

        div_team_str: DivTeam | None = None
        div_team_name: str | None = None

        if not event.has_solo_division:
            group_id = user.group_ids[event.scheduled_event_id]
            if event.divisions:
                div_team_str = DivTeam.DIVISION
                div_team_name = next((d.name for d in event.divisions if d.id == group_id), None)
            elif event.teams:
                div_team_str = DivTeam.TEAM
                div_team_name = next((t.name for t in event.teams if t.id == group_id), None)

        summary.append((event, div_team_str, div_team_name))

    summary.sort(key=lambda row: row[0].starts_at)
    return summary
