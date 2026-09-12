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
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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

    async def add_score(
        self,
        discord_user_id: int,
        discord_user_name: str,
        tag: str,
        scheduled_event_id: int,
        score: int,
        machine_id: int | None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/players/{discord_user_id}/scores",
            json={
                "scheduled_event_id": scheduled_event_id,
                "discord_user_name": discord_user_name,
                "tag": tag,
                "score": score,
                "machine_id": machine_id,
            },
        )

    async def list_scores(self, discord_user_id: int, scheduled_event_id: int) -> list[dict[str, Any]]:
        body = await self._request(
            "GET",
            f"/v1/players/{discord_user_id}/scores?scheduled_event_id={scheduled_event_id}",
        )
        return body["scores"]

    async def edit_score(self, discord_user_id: int, score_id: int, points: int) -> dict[str, Any]:
        return await self._request(
            "PATCH",
            f"/v1/players/{discord_user_id}/scores/{score_id}",
            json={"points": points},
        )

    async def delete_score(self, discord_user_id: int, score_id: int) -> None:
        await self._request("DELETE", f"/v1/players/{discord_user_id}/scores/{score_id}")

    async def machines(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/machines")

    async def active_events(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/events/active")

    async def scoreboard(self, scheduled_event_id: int, discord_user_id: int) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/events/{scheduled_event_id}/scoreboard?discord_user_id={discord_user_id}",
        )

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

    async def withdraw(
        self, discord_user_id: int, scheduled_event_id: int, now: datetime
    ) -> dict[str, Any]:
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
                    raise FzdApiError(self._message_for(response.status, body), response.status)
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
