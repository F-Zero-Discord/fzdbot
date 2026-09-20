# Miscellaneous bot commands for registering users in the database (/register)
# or modifying events (/start_event only for now)
import logging
import discord
from discord.ext import commands
from discord import app_commands

from fzdbot.error_alerts import send_error_alert
from fzdbot.fzd_api import FzdApiError
from fzdbot.fzd_db import get_db_connection  # connect_to_database
from fzdbot.fzd_db import get_event_types
from fzdbot.fzd_db import check_for_active_event
from fzdbot.fzd_db import create_event
from fzdbot.fzd_db import get_event_schedule
from fzdbot.formatters import format_events_schedule
from fzdbot.utils.user_utils import default_display_name
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)


class Modify_Events_Users(commands.Cog):
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

    # Manually start an event
    @app_commands.command(
        name="fzd_start_event", description="Choose FZD event to start, assuming no other event is ongoing"
    )
    async def startEvent(self, interaction: discord.Interaction, event: str):
        duration = 2  # duration of event (hours), set constant for now
        opts = [
            s["name"] for s in self.recurring_events if "name" in s
        ]  # list of event names (all valid options)
        try:
            event_name = [e["name"] for e in self.recurring_events if e["id"] == int(event)]  # chosen event name
            if (
                not event_name
            ):  # in the rare case user inputs an integer 'event', and above line returns empty list
                raise IndexError("chosen event not part of list")

            async with get_db_connection() as db:
                # Check every hour in the proposed new event for possible overlap with database events
                for hour_to_check in range(0, duration + 1):
                    match_event = await check_for_active_event(db, hours_from_now=hour_to_check)
                    if match_event["name"] != "NULL":
                        message = f"another event is currently running -- {match_event['name']}"
                        if hour_to_check > 0:
                            message = f"another event will start within the next {duration} hours -- {match_event['name']}"
                        await interaction.response.send_message(
                            f"⚠️  Warning: Could not start event, {message}", ephemeral=True
                        )
                        return

                # Event is created here
                current_event = match_event
                current_event["name"] = event_name[0]
                current_event["id"] = int(event)
                await create_event(db, current_event, duration=duration)  # default duration is 2 hours
                await interaction.response.send_message(f"✅ FZD event {event_name[0]} successfully started!")
                logger.info("User %s started event %s", interaction.user, event_name[0])

        except (IndexError, ValueError) as e:
            logger.warning("[startEvent] exception: %s", e)
            await interaction.response.send_message(
                f"❌ ERROR! Must choose from available event options -- {opts}", ephemeral=True
            )
        except Exception as error:
            logger.exception("[startEvent] Unexpected exception")
            await send_error_alert(
                self.bot,
                where="fzd_start_event",
                error=error,
                interaction=interaction,
            )
            await interaction.response.send_message(
                "❌ ERROR! Something went wrong, please contact FZD staff to address!",
                ephemeral=True,
            )

    async def cog_load(self):
        # Bind autocomplete handler properly
        self.startEvent.autocomplete("event")(self.event_type_autocomplete)
        async with get_db_connection() as db:
            self.recurring_events = await get_event_types(db)

    # This command registers a user into the database
    @app_commands.command(name="fzd_set_name", description="Register your discord id to FZD scoreboard database")
    async def registerUser(self, interaction: discord.Interaction, display_name: str):
        warning = ""
        if display_name is None:
            display_name = default_display_name(interaction.user)
        elif len(display_name) > 10:
            display_name = display_name[0:10]
            warning = "⚠️  Warning: display_name should be 10 characters or less (as in F-Zero 99 in game name) \n"

        try:
            await interaction.response.defer(ephemeral=True)
            await self.bot.api.set_tag(interaction.user.id, interaction.user.name, display_name)
            await interaction.followup.send(
                f"{warning}✅  User {interaction.user} is now registered in the FZD database with display name {display_name}",
                ephemeral=True,
            )
            logger.info("User %s set display name to %s", interaction.user, display_name)
        except FzdApiError as error:
            await interaction.followup.send(f"{warning}❌ ERROR! {error}", ephemeral=True)
            logger.warning("[registerUser] API error: %s", error)
        except Exception as error:
            await interaction.followup.send(
                f"{warning}❌ ERROR! Something went wrong, please contact FZD staff to address!", ephemeral=True
            )
            logger.exception("[registerUser] Exception occurred in fzd_register")
            await send_error_alert(
                self.bot,
                where="fzd_register",
                error=error,
                interaction=interaction,
            )

    # @app_commands.command(name="test_async")
    # async def test_async(self, interaction: discord.Interaction, delay: int):
    #    start = time.perf_counter()
    #    await interaction.response.send_message(f"Starting {delay}s delay...", ephemeral=True)
    #    await asyncio.sleep(delay)
    #    end = time.perf_counter()
    #    print(f"Command with delay={delay} done after {end - start:.1f}s")

    @app_commands.command(name="fzd_events_schedule", description="View upcoming FZD events schedule")
    async def viewSchedule(self, interaction: discord.Interaction):
        try:
            async with get_db_connection() as db:
                events = await get_event_schedule(db)
                formatted_lines = format_events_schedule(events)
                schedule = discord.Embed(title="Upcoming FZD Events Schedule", description="")
                schedule.set_thumbnail(
                    url="https://media.discordapp.net/attachments/1399501477608951933/1400792457007861800/Supernova_Server_Icon.png?ex=689c6da3&is=689b1c23&hm=68b8d8790d30689fbad0dfb9341c78921ecf9afecc5919880c81680329c32644&=&format=webp&quality=lossless&width=1024&height=1024"
                )
                schedule.add_field(name="", value=formatted_lines[0], inline=False)
                await interaction.response.send_message(embed=schedule)
        except Exception as error:
            await interaction.response.send_message(
                "❌ ERROR! Something went wrong, please contact FZD staff to address!", ephemeral=True
            )
            logger.exception("[viewSchedule] Exception occurred in fzd_events_schedule")
            await send_error_alert(
                self.bot,
                where="fzd_events_schedule",
                error=error,
                interaction=interaction,
            )


async def setup(bot: commands.Bot):
    settings = get_settings()
    GUILD_ID = discord.Object(id=settings.server_id)
    await bot.add_cog(Modify_Events_Users(bot), guild=GUILD_ID)
