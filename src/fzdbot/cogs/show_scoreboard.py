# Cog class for displaying scoreboard results using bot (/show command)

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.error_alerts import send_error_alert
from fzdbot.fzd_api import FzdApiError
from fzdbot.formatters import (
    format_discord_timestamp,
    format_scoreboard_display_text,
    format_scoreboard_for_discord_embed,
)
from fzdbot.fzd_db import (
    get_db_connection,  # connect_to_database
    get_event_types,
)
from fzdbot.settings import get_settings

thumbnail = "https://media.discordapp.net/attachments/1399501477608951933/1400792457007861800/Supernova_Server_Icon.png?ex=689c6da3&is=689b1c23&hm=68b8d8790d30689fbad0dfb9341c78921ecf9afecc5919880c81680329c32644&=&format=webp&quality=lossless&width=1024&height=1024"

logger = logging.getLogger(__name__)


class Scoreboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def event_type_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        event_choices = [
            app_commands.Choice(name=e["name"], value=str(e["id"]))
            for e in self.recurring_events
            if current.lower() in e["name"].lower()
        ]

        return event_choices[:25]  # Discord only accepts max 25 autocomplete results

    @app_commands.command(
        name="fzd_show", description="Show most current FZD event scoreboard"
    )  # ,  guild=GUILD_ID)
    async def showScoreboard(self, interaction: discord.Interaction, event_type: str | None = None):
        logger.debug("[showScoreboard] Invoked by %s with event_type=%s", interaction.user, event_type)
        try:
            await interaction.response.defer()
            try:
                eventinfo = await self.bot.api.latest_event(event_type, datetime.now(timezone.utc))
            except FzdApiError as error:
                if error.status != 404:
                    raise
                eventinfo = None

            if not eventinfo:
                if event_type:
                    event_name = [e["name"] for e in self.recurring_events if e["id"] == int(event_type)]
                    await interaction.followup.send(
                        f"⚠️  No results found for event_type '{event_name[0]}'! If this is unexpected behavior contact a mod!",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "❌ ERROR! Something unexpected went wrong, contact an FZD mod to help!", ephemeral=True
                    )
                    logger.warning("[showScoreboard] Unknown issue encountered by %s", interaction.user)
            else:
                try:
                    results = await self.bot.api.scoreboard(eventinfo["scheduled_event_id"], interaction.user.id)
                except FzdApiError as error:
                    if error.status != 404:
                        raise
                    results = {"division": None, "entries": []}

                eventdate = datetime.fromisoformat(eventinfo["starts_at"]).astimezone(timezone.utc)
                title = eventinfo["event"]
                eventscoreslist = [
                    {
                        "player": entry["display_name"],
                        "score": entry["points"],
                        "team": entry["team"],
                        "is_qualified": entry["qualified"],
                        "division": results["division"],
                    }
                    for entry in results["entries"]
                ]
                if not eventscoreslist:
                    scoreboard = discord.Embed(
                        title=title, description=f"*Played on {format_discord_timestamp(eventdate)}*"
                    )
                    scoreboard.add_field(name="", value="NO RESULTS TO DISPLAY YET", inline=False)
                else:
                    # Check for division name, put in title if so
                    has_divisions = any(d.get("division") is not None for d in eventscoreslist)
                    if has_divisions:
                        title = title + " - " + eventscoreslist[0]["division"]

                    scoreboard = discord.Embed(
                        title=title, description=f"*Played on {format_discord_timestamp(eventdate)}*"
                    )
                    scoreboard.set_thumbnail(url=thumbnail)

                    ranked_scoreboard = format_scoreboard_display_text(eventscoreslist)
                    settings = get_settings()
                    fields_display_text = format_scoreboard_for_discord_embed(
                        ranked_scoreboard, max_num_lines=settings.scoreboard_lines_per_block
                    )
                    for i, block in enumerate(fields_display_text, start=1):
                        scoreboard.add_field(name="", value=block, inline=False)

                await interaction.followup.send(embed=scoreboard)
        except Exception as error:
            logger.exception(
                "[showScoreboard] Exception user=%r event_type=%r",
                interaction.user,
                event_type,
            )
            await send_error_alert(
                self.bot,
                where="fzd_show",
                error=error,
                interaction=interaction,
                details={"event_type": event_type},
            )
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ ERROR! Something unexpected went wrong, contact an FZD mod to help!", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ ERROR! Something unexpected went wrong, contact an FZD mod to help!", ephemeral=True
                )

    async def cog_load(self):
        # Bind autocomplete handler properly
        self.showScoreboard.autocomplete("event_type")(self.event_type_autocomplete)
        async with get_db_connection() as db:
            self.recurring_events = await get_event_types(db)


async def setup(bot: commands.Bot):
    settings = get_settings()
    GUILD_ID = discord.Object(id=settings.server_id)
    await bot.add_cog(Scoreboard(bot), guild=GUILD_ID)
