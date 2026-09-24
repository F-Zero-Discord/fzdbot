"""Who an event's or a division's role goes to, and the tick that hands it out,
against a fake guild and a fake API."""

import asyncio
from types import SimpleNamespace
from typing import cast

import discord
import pytest

from fzdbot.api_types import Ggp8RegistrationResponse
from fzdbot.cogs import event_roles as cog_module
from fzdbot.cogs.event_roles import EventRoles
from fzdbot.event_roles import division_holders, holders
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot

ASHES, REBIRTH = 738, 739
ROLE = 10
STANDARD, EXPERT = 27, 28
STANDARD_ROLE = 11


def registration(
    snowflake, *, event_id=ASHES, waitlisted=False, tag="player", division_id=None
) -> Ggp8RegistrationResponse:
    return Ggp8RegistrationResponse(
        scheduled_event_id=event_id,
        event="Ashes",
        display_name="Ashes",
        tag=tag,
        discord_user_name=tag,
        discord_user_id=str(snowflake),
        division_id=division_id,
        division="Waitlist" if waitlisted else "Ashes",
        division_alt_name=None,
        team=None,
        team_alt_name=None,
        registered_at="2026-09-01T10:00:00Z",
        waitlisted=waitlisted,
    )


# --- Who should hold it -------------------------------------------------------


def test_a_registrant_holding_a_place_is_a_holder():
    assert holders([registration(1)], ASHES) == {1}


def test_a_waitlisted_registrant_is_not():
    """Theun's one condition: the queue gets nothing until it gets a place."""
    assert holders([registration(1), registration(2, waitlisted=True)], ASHES) == {1}


def test_another_events_registrants_are_another_roles_business():
    assert holders([registration(1), registration(2, event_id=REBIRTH)], ASHES) == {1}


def test_a_division_role_goes_to_that_divisions_members():
    """Read off `division_id`, which follows a player staff move; a division's
    name is theirs to change and would move the role with it."""
    registrations = [registration(1, division_id=STANDARD), registration(2, division_id=EXPERT)]

    assert division_holders(registrations, STANDARD) == {1}


def test_a_waitlisted_member_of_a_division_is_not_a_holder():
    assert division_holders([registration(1, division_id=STANDARD, waitlisted=True)], STANDARD) == set()


# --- The tick -----------------------------------------------------------------


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="", headers={}), "refused")


class Role:
    def __init__(self, members, *, role_id=ROLE, name="GGP Ashes"):
        self.id = role_id
        self.name = name
        self.members = members


class Member:
    def __init__(self, member_id, *, forbidden=False):
        self.id = member_id
        self.forbidden = forbidden

    async def add_roles(self, role, reason=None):
        if self.forbidden:
            raise http_error(discord.Forbidden, 403)
        role.members.append(self)

    async def remove_roles(self, role, reason=None):
        if self.forbidden:
            raise http_error(discord.Forbidden, 403)
        role.members.remove(self)


class Guild:
    def __init__(self, role, members, *, others=()):
        self.roles = {r.id: r for r in (role, *others)}
        self.members = {member.id: member for member in members}

    def get_role(self, role_id):
        return self.roles.get(role_id)

    def get_member(self, snowflake):
        return self.members.get(snowflake)


class Api:
    def __init__(self, registrations):
        self.registrations = registrations

    async def ggp8_registrations(self):
        if isinstance(self.registrations, Exception):
            raise self.registrations
        return self.registrations


class Bot:
    def __init__(self, api, guild):
        self.api = api
        self.guild = guild

    def get_guild(self, server_id):
        return self.guild


@pytest.fixture
def alerts(monkeypatch):
    monkeypatch.setattr(
        cog_module,
        "get_settings",
        lambda: SimpleNamespace(
            server_id=1,
            ggp8_event_roles={ASHES: ROLE},
            ggp8_division_roles={STANDARD: STANDARD_ROLE},
            event_role_sync_seconds=300,
        ),
    )
    sent = []

    async def send(bot, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(cog_module, "send_error_alert", send)
    return sent


def tick(bot):
    asyncio.run(EventRoles(cast(FZDBot, bot)).reconcile())


def test_the_role_follows_the_registrations(alerts):
    """Granted to a registrant without it, taken from a holder who is not one,
    and never granted to the queue."""
    registered, waiting, stranger = Member(1), Member(2), Member(3)
    role = Role([stranger])
    bot = Bot(
        Api([registration(1), registration(2, waitlisted=True)]),
        Guild(role, [registered, waiting, stranger]),
    )

    tick(bot)

    assert [member.id for member in role.members] == [1]


def test_a_division_role_is_granted_beside_the_events_and_follows_a_move(alerts):
    """Moved out of Standard, the second player loses its role and keeps the
    event's; the first is granted both."""
    staying, moved = Member(1), Member(2)
    event_role = Role([])
    standard = Role([moved], role_id=STANDARD_ROLE, name="GGP Classic Standard")
    bot = Bot(
        Api([registration(1, division_id=STANDARD), registration(2, division_id=EXPERT)]),
        Guild(event_role, [staying, moved], others=[standard]),
    )

    tick(bot)

    assert sorted(member.id for member in event_role.members) == [1, 2]
    assert [member.id for member in standard.members] == [1]


def test_a_holder_who_is_still_registered_is_left_alone(alerts):
    registered = Member(1)
    role = Role([registered])
    bot = Bot(Api([registration(1)]), Guild(role, [registered]))

    tick(bot)

    assert [member.id for member in role.members] == [1]


def test_an_event_with_no_registrations_takes_nothing_back(alerts):
    """A misconfigured event id and an empty event answer the same; one stale
    role is the cheaper of the two mistakes."""
    holder = Member(1)
    role = Role([holder])
    bot = Bot(Api([registration(2, event_id=REBIRTH)]), Guild(role, [holder]))

    tick(bot)

    assert [member.id for member in role.members] == [1]


def test_a_registrant_who_is_not_in_the_guild_is_skipped(alerts):
    """And the registrants after them are still granted theirs."""
    present = Member(2)
    role = Role([])
    bot = Bot(Api([registration(1), registration(2)]), Guild(role, [present]))

    tick(bot)

    assert [member.id for member in role.members] == [2]


def test_a_member_the_bot_may_not_edit_does_not_stop_the_pass(alerts):
    """Staff hold a role above the bot's, and staff register for events."""
    above, ordinary = Member(1, forbidden=True), Member(2)
    role = Role([])
    bot = Bot(Api([registration(1), registration(2)]), Guild(role, [above, ordinary]))

    tick(bot)

    assert [member.id for member in role.members] == [2]


def test_an_unreadable_api_changes_nothing(alerts):
    """Nothing is inferred from an answer that never arrived."""
    holder = Member(1)
    role = Role([holder])
    bot = Bot(Api(FzdApiError("the API is down")), Guild(role, [holder]))

    tick(bot)

    assert [member.id for member in role.members] == [1]
    assert alerts == []
