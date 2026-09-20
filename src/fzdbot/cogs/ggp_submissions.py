"""`/ggp_submit_score` and `/ggp_submit_time`: a result on the running GGP8
event's active slot, with a machine always named.

The player types a value and picks a machine, and nothing else: which event
and which slot are read from the clock at the write, so there is no list of
slots to read. The running event is the GGP8 event whose window holds now,
and the active slot the one of its schedule that started most recently. Any
other slot — one that is over, or one of another event running at the same
time — is `/submit_score`'s and `/submit_time`'s, which offer every slot.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.api_types import EventResponse, PlayerResultResponse, SlotResponse
from fzdbot.cogs.submissions import (
    DNF,
    TIME_EXAMPLE,
    machine_choices,
    machine_named,
    parse_score,
    parse_time,
    recency,
    refuse,
)
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot
from fzdbot.scoreboards import event_label, format_time, slot_name
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

Method = Literal["score", "time"]


def active_events(events: list[EventResponse], now: datetime) -> list[EventResponse]:
    """The events whose window holds `now`: started, and not yet ended."""
    return [
        event
        for event in events
        if datetime.fromisoformat(event["starts_at"]) <= now < datetime.fromisoformat(event["ends_at"])
    ]


def active_slot(schedule: list[SlotResponse], now: datetime) -> SlotResponse | None:
    """The slot whose `starts_at` has passed most recently — at 20:00:00
    exactly, the one starting at 20:00 — or `None` when no slot has started.
    A slot with no start entered is never active.

    This is what makes the schedule's start times a contract: a slot takes
    results from its own start until the next slot's, so staff space the
    starts by the race plus the time players take to submit, 7 to 8 minutes
    for T&T.
    """
    if not schedule:
        return None
    first = min(schedule, key=lambda slot: recency(slot, now))
    return first if recency(first, now)[0] == 0 else None


def _value_phrase(method: Method, value: int | None) -> str:
    if value is None:
        return "DNF"
    return f"a score of {value}" if method == "score" else f"a time of {format_time(value)}"


def _replaced_phrase(method: Method, row: PlayerResultResponse | None) -> str:
    """`, replacing 820` for the row the write replaces, empty when there was none."""
    if row is None:
        return ""
    value = row["score"] if method == "score" else row["time_cs"]
    if value is None:
        return ", replacing a DNF"
    return f", replacing {value if method == 'score' else format_time(value)}"


class GgpSubmissions(commands.Cog):
    def __init__(self, bot: FZDBot):
        self.bot = bot

    async def machine_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await machine_choices(self.bot.api, current)

    async def _active(
        self, interaction: discord.Interaction, now: datetime
    ) -> tuple[EventResponse, SlotResponse] | None:
        """The running GGP8 event and its active slot, or `None` after telling
        the user why there is nothing to submit to.
        """
        running = active_events(await self.bot.api.ggp8_events(), now)
        if not running:
            await refuse(interaction, "No GGP8 event is running right now.")
            return None
        if len(running) > 1:
            names = " and ".join(event_label(event) for event in running)
            await refuse(interaction, f"{names} are both running right now; use `/submit_score` to pick the slot.")
            return None
        event = running[0]

        schedule = await self.bot.api.schedule(event["scheduled_event_id"])
        slot = active_slot(schedule, now)
        if slot is not None:
            return event, slot

        if not schedule:
            sentence = "The running event has no schedule entered, so it cannot take results. Tell FZD staff."
        else:
            soonest = min(schedule, key=lambda slot: recency(slot, now))
            if soonest["starts_at"] is None:
                sentence = "The running event's schedule has no start times entered. Tell FZD staff."
            else:
                starts = int(datetime.fromisoformat(soonest["starts_at"]).timestamp())
                sentence = (
                    f"{event_label(event)} starts with {slot_name(soonest)} <t:{starts}:R>; nothing to submit to yet."
                )
        await refuse(interaction, sentence)
        return None

    async def _submit(self, interaction: discord.Interaction, method: Method, value: int | None, machine: str) -> None:
        """Set `value` on the active slot for the caller, then confirm publicly,
        naming what it replaced where the caller had a result on the slot.
        """
        now = datetime.now(UTC)
        await interaction.response.defer()

        try:
            active = await self._active(interaction, now)
            if active is None:
                return
            event, slot = active
            machines, results = await asyncio.gather(
                self.bot.api.machines(),
                self.bot.api.player_results(interaction.user.id, event["scheduled_event_id"]),
            )
            machine_row = machine_named(machines, machine)
            previous = next((row for row in results if row["slot_id"] == slot["slot_id"]), None)
            write = self.bot.api.set_score if method == "score" else self.bot.api.set_time
            await write(
                interaction.user.id,
                interaction.user.name,
                event["scheduled_event_id"],
                slot["slot_id"],
                value,
                machine_row["machine_id"],
                now,
            )
        except ValueError:
            await refuse(interaction, "Pick a machine from the list.")
            return
        except FzdApiError as error:
            logger.warning("[ggp_submissions] %s refused for user=%s: %s", method, interaction.user, error)
            await refuse(interaction, error.refusal())
            return

        await interaction.followup.send(
            f"✅ User {interaction.user.display_name} has set {_value_phrase(method, value)} ({machine_row['name']}) "
            f"for {event_label(event)} {slot_name(slot)}{_replaced_phrase(method, previous)}."
        )
        logger.info(
            "[ggp_submissions] user=%s event=%s slot=%s %s=%s",
            interaction.user,
            event["scheduled_event_id"],
            slot["slot_id"],
            method,
            value,
        )

    @app_commands.command(name="ggp_submit_score", description="Set your score for the GGP slot that just ran")
    @app_commands.describe(score="Your points, or dnf", machine="The machine you raced")
    async def ggp_submit_score(self, interaction: discord.Interaction, score: str, machine: str) -> None:
        try:
            points = parse_score(score)
        except ValueError:
            await refuse(interaction, f"Enter your score as a whole number, like 87, or `{DNF}`.")
            return
        await self._submit(interaction, "score", points, machine)

    @app_commands.command(name="ggp_submit_time", description="Set your time for the GGP slot that just ran")
    @app_commands.describe(
        time=f"Minutes, seconds and centiseconds, like {TIME_EXAMPLE}, or dnf", machine="The machine you raced"
    )
    async def ggp_submit_time(self, interaction: discord.Interaction, time: str, machine: str) -> None:
        try:
            time_cs = parse_time(time)
        except ValueError:
            await refuse(
                interaction,
                f"Enter your time as minutes, seconds and centiseconds, like `{TIME_EXAMPLE}`, or `{DNF}`.",
            )
            return
        await self._submit(interaction, "time", time_cs, machine)

    async def cog_load(self) -> None:
        for command in (self.ggp_submit_score, self.ggp_submit_time):
            command.autocomplete("machine")(self.machine_autocomplete)


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(GgpSubmissions(bot), guild=discord.Object(id=get_settings().server_id))
