"""Who should hold a GGP8 event's or division's Discord role, read off the
registrations.

The whole of the rule is here and none of it is Discord: a set of snowflakes in,
a set of snowflakes out, so the cog above is the diff and the REST calls and
nothing else.
"""

from collections.abc import Sequence

from fzdbot.api_types import Ggp8RegistrationResponse


def _placed(registrations: Sequence[Ggp8RegistrationResponse]) -> set[int]:
    """Everybody here holding a place: `waitlisted` is the API's own answer to
    who is in the event rather than in the queue for it, and somebody waiting
    gets nothing until a place opens."""
    return {int(registration["discord_user_id"]) for registration in registrations if not registration["waitlisted"]}


def holders(registrations: Sequence[Ggp8RegistrationResponse], scheduled_event_id: int) -> set[int]:
    """The snowflakes that should hold this event's role. A registration for
    another event is another role's business."""
    return _placed([r for r in registrations if r["scheduled_event_id"] == scheduled_event_id])


def division_holders(registrations: Sequence[Ggp8RegistrationResponse], division_id: int) -> set[int]:
    """The snowflakes that should hold this division's role: its members as the
    API reads them now, so a player staff move between divisions moves roles on
    the next tick."""
    return _placed([r for r in registrations if r["division_id"] == division_id])
