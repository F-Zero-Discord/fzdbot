"""The live-scoreboard tick against a fake API and a fake channel."""

import asyncio
from types import SimpleNamespace
from typing import cast

import discord
import pytest

from fzdbot.cogs import show_scoreboard
from fzdbot.cogs.show_scoreboard import Scoreboard
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot

PAST, FUTURE = "2026-09-01T20:00:00Z", "2099-01-01T20:00:00Z"


def board(message_id, event_id=1, division_id=None):
    return {
        "message_id": str(message_id),
        "channel_id": "500",
        "scheduled_event_id": event_id,
        "division_id": division_id,
        "team_id": None,
    }


def detail(ends_at=FUTURE):
    return {
        "scheduled_event_id": 1,
        "event": "Ashes",
        "display_name": None,
        "starts_at": "2026-09-01T18:00:00Z",
        "ends_at": ends_at,
        "scoring_method": "points",
        "group_kind": None,
        "groups": [],
        "slots": [],
        "scoring": {"num_mulligans": 0, "max_time_loss_cs": None, "machine_counts_once": False},
    }


def scoreboard(*names):
    return {
        "scheduled_event_id": 1,
        "group_kind": None,
        "scoring_method": "points",
        "filter": {"division_id": None, "team_id": None},
        "num_mulligans": 0,
        "max_time_loss_cs": None,
        "machine_counts_once": False,
        "rows": [
            {
                "display_name": n,
                "emote": None,
                "group_id": None,
                "results": [],
                "submissions": 0,
                "total": 10,
                "rank": 1,
            }
            for n in names
        ],
        "team_totals": [],
    }


def division_detail(ends_at=FUTURE):
    """An event with divisions draws one board per division, and none for the event itself."""
    return detail(ends_at) | {
        "group_kind": "division",
        "groups": [{"group_id": 26, "name": "Master", "alt_name": None, "emote": None, "display_order": 1}],
    }


def division_scoreboard():
    """Every row in a division, so no board is drawn for players with none."""
    board = scoreboard("Ann")
    board["group_kind"] = "division"
    for row in board["rows"]:
        row["group_id"] = 26
    return board


class Api:
    def __init__(self, boards, detail, scoreboard):
        self.boards = boards
        self.detail = detail
        self.board = scoreboard
        self.stopped = []
        self.reads = []

    async def live_scoreboards(self):
        return self.boards

    async def event_detail(self, event_id):
        self.reads.append(("detail", event_id))
        if isinstance(self.detail, Exception):
            raise self.detail
        return self.detail

    async def scoreboard(self, event_id):
        self.reads.append(("scoreboard", event_id))
        return self.board

    async def stop_scoreboard(self, message_id):
        self.stopped.append(message_id)


class Message:
    def __init__(self, message_id, failure):
        self.id = message_id
        self.failure = failure
        self.edits = []

    async def edit(self, *, embed):
        if self.failure is not None:
            raise self.failure
        self.edits.append(embed)


class Bot:
    def __init__(self, api, failures=None):
        self.api = api
        self.failures = failures or {}
        self.messages = {}

    def get_partial_messageable(self, channel_id):
        return SimpleNamespace(get_partial_message=self.message)

    def message(self, message_id):
        if message_id not in self.messages:
            self.messages[message_id] = Message(message_id, self.failures.get(message_id))
        return self.messages[message_id]


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="", headers={}), "refused")


@pytest.fixture
def alerts(monkeypatch):
    settings = SimpleNamespace(scoreboard_display_podium=False, scoreboard_lines_per_block=8)
    monkeypatch.setattr(show_scoreboard, "get_settings", lambda: settings)
    monkeypatch.setattr("fzdbot.formatters.get_settings", lambda: settings)
    sent = []

    async def send(bot, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(show_scoreboard, "send_error_alert", send)
    return sent


def tick(bot, times=1, cog=None):
    cog = cog or Scoreboard(cast(FZDBot, bot))

    async def run():
        for _ in range(times):
            await cog.refresh_boards()

    asyncio.run(run())
    return cog


def test_an_unchanged_board_is_edited_once_and_two_boards_of_one_event_share_reads(alerts):
    api = Api([board(11), board(12)], detail(), scoreboard("Ann"))
    bot = Bot(api)
    tick(bot, times=3)

    assert [len(m.edits) for m in bot.messages.values()] == [1, 1]
    assert bot.messages[11].edits[0].fields[0].value == "1\\. **Ann** - **10**\n"
    assert api.reads == [("detail", 1), ("scoreboard", 1)] * 3
    assert api.stopped == [] and alerts == []


def test_a_changed_board_is_edited_again(alerts):
    api = Api([board(11)], detail(), scoreboard("Ann"))
    bot = Bot(api)
    cog = tick(bot)
    api.board = scoreboard("Ann", "Bob")
    tick(bot, cog=cog)

    assert len(bot.messages[11].edits) == 2


def test_a_deleted_message_stops_its_board_without_an_alert(alerts):
    api = Api([board(11)], detail(), scoreboard("Ann"))
    bot = Bot(api, failures={11: http_error(discord.NotFound, 404)})
    tick(bot, times=2)

    assert api.stopped == [11, 11]
    assert alerts == []


def test_one_failing_board_alerts_once_and_does_not_stop_the_next(alerts):
    api = Api([board(11), board(12)], detail(), scoreboard("Ann"))
    bot = Bot(api, failures={11: http_error(discord.Forbidden, 403)})
    tick(bot, times=3)

    assert len(bot.messages[12].edits) == 1
    assert api.stopped == []
    assert [a["details"]["message_id"] for a in alerts] == ["11"]


def test_an_ended_event_is_drawn_as_final_and_its_row_deleted(alerts):
    api = Api([board(11)], detail(ends_at=PAST), scoreboard("Ann"))
    bot = Bot(api)
    tick(bot)

    (embed,) = bot.messages[11].edits
    assert "**Final results**" in embed.description
    assert api.stopped == [11]


def test_a_gone_event_stops_its_board(alerts):
    api = Api([board(11)], FzdApiError("gone", 404, "no such event"), scoreboard())
    bot = Bot(api)
    tick(bot)

    assert api.stopped == [11]
    assert bot.messages == {} and alerts == []


def test_an_unreachable_registry_skips_the_tick(alerts):
    class Down(Api):
        async def live_scoreboards(self):
            raise FzdApiError("down")

    api = Down([board(11)], detail(), scoreboard())
    tick(Bot(api))

    assert api.reads == [] and alerts == []


def test_a_board_the_event_draws_no_longer_is_stopped(alerts):
    api = Api([board(11)], division_detail(), division_scoreboard())
    bot = Bot(api)
    tick(bot, times=2)

    assert api.stopped == [11, 11]
    assert bot.messages == {} and alerts == []
