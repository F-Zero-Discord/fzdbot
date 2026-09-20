"""`/set_vote`: staff record which track a lobby voted in on a race slot.

A race slot's lineup is a pair, and which of the two was raced is decided by a
vote as the race starts, once per lobby: a division where the event has them,
the whole event where it has none. Recording it is what lets the submission
picker and a scoreboard name the track. Setting it again for the same lobby
replaces it. Which slots have a vote, which tracks a slot takes and whether a
division must be named are the API's rules, and its refusal is the sentence
the user reads.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.cogs.submissions import MAX_CHOICES, NO_EVENT, NO_SCHEDULE, SLOT_VALUE, choice_name, recency, slot_ids
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot
from fzdbot.scoreboards import group_label, slot_name
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)


def _typed_slot(slot: Any) -> tuple[int, int] | None:
    match = SLOT_VALUE.match(str(slot or "").strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


class Votes(commands.Cog):
    def __init__(self, bot: FZDBot):
        self.bot = bot

    async def slot_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """The race slots of every event running now, the one raced most
        recently first, labelled as the submission picker labels them but with
        the whole event's vote only, since the lobby is the division option's
        to name. A prix slot has no vote and is not offered. One placeholder
        when there is nothing to pick: no event, or an event whose schedule
        has not been entered.
        """
        try:
            events = await self.bot.api.active_events()
            schedules = await asyncio.gather(*(self.bot.api.schedule(e["scheduled_event_id"]) for e in events))
        except FzdApiError as error:
            logger.warning("[votes] slot autocomplete could not read the API: %s", error)
            return []

        if not events:
            return [app_commands.Choice(name="No event is running right now", value=NO_EVENT)]
        if not any(schedules):
            return [app_commands.Choice(name="The running event has no schedule entered", value=NO_SCHEDULE)]

        now = datetime.now(UTC)
        slots = sorted(
            (
                (event, slot)
                for event, schedule in zip(events, schedules, strict=False)
                for slot in schedule
                if slot["kind"] == "race"
            ),
            key=lambda pair: recency(pair[1], now),
        )
        needle = current.casefold()
        named = [(choice_name(slot, None), event, slot) for event, slot in slots]
        choices = [
            app_commands.Choice(name=name, value=f"{event['scheduled_event_id']}:{slot['slot_id']}")
            for name, event, slot in named
            if needle in name.casefold()
        ]
        return choices[:MAX_CHOICES]

    async def track_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The chosen slot's pair. A slot whose lineup names no tracks takes
        any track, so there every track of the slot's game is offered: the
        classic rows for a Classic slot, every other row otherwise, since a
        classic track shares its name with the standard one and only the
        mode tells them apart. Nothing before a slot is chosen.
        """
        ids = _typed_slot(interaction.namespace.slot)
        if ids is None:
            return []
        scheduled_event_id, slot_id = ids
        try:
            schedule = await self.bot.api.schedule(scheduled_event_id)
            slot = next((s for s in schedule if s["slot_id"] == slot_id), None)
            if slot is None:
                return []
            tracks = slot["tracks"] or [
                track
                for track in await self.bot.api.tracks()
                if (track["type"] == "classic") == (slot["mode"] == "Classic")
            ]
        except FzdApiError as error:
            logger.warning("[votes] track autocomplete could not read the API: %s", error)
            return []
        needle = current.casefold()
        choices = [
            app_commands.Choice(name=track["name"], value=str(track["track_id"]))
            for track in tracks
            if needle in track["name"].casefold()
        ]
        return choices[:MAX_CHOICES]

    async def division_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The chosen slot's event's divisions; nothing for an event without
        them, where the option is left empty.
        """
        ids = _typed_slot(interaction.namespace.slot)
        if ids is None:
            return []
        try:
            detail = await self.bot.api.event_detail(ids[0])
        except FzdApiError as error:
            logger.warning("[votes] division autocomplete could not read the API: %s", error)
            return []
        if detail["group_kind"] != "division":
            return []
        needle = current.casefold()
        named = [(group_label(group), group) for group in detail["groups"]]
        choices = [
            app_commands.Choice(name=name, value=str(group["group_id"]))
            for name, group in named
            if needle in name.casefold()
        ]
        return choices[:MAX_CHOICES]

    @app_commands.command(name="set_vote", description="Record which track a lobby voted in on a race slot")
    @app_commands.describe(
        slot="Which race slot",
        track="The track that won the vote",
        division="The division whose lobby voted; leave empty for an event without divisions",
    )
    async def set_vote(
        self, interaction: discord.Interaction, slot: str, track: str, division: str | None = None
    ) -> None:
        ids = await slot_ids(interaction, slot)
        if ids is None:
            return
        if not track.strip().isdigit() or (division is not None and not division.strip().isdigit()):
            await interaction.response.send_message("Pick the track and the division from the lists.", ephemeral=True)
            return
        scheduled_event_id, slot_id = ids
        division_id = int(division) if division is not None else None
        await interaction.response.defer(ephemeral=True)

        try:
            voted = await self.bot.api.set_vote(
                interaction.user.id, interaction.user.name, scheduled_event_id, slot_id, int(track), division_id
            )
        except FzdApiError as error:
            logger.warning("[votes] set_vote refused for user=%s: %s", interaction.user, error)
            await interaction.followup.send(error.refusal(), ephemeral=True)
            return

        await interaction.followup.send(f"✅ Vote recorded: {slot_name(voted, division_id)}.", ephemeral=True)
        logger.info(
            "[votes] user=%s event=%s slot=%s division=%s track=%s",
            interaction.user,
            scheduled_event_id,
            slot_id,
            division_id,
            track,
        )

    async def cog_load(self) -> None:
        self.set_vote.autocomplete("slot")(self.slot_autocomplete)
        self.set_vote.autocomplete("track")(self.track_autocomplete)
        self.set_vote.autocomplete("division")(self.division_autocomplete)


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(Votes(bot), guild=discord.Object(id=get_settings().server_id))
