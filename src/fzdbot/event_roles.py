"""Who should hold which Discord role, read off a tournament and its registrants.

The whole of the rule is here and none of it is Discord: a tournament and its
registrants in, snowflakes per role out, so the cog is the diff and the REST
calls and nothing else.
"""

from collections.abc import Sequence

from fzdbot.api_types import TournamentRegistrantResponse, TournamentResponse


def role_holders(
    tournament: TournamentResponse, registrants: Sequence[TournamentRegistrantResponse]
) -> dict[int, set[int]]:
    """Each role one of the tournament's groups grants, to the snowflakes that
    should hold it; a role no group grants is not answered.

    A registrant holds their group's role while they hold a place: `waitlisted`
    is the API's own answer to who is in the event rather than in the queue for
    it, and somebody waiting gets nothing until a place opens. Groups may share
    a role, which then goes to the union of their members.

    `closed_at` set is staff declaring the results final: the tournament is
    parked and every role it grants goes to nobody. A past tournament not yet
    closed is under verification and answers no role at all, so what its
    members hold is left as it stands.
    """
    if tournament["phase"] == "past" and tournament["closed_at"] is None:
        return {}
    holders: dict[int, set[int]] = {
        int(group["role_id"]): set()
        for event in tournament["events"]
        for group in event["groups"]
        if group["role_id"] is not None
    }
    if tournament["closed_at"] is not None:
        return holders
    for registrant in registrants:
        role_id = registrant["group"]["role_id"]
        # Null for a membership entered by hand for a player never linked to a
        # Discord account: nobody to grant to.
        snowflake = registrant["discord_user_id"]
        if role_id is None or registrant["waitlisted"] or snowflake is None:
            continue
        holders[int(role_id)].add(int(snowflake))
    return holders
