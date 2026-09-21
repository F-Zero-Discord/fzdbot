import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp

from fzdbot.api_types import (
    EventDetailResponse,
    EventResponse,
    EventTypeResponse,
    Ggp8EvaluationAnswerResponse,
    Ggp8EvaluationResponse,
    Ggp8RegistrationResponse,
    LiveScoreboardResponse,
    MachineResponse,
    PlayerResultResponse,
    PlayerTagResponse,
    RegistrationEventResponse,
    RivalEventResponse,
    RivalsResponse,
    ScoreboardResponse,
    ScoreResultResponse,
    SlotResponse,
    TimeResultResponse,
    TrackResponse,
)

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


def _instant(moment: datetime) -> str:
    """A `?now=` value. The API reads its domain clock from this parameter, so a
    naive datetime here would be sent as a wall clock in an unstated zone.
    """
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class FzdApiError(Exception):
    """`status` is the HTTP status, or None when the API was never reached.
    `detail` is the API's own sentence for a 4xx, when it gave one — the
    refusal a command can show a user as it is, where `str(error)` also says
    which service refused.
    """

    def __init__(self, message: str, status: int | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail

    def refusal(self) -> str:
        """What to tell the user. A 4xx carries the API's own sentence about the
        rule that refused the request; anything else is the client's description.
        """
        if self.detail and self.status in (404, 409, 422):
            return self.detail
        return str(self)


class FzdApi:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def set_tag(self, discord_user_id: int, discord_user_name: str, tag: str) -> PlayerTagResponse:
        return await self._request(
            "PUT",
            f"/v1/players/{discord_user_id}/tag",
            json={"discord_user_name": discord_user_name, "tag": tag},
        )

    async def schedule(self, scheduled_event_id: int) -> list[SlotResponse]:
        """The event's slots in schedule order, each with its lineup, mode and
        start, its lineup's `tracks`, and `vote_winners`, one per lobby whose
        vote is recorded. Empty when none are entered, which is an event that
        cannot take a result.
        """
        return await self._request("GET", f"/v1/events/{scheduled_event_id}/schedule")

    async def set_score(
        self,
        discord_user_id: int,
        discord_user_name: str,
        scheduled_event_id: int,
        slot_id: int,
        score: int | None,
        machine_id: int | None,
        now: datetime,
    ) -> ScoreResultResponse:
        """Set, or replace, the player's points result on a slot. `score=None`
        submits a DNF: a row that holds no value. The API refuses a slot off
        the event, an event scored by time or not running at `now`, a negative
        score, and a missing machine where the event records one.
        """
        return await self._request(
            "PUT",
            f"/v1/events/{scheduled_event_id}/slots/{slot_id}/score?now={_instant(now)}",
            json=self._submission(discord_user_id, discord_user_name, machine_id, score=score),
        )

    async def set_time(
        self,
        discord_user_id: int,
        discord_user_name: str,
        scheduled_event_id: int,
        slot_id: int,
        time_cs: int | None,
        machine_id: int | None,
        now: datetime,
    ) -> TimeResultResponse:
        """Set, or replace, the player's time on a slot, in centiseconds as
        entered. `time_cs=None` submits a DNF. Refusals as for `set_score`,
        with the scoring method read the other way round.
        """
        return await self._request(
            "PUT",
            f"/v1/events/{scheduled_event_id}/slots/{slot_id}/time?now={_instant(now)}",
            json=self._submission(discord_user_id, discord_user_name, machine_id, time_cs=time_cs),
        )

    async def delete_result(self, discord_user_id: int, scheduled_event_id: int, slot_id: int, now: datetime) -> None:
        """Return the slot to nothing submitted for the player. 404 when there
        was nothing, 409 once the event is not running at `now`.
        """
        await self._request(
            "DELETE",
            f"/v1/events/{scheduled_event_id}/slots/{slot_id}/result?discord_user_id={discord_user_id}&now={_instant(now)}",
        )

    @staticmethod
    def _submission(
        discord_user_id: int, discord_user_name: str, machine_id: int | None, **value: int | None
    ) -> dict[str, Any]:
        """A result body: the player, the machine, and exactly one of the value
        or `dnf`. The API refuses a body carrying both or neither.
        """
        body: dict[str, Any] = {
            "discord_user_id": str(discord_user_id),
            "discord_user_name": discord_user_name,
            "machine_id": machine_id,
        }
        ((field, amount),) = value.items()
        if amount is None:
            body["dnf"] = True
        else:
            body[field] = amount
        return body

    async def set_vote(
        self,
        recorded_by_discord_user_id: int,
        recorded_by_discord_user_name: str,
        scheduled_event_id: int,
        slot_id: int,
        track_id: int,
        division_id: int | None,
    ) -> SlotResponse:
        """Set the track one lobby voted in on a race slot; a second call for
        the same lobby replaces the first. `division_id` names the lobby on an
        event with divisions and is `None` on one without. Answers the slot.
        """
        return await self._request(
            "PUT",
            f"/v1/events/{scheduled_event_id}/slots/{slot_id}/vote",
            json={
                "track_id": track_id,
                "division_id": division_id,
                "recorded_by_discord_user_id": str(recorded_by_discord_user_id),
                "recorded_by_discord_user_name": recorded_by_discord_user_name,
            },
        )

    async def tracks(self) -> list[TrackResponse]:
        return await self._request("GET", "/v1/tracks")

    async def machines(self) -> list[MachineResponse]:
        return await self._request("GET", "/v1/machines")

    async def player_results(self, discord_user_id: int, scheduled_event_id: int) -> list[PlayerResultResponse]:
        """The player's own results on the event, one per slot they have set,
        in position order: `slot_id`, `position`, `score`, `time_cs`,
        `machine_id`, `machine` and `modified_dt`. A submitted DNF is a row
        whose value is `None`. Empty for a player who has set nothing; 404 for
        an unknown event.
        """
        return await self._request(
            "GET", f"/v1/players/{discord_user_id}/results?scheduled_event_id={scheduled_event_id}"
        )

    async def active_events(self) -> list[EventResponse]:
        return await self._request("GET", "/v1/events/active")

    async def event_detail(self, scheduled_event_id: int) -> EventDetailResponse:
        """The event with what a board needs: `group_kind` (`division`, `team`
        or None), its `groups`, its `slots` with their multipliers, and
        `scoring` (mulligans and the time cap). 404 for an unknown or
        cancelled event.
        """
        return await self._request("GET", f"/v1/events/{scheduled_event_id}")

    async def scoreboard(self, scheduled_event_id: int) -> ScoreboardResponse:
        """The standings, the same for any caller: a division event answers
        every division in group order, each ranked within itself, and a team
        event every team. One read answers every board the event draws, so
        nothing here asks the API to narrow to one group.
        """
        return await self._request("GET", f"/v1/events/{scheduled_event_id}/scoreboard")

    async def event_types(self, recurring: bool | None = None) -> list[EventTypeResponse]:
        """Every event definition, or only the weeklies (`True`) or the
        one-offs (`False`).
        """
        path = "/v1/event-types"
        if recurring is not None:
            path += f"?recurring={'true' if recurring else 'false'}"
        return await self._request("GET", path)

    async def latest_event(self, event_type: str | None, now: datetime) -> EventResponse:
        now_utc = _instant(now)
        path = f"/v1/events/latest?now={now_utc}"
        if event_type is not None:
            path = f"/v1/events/latest?event_type={event_type}&now={now_utc}"
        return await self._request("GET", path)

    async def registrations(self, discord_user_id: int, now: datetime) -> list[RegistrationEventResponse]:
        """Every event open to registration, each with its groups and this
        player's standing in it. One call: the groups carry their own capacity
        and headcount, so nothing downstream counts anything.
        """
        body = await self._request(
            "GET",
            f"/v1/players/{discord_user_id}/registrations?now={_instant(now)}",
        )
        return body["events"]

    async def register(
        self,
        discord_user_id: int,
        discord_user_name: str,
        tag: str,
        scheduled_event_id: int,
        group_id: int,
        now: datetime,
    ) -> RegistrationEventResponse:
        """Join `group_id`, or move to it from another group of the same event.
        409 means the group filled up; the caller re-reads and says so.
        """
        return await self._request(
            "PUT",
            f"/v1/players/{discord_user_id}/registrations/{scheduled_event_id}?now={_instant(now)}",
            json={
                "discord_user_name": discord_user_name,
                "tag": tag,
                "group_id": group_id,
            },
        )

    async def withdraw(self, discord_user_id: int, scheduled_event_id: int, now: datetime) -> RegistrationEventResponse:
        """Leave whichever group of this event the player holds. Idempotent."""
        return await self._request(
            "DELETE",
            f"/v1/players/{discord_user_id}/registrations/{scheduled_event_id}?now={_instant(now)}",
        )

    async def evaluations(self, discord_user_id: int, scheduled_event_id: int) -> Ggp8EvaluationResponse:
        """This player's questionnaire answers for one event, plus the two answer
        lists a form offers. `answered_for_this_event` tells a stored answer from
        one carried over from the player's most recent event.
        """
        return await self._request(
            "GET",
            f"/v1/ggp8/players/{discord_user_id}/evaluations/{scheduled_event_id}",
        )

    async def save_evaluations(
        self,
        discord_user_id: int,
        discord_user_name: str,
        tag: str,
        scheduled_event_id: int,
        self_evaluation_id: int,
        most_recent_event_id: int,
    ) -> Ggp8EvaluationAnswerResponse:
        return await self._request(
            "PUT",
            f"/v1/ggp8/players/{discord_user_id}/evaluations/{scheduled_event_id}",
            json={
                "discord_user_name": discord_user_name,
                "tag": tag,
                "self_evaluation_id": self_evaluation_id,
                "most_recent_event_id": most_recent_event_id,
            },
        )

    async def ggp8_events(self) -> list[EventResponse]:
        """The scheduled events that are GGP8's, earliest first. Which ones is
        the API's configuration; nothing here holds an event id.
        """
        return await self._request("GET", "/v1/ggp8/events")

    async def ggp8_and_active_events(self) -> list[EventResponse]:
        """GGP8's events and whatever is running now, earliest first, each
        once. What a picker offers when a command is GGP8's but has to be
        tried on a weekly: the weekly is listed while it runs.
        """
        ggp8, active = await asyncio.gather(self.ggp8_events(), self.active_events())
        events = {event["scheduled_event_id"]: event for event in [*ggp8, *active]}
        return sorted(events.values(), key=lambda event: event["starts_at"])

    async def ggp8_registrations(self) -> list[Ggp8RegistrationResponse]:
        """Everybody currently registered for a GGP8 event, one row per player
        per event, each with their division or team and their Discord id.
        """
        return await self._request("GET", "/v1/ggp8/registrations")

    async def live_scoreboards(self) -> list[LiveScoreboardResponse]:
        """Every message registered as a live board: `message_id` and
        `channel_id` as strings, `scheduled_event_id`, and the `division_id`
        or `team_id` it is narrowed to, both null for a whole event.
        """
        return await self._request("GET", "/v1/scoreboards")

    async def register_scoreboard(
        self,
        message_id: int,
        channel_id: int,
        scheduled_event_id: int,
        *,
        division_id: int | None = None,
        team_id: int | None = None,
    ) -> LiveScoreboardResponse:
        """Make the message a live board. Idempotent; the API refuses a group
        the event does not have (404) or of the kind it does not group by (422).
        """
        return await self._request(
            "PUT",
            f"/v1/scoreboards/{message_id}",
            json={
                "channel_id": str(channel_id),
                "scheduled_event_id": scheduled_event_id,
                "division_id": division_id,
                "team_id": team_id,
            },
        )

    async def stop_scoreboard(self, message_id: int) -> None:
        """Take the message out of the registry. 204 whether or not it was in it."""
        await self._request("DELETE", f"/v1/scoreboards/{message_id}")

    async def rivals(self, discord_user_id: int, now: datetime) -> RivalsResponse:
        """Every event running a Rival Challenge with this player's standing in
        it — registered, locked, their pick — and who has picked them.
        """
        return await self._request("GET", f"/v1/players/{discord_user_id}/rivals?now={_instant(now)}")

    async def choose_rival(
        self, discord_user_id: int, scheduled_event_id: int, rival_discord_user_id: int, now: datetime
    ) -> RivalEventResponse:
        """Name a rival for one event, replacing an earlier pick. The API holds
        the rules: 404 for an event with no Rival Challenge or a rival not in the
        caller's division, 409 when the caller is not registered or the event has
        started, 422 for naming yourself. Answers the refreshed event.
        """
        return await self._request(
            "PUT",
            f"/v1/players/{discord_user_id}/rivals/{scheduled_event_id}?now={_instant(now)}",
            json={"rival_discord_user_id": str(rival_discord_user_id)},
        )

    async def withdraw_rival(self, discord_user_id: int, scheduled_event_id: int, now: datetime) -> RivalEventResponse:
        """Clear the caller's rival for one event. Idempotent; 409 once the event
        has started.
        """
        return await self._request(
            "DELETE",
            f"/v1/players/{discord_user_id}/rivals/{scheduled_event_id}?now={_instant(now)}",
        )

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method, url, json=json, headers={API_KEY_HEADER: self._api_key}
            ) as response:
                body = await self._read_body(response)
                if response.status >= 400:
                    raise FzdApiError(self._message_for(response.status, body), response.status, self._detail_of(body))
                return body
        except TimeoutError as error:
            logger.error("[API] %s %s timed out", method, url)
            raise FzdApiError("The FZD API did not answer in time. Nothing was changed.") from error
        except aiohttp.ClientError as error:
            logger.error("[API] %s %s failed: %s", method, url, error)
            raise FzdApiError(f"Could not reach the FZD API ({error}). Nothing was changed.") from error

    @staticmethod
    async def _read_body(response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError):
            return {}

    @staticmethod
    def _detail_of(body: Any) -> str | None:
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail if isinstance(detail, str) else None

    @staticmethod
    def _message_for(status: int, body: Any) -> str:
        detail = body.get("detail") if isinstance(body, dict) else None
        if status == 401:
            return "The FZD API rejected this bot's key. Its `FZD_API_KEY` needs to match what the API has configured."
        if status == 403:
            return "The FZD API refused this request."
        if status == 404:
            return f"The FZD API could not find that record. ({detail or 'not found'})"
        if status == 409:
            return f"The FZD API could not make that change. ({detail or 'conflict'})"
        if status == 422:
            return f"The FZD API rejected the request as invalid: {detail if isinstance(detail, str) else body or 'no detail given'}"
        if status >= 500:
            return (
                f"The FZD API failed ({status}). Nothing was changed; try again, and tell a dev if it keeps happening."
            )
        return f"The FZD API answered {status}. ({detail or 'no detail given'})"
