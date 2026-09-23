import asyncio
from datetime import UTC, datetime

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
    client._session = session  # pyright: ignore[reportAttributeAccessIssue]
    return client, session


def test_player_request_shapes_and_snowflakes_are_strings():
    async def run():
        client, session = client_with(
            Response(200, {}),
            Response(201, {"score": 99}),
            Response(200, {"time_cs": None}),
            Response(204, {}),
        )
        now = datetime(2026, 9, 25, 19, 30, tzinfo=UTC)

        await client.set_tag(123456789012345678, "pilot", "Pilot")
        await client.set_score(123456789012345678, "pilot", 14, 3, 99, 2, now)
        await client.set_time(123456789012345678, "pilot", 14, 3, None, None, now)
        await client.delete_result(123456789012345678, 14, 3, now)

        assert session.calls == [
            (
                "PUT",
                "https://api.example.test/v1/players/123456789012345678/tag",
                {"discord_user_name": "pilot", "tag": "Pilot"},
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "PUT",
                "https://api.example.test/v1/events/14/slots/3/score?now=2026-09-25T19:30:00Z",
                {
                    "discord_user_id": "123456789012345678",
                    "discord_user_name": "pilot",
                    "machine_id": 2,
                    "score": 99,
                },
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "PUT",
                "https://api.example.test/v1/events/14/slots/3/time?now=2026-09-25T19:30:00Z",
                {
                    "discord_user_id": "123456789012345678",
                    "discord_user_name": "pilot",
                    "machine_id": None,
                    "dnf": True,
                },
                {API_KEY_HEADER: "test-key"},
            ),
            (
                "DELETE",
                "https://api.example.test/v1/events/14/slots/3/result?discord_user_id=123456789012345678&now=2026-09-25T19:30:00Z",
                None,
                {API_KEY_HEADER: "test-key"},
            ),
        ]

    asyncio.run(run())


def test_schedule_request():
    async def run():
        client, session = client_with(Response(200, []))

        assert await client.schedule(14) == []
        assert [call[:3] for call in session.calls] == [
            ("GET", "https://api.example.test/v1/events/14/schedule", None),
        ]

    asyncio.run(run())


def test_machine_and_active_event_requests():
    async def run():
        client, session = client_with(Response(200, []), Response(200, []), Response(200, []))

        assert await client.machines() == []
        assert await client.active_events() == []
        assert await client.player_results(123456789012345678, 14) == []
        assert [call[:3] for call in session.calls] == [
            ("GET", "https://api.example.test/v1/machines", None),
            ("GET", "https://api.example.test/v1/events/active", None),
            ("GET", "https://api.example.test/v1/players/123456789012345678/results?scheduled_event_id=14", None),
        ]

    asyncio.run(run())


def test_event_detail_and_scoreboard_requests():
    async def run():
        client, session = client_with(
            Response(200, {"scheduled_event_id": 14}),
            Response(200, {"rows": []}),
            Response(200, {"rows": []}),
            Response(200, {"rows": []}),
        )

        assert await client.event_detail(14) == {"scheduled_event_id": 14}
        await client.scoreboard(14)
        await client.scoreboard(14)

        assert [call[:3] for call in session.calls] == [
            ("GET", "https://api.example.test/v1/events/14", None),
            ("GET", "https://api.example.test/v1/events/14/scoreboard", None),
            ("GET", "https://api.example.test/v1/events/14/scoreboard", None),
        ]

    asyncio.run(run())


def test_event_types_request_spells_the_filter_lowercase():
    async def run():
        client, session = client_with(Response(200, []), Response(200, []), Response(200, []))

        await client.event_types()
        await client.event_types(recurring=True)
        await client.event_types(recurring=False)

        assert [call[:3] for call in session.calls] == [
            ("GET", "https://api.example.test/v1/event-types", None),
            ("GET", "https://api.example.test/v1/event-types?recurring=true", None),
            ("GET", "https://api.example.test/v1/event-types?recurring=false", None),
        ]

    asyncio.run(run())


def test_latest_event_request_omits_an_empty_event_type():
    async def run():
        client, session = client_with(Response(200, {}), Response(200, {}))
        now = datetime(2026, 9, 2, 19, 30, tzinfo=UTC)

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
        assert error.value.refusal() == "tag must be at most 10 characters"

    asyncio.run(run())


def test_calendar_request_carries_its_window():
    async def run():
        client, session = client_with(Response(200, []), Response(200, []))

        await client.calendar()
        await client.calendar(days=30)

        assert [call[:3] for call in session.calls] == [
            ("GET", "https://api.example.test/v1/events?days=14", None),
            ("GET", "https://api.example.test/v1/events?days=30", None),
        ]

    asyncio.run(run())
