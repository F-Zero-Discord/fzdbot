"""`/setup_scoreboard` and `/fzd_show`: post an event's standings.

Both read the event detail and the scoreboard from the API and hand the two to
`scoreboards.render_boards`; the bot holds no event id and no group id. A
posted board is a snapshot — it does not update — and `/setup_scoreboard` says
so in its description while keeping the name it was asked for.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.formatters import format_discord_timestamp, format_scoreboard_for_discord_embed
from fzdbot.fzd_api import FzdApiError
from fzdbot.scoreboards import event_label, render_boards
from fzdbot.settings import get_settings

thumbnail = "https://media.discordapp.net/attachments/1399501477608951933/1400792457007861800/Supernova_Server_Icon.png?ex=689c6da3&is=689b1c23&hm=68b8d8790d30689fbad0dfb9341c78921ecf9afecc5919880c81680329c32644&=&format=webp&quality=lossless&width=1024&height=1024"

logger = logging.getLogger(__name__)

MAX_CHOICES = 25  # Discord accepts at most 25 autocomplete results
MAX_EMBEDS = 10  # and at most 10 embeds per message


def _group_choice(group: dict[str, Any]) -> app_commands.Choice[str]:
    name = group["name"] if not group["alt_name"] else f"{group['name']} ({group['alt_name']})"
    return app_commands.Choice(name=name, value=str(group["group_id"]))


def _find_group(detail: dict[str, Any], group: str) -> dict[str, Any] | None:
    """The group a choice value names, or one typed by name."""
    wanted = group.strip().casefold()
    for candidate in detail["groups"]:
        if wanted in (str(candidate["group_id"]), candidate["name"].casefold()):
            return candidate
    return None


def _embeds(detail: dict[str, Any], scoreboard: dict[str, Any]) -> list[discord.Embed]:
    played = f"*Played on {format_discord_timestamp(datetime.fromisoformat(detail['starts_at']))}*"
    settings = get_settings()
    embeds = []
    for board in render_boards(detail, scoreboard, podium=settings.scoreboard_display_podium):
        title = f"{event_label(detail)} - {board.title}" if board.title else event_label(detail)
        embed = discord.Embed(title=title, description="\n".join([played, *board.notes]))
        if not board.lines:
            embed.add_field(name="", value="NO RESULTS TO DISPLAY YET", inline=False)
        else:
            embed.set_thumbnail(url=thumbnail)
            for block in format_scoreboard_for_discord_embed(
                board.lines, max_num_lines=settings.scoreboard_lines_per_block
            ):
                embed.add_field(name="", value=block, inline=False)
        embeds.append(embed)
    return embeds


class Scoreboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def event_type_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The weeklies, read per interaction so a new one needs no restart."""
        try:
            event_types = await self.bot.api.event_types(recurring=True)
        except FzdApiError as error:
            logger.warning("[fzd_show] event type autocomplete could not read the API: %s", error)
            return []
        needle = current.casefold()
        choices = [
            app_commands.Choice(name=event_type["name"], value=str(event_type["event_type_id"]))
            for event_type in event_types
            if needle in event_type["name"].casefold()
        ]
        return choices[:MAX_CHOICES]

    async def event_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """GGP8's events and whatever is running now, earliest first, each once."""
        try:
            ggp8, active = await asyncio.gather(self.bot.api.ggp8_events(), self.bot.api.active_events())
        except FzdApiError as error:
            logger.warning("[setup_scoreboard] event autocomplete could not read the API: %s", error)
            return []
        events = {event["scheduled_event_id"]: event for event in [*ggp8, *active]}
        needle = current.casefold()
        choices = [
            app_commands.Choice(name=event_label(event), value=str(event["scheduled_event_id"]))
            for event in sorted(events.values(), key=lambda event: event["starts_at"])
            if needle in event_label(event).casefold()
        ]
        return choices[:MAX_CHOICES]

    async def group_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The chosen event's divisions or teams; nothing for an ungrouped event
        or before an event is chosen."""
        event = str(interaction.namespace.event or "")
        if not event.isdigit():
            return []
        try:
            detail = await self.bot.api.event_detail(int(event))
        except FzdApiError as error:
            logger.warning("[setup_scoreboard] group autocomplete could not read the API: %s", error)
            return []
        needle = current.casefold()
        choices = [_group_choice(group) for group in detail["groups"] if needle in group["name"].casefold()]
        return choices[:MAX_CHOICES]

    @staticmethod
    async def _refuse(interaction: discord.Interaction, sentence: str) -> None:
        if interaction.response.is_done():
            await interaction.delete_original_response()
            await interaction.followup.send(sentence, ephemeral=True)
        else:
            await interaction.response.send_message(sentence, ephemeral=True)

    async def _post(self, interaction: discord.Interaction, scheduled_event_id: int, group: str | None) -> None:
        """Read the event and its board and post the embeds, ten to a message."""
        try:
            detail = await self.bot.api.event_detail(scheduled_event_id)
            narrowed: dict[str, int] = {}
            if group is not None:
                chosen = _find_group(detail, group)
                if chosen is None:
                    await self._refuse(interaction, "Pick a group from the list.")
                    return
                narrowed = {f"{detail['group_kind']}_id": chosen["group_id"]}
            scoreboard = await self.bot.api.scoreboard(scheduled_event_id, **narrowed)
        except FzdApiError as error:
            logger.warning("[scoreboard] refused for event=%s: %s", scheduled_event_id, error)
            await self._refuse(interaction, error.refusal())
            return

        embeds = _embeds(detail, scoreboard)
        for start in range(0, len(embeds), MAX_EMBEDS):
            await interaction.followup.send(embeds=embeds[start : start + MAX_EMBEDS])

    @app_commands.command(
        name="setup_scoreboard",
        description="Post an event's scoreboard as it stands now (the post does not update)",
    )
    @app_commands.describe(event="Which event", group="One division or team; leave empty for every group")
    async def setup_scoreboard(self, interaction: discord.Interaction, event: str, group: str | None = None):
        if not event.strip().isdigit():
            await self._refuse(interaction, "Pick an event from the list.")
            return
        await interaction.response.defer()
        await self._post(interaction, int(event), group)

    @app_commands.command(name="fzd_show", description="Show most current FZD event scoreboard")
    async def showScoreboard(self, interaction: discord.Interaction, event_type: str | None = None):
        logger.debug("[showScoreboard] Invoked by %s with event_type=%s", interaction.user, event_type)
        await interaction.response.defer()
        try:
            eventinfo = await self.bot.api.latest_event(event_type, datetime.now(timezone.utc))
        except FzdApiError as error:
            if error.status != 404:
                raise
            await self._refuse(interaction, "⚠️  No event found to show. If this is unexpected, contact a mod!")
            return
        await self._post(interaction, eventinfo["scheduled_event_id"], None)

    async def cog_load(self):
        self.showScoreboard.autocomplete("event_type")(self.event_type_autocomplete)
        self.setup_scoreboard.autocomplete("event")(self.event_autocomplete)
        self.setup_scoreboard.autocomplete("group")(self.group_autocomplete)


async def setup(bot: commands.Bot):
    await bot.add_cog(Scoreboard(bot), guild=discord.Object(id=get_settings().server_id))
