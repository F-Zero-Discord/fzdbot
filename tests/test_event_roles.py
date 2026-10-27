"""Who a group's role goes to, read off a tournament and its registrants, and
the tick that hands it out against a fake guild and a fake API."""

import asyncio
from types import SimpleNamespace
from typing import cast

import discord
import pytest

from fzdbot.api_types import (
    EventResponse,
    TournamentEventResponse,
    TournamentGroupResponse,
    TournamentPhase,
    TournamentRegistrantResponse,
    TournamentResponse,
    TournamentSummaryResponse,
)
from fzdbot.cogs import event_roles as cog_module
from fzdbot.cogs.event_roles import EventRoles
from fzdbot.event_roles import role_holders
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot

MAIN_EVENT, CLASSIC = 810, 811
MAIN, WAITLIST, MASTERS, EXPERT = 33, 36, 34, 35
MAIN_ROLE, MASTERS_ROLE, EXPERT_ROLE = 10, 11, 12
GROUP_ROLES = {MAIN: MAIN_ROLE, WAITLIST: None, MASTERS: MASTERS_ROLE, EXPERT: EXPERT_ROLE}


def group(group_id, *, role_id, waitlist=False) -> TournamentGroupResponse:
    return TournamentGroupResponse(
        group_id=group_id,
        kind="division",
        name="Waitlist" if waitlist else "Main Event",
        alt_name=None,
        emote=None,
        display_order=0,
        capacity=None,
        registered=0,
        role_id=None if role_id is None else str(role_id),
        waitlist=waitlist,
    )


def event(scheduled_event_id, groups) -> TournamentEventResponse:
    return TournamentEventResponse(
        event=EventResponse(
            scheduled_event_id=scheduled_event_id,
            event="Main Event",
            display_name=None,
            starts_at="2026-11-01T18:00:00Z",
            ends_at="2026-11-01T21:00:00Z",
            mode="99",
            scoring_method="points",
            machine_input_required=False,
            tournament="sns2",
        ),
        group_kind="division",
        groups=groups,
    )


def sns2(*, phase: TournamentPhase = "current", closed_at=None, group_roles=GROUP_ROLES) -> TournamentResponse:
    return TournamentResponse(
        slug="sns2",
        name="Supernova Showdown 2",
        rules_channel_id=None,
        help_channel_id=None,
        published_at="2026-10-01T00:00:00Z",
        closed_at=closed_at,
        starts_at="2026-11-01T18:00:00Z",
        ends_at="2026-11-01T21:00:00Z",
        phase=phase,
        events=[
            event(
                MAIN_EVENT,
                [
                    group(MAIN, role_id=group_roles[MAIN]),
                    group(WAITLIST, role_id=group_roles[WAITLIST], waitlist=True),
                ],
            ),
            event(CLASSIC, [group(MASTERS, role_id=group_roles[MASTERS]), group(EXPERT, role_id=group_roles[EXPERT])]),
        ],
    )


def summary(tournament: TournamentResponse) -> TournamentSummaryResponse:
    return TournamentSummaryResponse(
        slug=tournament["slug"],
        name=tournament["name"],
        rules_channel_id=None,
        help_channel_id=None,
        published_at=tournament["published_at"],
        closed_at=tournament["closed_at"],
        starts_at=tournament["starts_at"],
        ends_at=tournament["ends_at"],
        phase=tournament["phase"],
    )


def registrant(snowflake, group_id=MAIN, *, group_roles=GROUP_ROLES) -> TournamentRegistrantResponse:
    waitlisted = group_id == WAITLIST
    role_id = group_roles[group_id]
    return TournamentRegistrantResponse(
        scheduled_event_id=MAIN_EVENT if group_id in (MAIN, WAITLIST) else CLASSIC,
        discord_user_id=None if snowflake is None else str(snowflake),
        display_name="player",
        tag="player",
        discord_user_name="player",
        group={
            "group_id": group_id,
            "kind": "division",
            "name": "Waitlist" if waitlisted else "Main Event",
            "alt_name": None,
            "role_id": None if role_id is None else str(role_id),
        },
        waitlisted=waitlisted,
        registered_at="2026-10-02T10:00:00Z",
    )


# --- Who should hold it -------------------------------------------------------


def test_a_registrant_holding_a_place_holds_their_groups_role():
    holders = role_holders(sns2(), [registrant(1), registrant(2, MASTERS)])

    assert holders[MAIN_ROLE] == {1}
    assert holders[MASTERS_ROLE] == {2}


def test_a_waitlisted_registrant_holds_nothing():
    """Theun's one condition: the queue gets nothing until it gets a place."""
    holders = role_holders(sns2(), [registrant(1), registrant(2, WAITLIST)])

    assert holders[MAIN_ROLE] == {1}
    assert 2 not in {snowflake for snowflakes in holders.values() for snowflake in snowflakes}


def test_a_role_nobody_is_registered_under_is_answered_empty():
    """So the last member to withdraw loses it."""
    assert role_holders(sns2(), [registrant(1)])[EXPERT_ROLE] == set()


def test_groups_sharing_a_role_pool_their_members():
    shared = {MAIN: MAIN_ROLE, WAITLIST: None, MASTERS: MASTERS_ROLE, EXPERT: MASTERS_ROLE}
    registrants = [registrant(1, MASTERS, group_roles=shared), registrant(2, EXPERT, group_roles=shared)]

    assert role_holders(sns2(group_roles=shared), registrants) == {MAIN_ROLE: set(), MASTERS_ROLE: {1, 2}}


def test_a_group_with_no_role_grants_none():
    roleless = {MAIN: None, WAITLIST: None, MASTERS: None, EXPERT: None}

    assert role_holders(sns2(group_roles=roleless), [registrant(1, group_roles=roleless)]) == {}


def test_a_registrant_with_no_discord_account_is_nobody_to_grant_to():
    assert role_holders(sns2(), [registrant(None)])[MAIN_ROLE] == set()


def test_a_closed_tournaments_roles_go_to_nobody():
    holders = role_holders(sns2(phase="past", closed_at="2026-11-05T12:00:00Z"), [registrant(1)])

    assert holders == {MAIN_ROLE: set(), MASTERS_ROLE: set(), EXPERT_ROLE: set()}


def test_a_past_tournament_under_verification_answers_no_role():
    """Its members keep what they hold until staff close it."""
    assert role_holders(sns2(phase="past"), [registrant(1)]) == {}


def test_an_upcoming_tournament_grants_like_a_current_one():
    assert role_holders(sns2(phase="upcoming"), [registrant(1)])[MAIN_ROLE] == {1}


# --- The tick -----------------------------------------------------------------


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="", headers={}), "refused")


class Role:
    def __init__(self, members, *, role_id=MAIN_ROLE, name="SNS2 Main Event"):
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
    """One tournament, or the error every read raises."""

    def __init__(self, registrants, *, tournament=None):
        self.registrants = registrants
        self.tournament_ = tournament or sns2()

    async def tournaments(self, now):
        if isinstance(self.registrants, Exception):
            raise self.registrants
        return [summary(self.tournament_)]

    async def tournament(self, slug, now):
        return self.tournament_

    async def tournament_registrants(self, slug):
        return self.registrants


class Bot:
    def __init__(self, api, guild):
        self.api = api
        self.guild = guild

    def get_guild(self, server_id):
        return self.guild


@pytest.fixture
def alerts(monkeypatch):
    monkeypatch.setattr(cog_module, "get_settings", lambda: SimpleNamespace(server_id=1, event_role_sync_seconds=300))
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
    bot = Bot(Api([registrant(1), registrant(2, WAITLIST)]), Guild(role, [registered, waiting, stranger]))

    tick(bot)

    assert [member.id for member in role.members] == [1]


def test_each_groups_role_is_reconciled_and_follows_a_move(alerts):
    """Moved out of Masters, the second player loses its role and keeps
    nothing else; the first keeps theirs."""
    staying, moved = Member(1), Member(2)
    main = Role([])
    masters = Role([moved], role_id=MASTERS_ROLE, name="SNS2 Classic Masters")
    expert = Role([], role_id=EXPERT_ROLE, name="SNS2 Classic Expert")
    bot = Bot(
        Api([registrant(1, MASTERS), registrant(2, EXPERT)]),
        Guild(main, [staying, moved], others=[masters, expert]),
    )

    tick(bot)

    assert main.members == []
    assert [member.id for member in masters.members] == [1]
    assert [member.id for member in expert.members] == [2]


def test_a_holder_who_is_still_registered_is_left_alone(alerts):
    registered = Member(1)
    role = Role([registered])
    bot = Bot(Api([registrant(1)]), Guild(role, [registered]))

    tick(bot)

    assert [member.id for member in role.members] == [1]


def test_a_closed_tournament_has_its_roles_withdrawn(alerts):
    holder = Member(1)
    role = Role([holder])
    closed = sns2(phase="past", closed_at="2026-11-05T12:00:00Z")
    bot = Bot(Api([registrant(1)], tournament=closed), Guild(role, [holder]))

    tick(bot)

    assert role.members == []


def test_a_role_the_guild_does_not_have_is_skipped(alerts):
    """Stage's groups name FZD's roles after a rebuild; the other roles still sync."""
    registered = Member(1)
    masters = Role([], role_id=MASTERS_ROLE)
    bot = Bot(Api([registrant(1, MASTERS)]), Guild(masters, [registered]))

    tick(bot)

    assert [member.id for member in masters.members] == [1]


def test_a_registrant_who_is_not_in_the_guild_is_skipped(alerts):
    """And the registrants after them are still granted theirs."""
    present = Member(2)
    role = Role([])
    bot = Bot(Api([registrant(1), registrant(2)]), Guild(role, [present]))

    tick(bot)

    assert [member.id for member in role.members] == [2]


def test_a_member_the_bot_may_not_edit_does_not_stop_the_pass(alerts):
    """Staff hold a role above the bot's, and staff register for events."""
    above, ordinary = Member(1, forbidden=True), Member(2)
    role = Role([])
    bot = Bot(Api([registrant(1), registrant(2)]), Guild(role, [above, ordinary]))

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
