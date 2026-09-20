import logging
from collections.abc import Mapping

import discord
from discord.ext import commands

from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)


def _truncate(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _build_error_alert_message(
    *,
    where: str,
    error: BaseException,
    interaction: discord.Interaction | None,
    details: Mapping[str, object] | None,
) -> str:
    error_summary = str(error).strip() or "no exception message"
    lines = [
        "Bot error alert",
        f"where: `{_truncate(where, 120)}`",
        f"error: `{_truncate(f'{type(error).__name__}: {error_summary}', 400)}`",
    ]

    if interaction is not None:
        command_name = getattr(interaction.command, "qualified_name", None)
        if command_name:
            lines.append(f"command: `/{_truncate(command_name, 80)}`")
        lines.append(f"user: `{_truncate(str(interaction.user), 120)}` ({interaction.user.id})")
        if interaction.channel_id is not None:
            channel_name = str(interaction.channel) if interaction.channel else "unknown"
            lines.append(f"channel: `{_truncate(channel_name, 120)}` ({interaction.channel_id})")

    if details:
        for key, value in details.items():
            if value is None:
                continue
            lines.append(f"{key}: `{_truncate(str(value), 200)}`")

    lines.append("traceback: see journal")

    message = "\n".join(lines)
    if len(message) > 2000:
        return message[:1997] + "..."
    return message


async def send_error_alert(
    bot: commands.Bot,
    *,
    where: str,
    error: BaseException,
    interaction: discord.Interaction | None = None,
    details: Mapping[str, object] | None = None,
) -> None:
    settings = get_settings()
    channel_id = settings.error_alert_channel_id
    if channel_id is None:
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException:
            logger.exception("Failed to fetch configured error alert channel %s", channel_id)
            return

    try:
        await channel.send(
            _build_error_alert_message(
                where=where,
                error=error,
                interaction=interaction,
                details=details,
            )
        )
    except Exception:
        logger.exception("Failed to send error alert to channel %s", channel_id)
