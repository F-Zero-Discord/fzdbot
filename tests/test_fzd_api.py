import asyncio
from datetime import datetime, timezone

import pytest

from fzdbot.fzd_api import API_KEY_HEADER, FzdApi, FzdApiError


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def json(self, *, content_type):
        return self.body


class Request:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return None


class Session:
    closed = False

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, *, json, headers):
        self.calls.append((method, url, json, headers))
        return Request(next(self.responses))

    async def close(self):
        self.closed = True


def client_with(*responses):
    client = FzdApi("https://api.example.test/", "test-key")
    session = Session(responses)
    client._session = session
    return client, session


def test_player_request_shapes_and_snowflakes_are_strings():
    async def run():
        client, session = client_with(
            Response(200, {}),
            Response(201, {"points": 42}),
            Response(200, {"scores": []}),
            Response(200, {}),
            Response(204, {}),
            Response(200, {"scheduled_event_id": 14, "division": None, "entries": []}),
        )

        await client.set_tag(123456789012345678, "pilot", "Pilot")
        await client.add_score(123456789012345678, "pilot", "Pilot", 14, 99, 3)
        await client.list_scores(123456789012345678, 14)
        await client.edit_score(123456789012345678, 8, 100)
        await client.delete_score(123456789012345678, 8)
        await client.scoreboard(14, 123456789012345678)

        assert session.calls == [
            (
                "PUT",
                "https://api.example.test/v1/players/123456789012345678/tag",
                {"discord_user_name": "pilot", "tag": "Pilot"},
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "POST",
                "https://api.example.test/v1/players/123456789012345678/scores",
                {
                    "scheduled_event_id": 14,
                    "discord_user_name": "pilot",
                    "tag": "Pilot",
                    "score": 99,
                    "machine_id": 3,
                },
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "GET",
                "https://api.example.test/v1/players/123456789012345678/scores?scheduled_event_id=14",
                None,
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "PATCH",
                "https://api.example.test/v1/players/123456789012345678/scores/8",
                {"points": 100},
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "DELETE",
                "https://api.example.test/v1/players/123456789012345678/scores/8",
                None,
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "GET",
                "https://api.example.test/v1/events/14/scoreboard?discord_user_id=123456789012345678",
                None,
                {API_KEY_HEADER: "test-key"},
            ),
        ]

    asyncio.run(run())


def test_machine_and_active_event_requests():
    async def run():
        client, session = client_with(Response(200, []), Response(200, []))

        assert await client.machines() == []
        assert await client.active_events() == []
        assert [call[:3] for call in session.calls] == [
            ("GET", "https://api.example.test/v1/machines", None),
            ("GET", "https://api.example.test/v1/events/active", None),
        ]

    asyncio.run(run())


def test_latest_event_request_omits_an_empty_event_type():
    async def run():
        client, session = client_with(Response(200, {}), Response(200, {}))
        now = datetime(2026, 9, 2, 19, 30, tzinfo=timezone.utc)

        await client.latest_event(None, now)
        await client.latest_event("7", now)

        assert [call[:3] for call in session.calls] == [
            ("GET", "https://api.example.test/v1/events/latest?now=2026-09-02T19:30:00Z", None),
            (
                "GET",
                "https://api.example.test/v1/events/latest?event_type=7&now=2026-09-02T19:30:00Z",
                None,
            ),
        ]

    asyncio.run(run())


def test_problem_document_becomes_renderable_error():
    async def run():
        client, _ = client_with(Response(422, {"detail": "tag must be at most 10 characters"}))

        with pytest.raises(FzdApiError, match="tag must be at most 10 characters") as error:
            await client.set_tag(123456789012345678, "pilot", "Pilot")

        assert error.value.status == 422

    asyncio.run(run())
