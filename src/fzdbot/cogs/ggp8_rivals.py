"""`/ggp8_rivals`, `/ggp8_rivals_delete` and `/ggp8_rivals_show`: name, clear,
or look over your rivals for GGP8's events.

GGP8-shaped on purpose and deleted with GGP8. The events offered are the ones
the API says are GGP8's and run a Rival Challenge; the players offered are that
event's registrants. The bot holds no event id, no event name and no list of
its own — every pick goes straight to the API, which owns the rules and answers
the refusal a user sees.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.formatters import format_discord_timestamp
from fzdbot.fzd_api import FzdApiError
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

MAX_CHOICES = 25  # Discord accepts at most 25 autocomplete results
MAX_CHOICE_NAME = 100  # and at most 100 characters per choice name


def _event_label(event: dict[str, Any]) -> str:
    return event["display_name"] or event["event"]


def _player_name(player: dict[str, Any]) -> str:
    return player["tag"] or player["discord_user_name"] or player["discord_user_id"]


def _group(registration: dict[str, Any]) -> str | None:
    """The division or team a registration row names. An event runs on one or
    the other, so exactly one is set."""
    return registration["division"] or registration["team"]


def _matches(typed: str, *names: str | None) -> bool:
    needle = typed.casefold()
    return any(needle in name.casefold() for name in names if name)


def _names(players: list[dict[str, Any]], empty: str) -> str:
    """A quoted block, one player per line, or the placeholder in italics."""
    if not players:
        return f"> *{empty}*"
    return "\n".join(f"> **{_player_name(player)}**" for player in players)


def _event_field(event: dict[str, Any], challengers: list[dict[str, Any]]) -> tuple[str, str]:
    """One embed field: the event as its name, and under it the caller's pick
    and everyone who picked them, each as a quoted block."""
    starts_at = format_discord_timestamp(datetime.fromisoformat(event["starts_at"]))
    when = f"🔒 Started {starts_at}" if event["locked"] else f"Starts {starts_at}"

    if event["rival"]:
        rival = f"> **{_player_name(event['rival']['player'])}**"
    elif not event["registered"]:
        rival = "> *You are not registered*"
    elif event["locked"]:
        rival = "> *No rival named*"
    else:
        rival = "> *No rival yet — `/ggp8_rivals` to name one*"

    picked_you = _names([challenger["player"] for challenger in challengers], "Nobody yet")
    return _event_label(event), f"{when}\n\n🎯 **Your rival**\n{rival}\n\n⚔️ **Picked you**\n{picked_you}"


def _refusal(error: FzdApiError) -> str:
    """What to tell the user. A 4xx carries the API's own sentence about the
    rule that refused the pick; anything else is the client's description."""
    if error.detail and error.status in (404, 409, 422):
        return error.detail
    return str(error)


class Ggp8Rivals(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def event_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """GGP8's events that run a Rival Challenge. `/v1/ggp8/events` is the
        list, and the caller's rivals overview says which of them take a pick,
        so an event without one (Yahtzee) is never offered and no name is
        written here to exclude it."""
        try:
            events, overview = await asyncio.gather(
                self.bot.api.ggp8_events(),
                self.bot.api.rivals(interaction.user.id, datetime.now(timezone.utc)),
            )
        except FzdApiError as error:
            logger.warning("[ggp8_rivals] event autocomplete could not read the API: %s", error)
            return []

        rival_event_ids = {event["scheduled_event_id"] for event in overview["events"]}
        choices = [
            app_commands.Choice(name=_event_label(event), value=str(event["scheduled_event_id"]))
            for event in events
            if event["scheduled_event_id"] in rival_event_ids and _matches(current, _event_label(event))
        ]
        return choices[:MAX_CHOICES]

    async def player_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Everybody registered for the chosen event, in every division, narrowed
        to the names containing what has been typed so far.

        A player outside the caller's own division is marked, not hidden: the
        pick is allowed, and the mark is what tells the caller they will be
        chasing a score rather than a placing.
        """
        event = interaction.namespace.event
        if not event or not str(event).isdigit():
            return []
        try:
            registrations = await self.bot.api.ggp8_registrations()
        except FzdApiError as error:
            logger.warning("[ggp8_rivals] player autocomplete could not read the API: %s", error)
            return []

        # A registrant with no stored Discord id cannot be named in a pick.
        players = [
            row for row in registrations if row["scheduled_event_id"] == int(event) and row["discord_user_id"]
        ]
        own = next((row for row in players if int(row["discord_user_id"]) == interaction.user.id), None)
        own_group = _group(own) if own is not None else None

        choices = []
        for row in sorted(players, key=lambda row: _player_name(row).casefold()):
            if row is own or not _matches(current, row["tag"], row["discord_user_name"]):
                continue
            label = _player_name(row)
            if own_group is not None and _group(row) != own_group:
                label = f"{label} — {_group(row)} (another division)"
            choices.append(app_commands.Choice(name=label[:MAX_CHOICE_NAME], value=row["discord_user_id"]))
        return choices[:MAX_CHOICES]

    @app_commands.command(name="ggp8_rivals", description="Name your rival for a GGP8 event")
    @app_commands.describe(event="The GGP8 event", user="The player you want to beat, from any division")
    async def set_rival(self, interaction: discord.Interaction, event: str, user: str) -> None:
        if not event.isdigit() or not user.isdigit():
            await interaction.response.send_message(
                "Pick the event and the player from the lists the command offers.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.api.choose_rival(
                interaction.user.id, int(event), int(user), datetime.now(timezone.utc)
            )
        except FzdApiError as error:
            await interaction.followup.send(f"❌ {_refusal(error)}", ephemeral=True)
            return

        rival = result["rival"]["player"]
        await interaction.followup.send(
            f"🎯 Your rival for **{_event_label(result)}** is now **{_player_name(rival)}**. "
            "Run the command again to change it, or `/ggp8_rivals_delete` to remove it.",
            ephemeral=True,
        )

    @app_commands.command(name="ggp8_rivals_delete", description="Remove your rival for a GGP8 event")
    @app_commands.describe(event="The GGP8 event")
    async def delete_rival(self, interaction: discord.Interaction, event: str) -> None:
        if not event.isdigit():
            await interaction.response.send_message(
                "Pick the event from the list the command offers.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.api.withdraw_rival(
                interaction.user.id, int(event), datetime.now(timezone.utc)
            )
        except FzdApiError as error:
            await interaction.followup.send(f"❌ {_refusal(error)}", ephemeral=True)
            return

        await interaction.followup.send(
            f"You no longer have a rival for **{_event_label(result)}**.", ephemeral=True
        )

    @app_commands.command(
        name="ggp8_rivals_show", description="Your rivals, and who has picked you, per GGP8 event"
    )
    async def show_rivals(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            overview = await self.bot.api.rivals(interaction.user.id, datetime.now(timezone.utc))
        except FzdApiError as error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return

        embed = discord.Embed(
            title="⚔️ Rival Challenge",
            description="Beat your rival's final score to earn the role and the bragging rights.",
            colour=discord.Colour.gold(),
        )
        for event in overview["events"]:
            challengers = [
                challenger
                for challenger in overview["challengers"]
                if challenger["scheduled_event_id"] == event["scheduled_event_id"]
            ]
            if not event["registered"] and not challengers:
                continue
            name, value = _event_field(event, challengers)
            embed.add_field(name=name, value=value, inline=False)

        if not embed.fields:
            embed.description = (
                "You are not registered for any event running a Rival Challenge, and nobody has picked you."
                if overview["events"]
                else "No event is running a Rival Challenge right now."
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_load(self) -> None:
        self.set_rival.autocomplete("event")(self.event_autocomplete)
        self.set_rival.autocomplete("user")(self.player_autocomplete)
        self.delete_rival.autocomplete("event")(self.event_autocomplete)


async def setup(bot: commands.Bot) -> None:
    settings = get_settings()
    await bot.add_cog(Ggp8Rivals(bot), guild=discord.Object(id=settings.server_id))
