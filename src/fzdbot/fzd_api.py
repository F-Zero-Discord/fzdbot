import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


def _instant(moment: datetime) -> str:
    """A `?now=` value. The API reads its domain clock from this parameter, so a
    naive datetime here would be sent as a wall clock in an unstated zone."""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class FzdApiError(Exception):
    """`status` is the HTTP status, or None when the API was never reached.
    `detail` is the API's own sentence for a 4xx, when it gave one — the
    refusal a command can show a user as it is, where `str(error)` also says
    which service refused."""

    def __init__(self, message: str, status: int | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail

    def refusal(self) -> str:
        """What to tell the user. A 4xx carries the API's own sentence about the
        rule that refused the request; anything else is the client's description."""
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

    async def set_tag(self, discord_user_id: int, discord_user_name: str, tag: str) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/v1/players/{discord_user_id}/tag",
            json={"discord_user_name": discord_user_name, "tag": tag},
        )

    async def schedule(self, scheduled_event_id: int) -> list[dict[str, Any]]:
        """The event's slots in schedule order, each with its lineup, mode and
        start, its lineup's `tracks`, and `vote_winners`, one per lobby whose
        vote is recorded. Empty when none are entered, which is an event that
        cannot take a result."""
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
    ) -> dict[str, Any]:
        """Set, or replace, the player's points result on a slot. `score=None`
        submits a DNF: a row that holds no value. The API refuses a slot off
        the event, an event scored by time or not running at `now`, a negative
        score, and a missing machine where the event records one."""
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
    ) -> dict[str, Any]:
        """Set, or replace, the player's time on a slot, in centiseconds as
        entered. `time_cs=None` submits a DNF. Refusals as for `set_score`,
        with the scoring method read the other way round."""
        return await self._request(
            "PUT",
            f"/v1/events/{scheduled_event_id}/slots/{slot_id}/time?now={_instant(now)}",
            json=self._submission(discord_user_id, discord_user_name, machine_id, time_cs=time_cs),
        )

    async def delete_result(
        self, discord_user_id: int, scheduled_event_id: int, slot_id: int, now: datetime
    ) -> None:
        """Return the slot to nothing submitted for the player. 404 when there
        was nothing, 409 once the event is not running at `now`."""
        await self._request(
            "DELETE",
            f"/v1/events/{scheduled_event_id}/slots/{slot_id}/result"
            f"?discord_user_id={discord_user_id}&now={_instant(now)}",
        )

    @staticmethod
    def _submission(
        discord_user_id: int, discord_user_name: str, machine_id: int | None, **value: int | None
    ) -> dict[str, Any]:
        """A result body: the player, the machine, and exactly one of the value
        or `dnf`. The API refuses a body carrying both or neither."""
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
    ) -> dict[str, Any]:
        """Set the track one lobby voted in on a race slot; a second call for
        the same lobby replaces the first. `division_id` names the lobby on an
        event with divisions and is `None` on one without. Answers the slot."""
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

    async def tracks(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/tracks")

    async def machines(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/machines")

    async def active_events(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/events/active")

    async def event_detail(self, scheduled_event_id: int) -> dict[str, Any]:
        """The event with what a board needs: `group_kind` (`division`, `team`
        or None), its `groups`, its `slots` with their multipliers, and
        `scoring` (mulligans and the time cap). 404 for an unknown or
        cancelled event."""
        return await self._request("GET", f"/v1/events/{scheduled_event_id}")

    async def scoreboard(
        self, scheduled_event_id: int, *, division_id: int | None = None, team_id: int | None = None
    ) -> dict[str, Any]:
        """The standings, the same for any caller. Unfiltered, a division
        event answers every division in group order, each ranked within
        itself; one of `division_id` or `team_id` narrows to that group, and
        the API refuses the kind the event does not have."""
        path = f"/v1/events/{scheduled_event_id}/scoreboard"
        if division_id is not None:
            path += f"?division_id={division_id}"
        elif team_id is not None:
            path += f"?team_id={team_id}"
        return await self._request("GET", path)

    async def event_types(self, recurring: bool | None = None) -> list[dict[str, Any]]:
        """Every event definition, or only the weeklies (`True`) or the
        one-offs (`False`)."""
        path = "/v1/event-types"
        if recurring is not None:
            path += f"?recurring={'true' if recurring else 'false'}"
        return await self._request("GET", path)

    async def latest_event(self, event_type: str | None, now: datetime) -> dict[str, Any]:
        now_utc = _instant(now)
        path = f"/v1/events/latest?now={now_utc}"
        if event_type is not None:
            path = f"/v1/events/latest?event_type={event_type}&now={now_utc}"
        return await self._request("GET", path)

    async def registrations(self, discord_user_id: int, now: datetime) -> list[dict[str, Any]]:
        """Every event open to registration, each with its groups and this
        player's standing in it. One call: the groups carry their own capacity
        and headcount, so nothing downstream counts anything."""
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
    ) -> dict[str, Any]:
        """Join `group_id`, or move to it from another group of the same event.
        409 means the group filled up; the caller re-reads and says so."""
        return await self._request(
            "PUT",
            f"/v1/players/{discord_user_id}/registrations/{scheduled_event_id}?now={_instant(now)}",
            json={
                "discord_user_name": discord_user_name,
                "tag": tag,
                "group_id": group_id,
            },
        )

    async def withdraw(self, discord_user_id: int, scheduled_event_id: int, now: datetime) -> dict[str, Any]:
        """Leave whichever group of this event the player holds. Idempotent."""
        return await self._request(
            "DELETE",
            f"/v1/players/{discord_user_id}/registrations/{scheduled_event_id}?now={_instant(now)}",
        )

    async def evaluations(self, discord_user_id: int, scheduled_event_id: int) -> dict[str, Any]:
        """This player's questionnaire answers for one event, plus the two answer
        lists a form offers. `answered_for_this_event` tells a stored answer from
        one carried over from the player's most recent event."""
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
    ) -> dict[str, Any]:
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

    async def ggp8_events(self) -> list[dict[str, Any]]:
        """The scheduled events that are GGP8's, earliest first. Which ones is
        the API's configuration; nothing here holds an event id."""
        return await self._request("GET", "/v1/ggp8/events")

    async def ggp8_registrations(self) -> list[dict[str, Any]]:
        """Everybody currently registered for a GGP8 event, one row per player
        per event, each with their division or team and their Discord id."""
        return await self._request("GET", "/v1/ggp8/registrations")

    async def rivals(self, discord_user_id: int, now: datetime) -> dict[str, Any]:
        """Every event running a Rival Challenge with this player's standing in
        it — registered, locked, their pick — and who has picked them."""
        return await self._request("GET", f"/v1/players/{discord_user_id}/rivals?now={_instant(now)}")

    async def choose_rival(
        self, discord_user_id: int, scheduled_event_id: int, rival_discord_user_id: int, now: datetime
    ) -> dict[str, Any]:
        """Name a rival for one event, replacing an earlier pick. The API holds
        the rules: 404 for an event with no Rival Challenge or a rival not in the
        caller's division, 409 when the caller is not registered or the event has
        started, 422 for naming yourself. Answers the refreshed event."""
        return await self._request(
            "PUT",
            f"/v1/players/{discord_user_id}/rivals/{scheduled_event_id}?now={_instant(now)}",
            json={"rival_discord_user_id": str(rival_discord_user_id)},
        )

    async def withdraw_rival(
        self, discord_user_id: int, scheduled_event_id: int, now: datetime
    ) -> dict[str, Any]:
        """Clear the caller's rival for one event. Idempotent; 409 once the event
        has started."""
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
                    raise FzdApiError(
                        self._message_for(response.status, body), response.status, self._detail_of(body)
                    )
                return body
        except asyncio.TimeoutError as error:
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
            return f"The FZD API failed ({status}). Nothing was changed; try again, and tell a dev if it keeps happening."
        return f"The FZD API answered {status}. ({detail or 'no detail given'})"
