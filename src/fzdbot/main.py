import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.error_alerts import send_error_alert
from fzdbot.fzd_api import FzdApi
from fzdbot.fzd_db import init_db_pool
from fzdbot.settings import configure_logging, get_settings

logger = logging.getLogger(__name__)


class FZDBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tree.on_error = self.on_app_command_error

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original_error = getattr(error, "original", error)
        logger.error(
            "Unhandled app command error for command=%s user=%s",
            getattr(interaction.command, "qualified_name", None),
            interaction.user,
            exc_info=(type(original_error), original_error, original_error.__traceback__),
        )
        await send_error_alert(
            self,
            where="app command",
            error=original_error,
            interaction=interaction,
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                "ERROR! Something went wrong, contact FZD staff for help!",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "ERROR! Something went wrong, contact FZD staff for help!",
                ephemeral=True,
            )

    async def setup_hook(self):
        try:
            settings = get_settings()
            self.api = FzdApi(settings.fzd_api_base_url, settings.fzd_api_key)
            self.db_pool = await init_db_pool()
            await self.load_extension("fzdbot.cogs.show_scoreboard")
            await self.load_extension("fzdbot.cogs.scoring")
            await self.load_extension("fzdbot.cogs.events_users_handling")
            await self.load_extension("fzdbot.cogs.event_register")
            logger.info("Loaded extensions")
        except Exception as error:
            logger.exception("Failed to load extensions")
            await send_error_alert(
                self,
                where="setup_hook load extensions",
                error=error,
            )
            raise

        try:
            guild_id = discord.Object(id=settings.server_id)
            # Force sync so bot command changes will appear right away
            synced = await self.tree.sync(guild=guild_id)
            logger.info("Synced %s commands to guild %s", len(synced), guild_id.id)
        except Exception as error:
            logger.exception("Error syncing commands")
            await send_error_alert(
                self,
                where="setup_hook sync commands",
                error=error,
                details={"guild_id": settings.server_id},
            )

    async def close(self) -> None:
        await self.api.close()
        await super().close()

    async def on_ready(self) -> None:
        logger.info("%s is now running", self.user)

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:
        error = sys.exc_info()[1]
        if error is None:
            logger.error("Unhandled exception in event %s without exception info", event_method)
            return

        logger.error(
            "Unhandled exception in event %s",
            event_method,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_error_alert(
            self,
            where=f"event {event_method}",
            error=error,
        )


def main() -> None:
    settings = get_settings()
    configure_logging()
    intents = discord.Intents.default()
    intents.message_content = True  # Required to read message content
    intents.guilds = True
    intents.messages = True

    client = FZDBot(command_prefix="!", intents=intents)
    client.run(token=settings.discord_token)


if __name__ == "__main__":
    main()
