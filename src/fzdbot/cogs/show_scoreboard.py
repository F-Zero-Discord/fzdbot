"""`/setup_scoreboard` and `/fzd_show`: post an event's standings.

Both read the event detail and the scoreboard from the API and hand the two to
`scoreboards.render_boards`; the bot holds no event id and no group id.

`/fzd_show` posts a snapshot. `/setup_scoreboard` posts a board that stays
current: one message per board, sent to the channel the command was run in and
registered with the API under its message id. One loop re-reads the registry
every `scoreboard_refresh_seconds`, renders every registered board and edits
the message where the render changed, until the event's `ends_at`, when the
board is drawn once more as final and its row deleted. Reading the registry
each tick is also how a restart resumes: nothing is held here that the next
tick does not read again. Stopping a board early is deleting its message.
"""

import asyncio
import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from fzdbot.api_types import EventDetailResponse, EventGroupResponse, LiveScoreboardResponse, ScoreboardResponse
from fzdbot.error_alerts import send_error_alert
from fzdbot.formatters import format_discord_timestamp, format_scoreboard_for_discord_embed
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot
from fzdbot.scoreboards import event_label, render_boards
from fzdbot.settings import get_settings

thumbnail = "https://media.discordapp.net/attachments/1399501477608951933/1400792457007861800/Supernova_Server_Icon.png?ex=689c6da3&is=689b1c23&hm=68b8d8790d30689fbad0dfb9341c78921ecf9afecc5919880c81680329c32644&=&format=webp&quality=lossless&width=1024&height=1024"

logger = logging.getLogger(__name__)

MAX_CHOICES = 25  # Discord accepts at most 25 autocomplete results
MAX_EMBEDS = 10  # and at most 10 embeds per message


type Reads = dict[int, tuple[EventDetailResponse, dict[tuple[int | None, int | None], ScoreboardResponse]]]
"""One tick's reads: the detail per event, and the scoreboard per (event, group)."""


def _group_choice(group: EventGroupResponse) -> app_commands.Choice[str]:
    name = group["name"] if not group["alt_name"] else f"{group['name']} ({group['alt_name']})"
    return app_commands.Choice(name=name, value=str(group["group_id"]))


def _find_group(detail: EventDetailResponse, group: str) -> EventGroupResponse | None:
    """The group a choice value names, or one typed by name."""
    wanted = group.strip().casefold()
    for candidate in detail["groups"]:
        if wanted in (str(candidate["group_id"]), candidate["name"].casefold()):
            return candidate
    return None


def _live_filters(detail: EventDetailResponse, group: str | None) -> list[dict[str, int]] | None:
    """What each live board of the event is narrowed to: the named group, or
    with none named, every division of a division event and the whole event
    otherwise. `None` when the name matches no group.
    """
    if group is not None:
        chosen = _find_group(detail, group)
        if chosen is None:
            return None
        return [{f"{detail['group_kind']}_id": chosen["group_id"]}]
    if detail["group_kind"] == "division":
        return [{"division_id": group["group_id"]} for group in detail["groups"]]
    return [{}]


def _embeds(detail: EventDetailResponse, scoreboard: ScoreboardResponse, *, final: bool = False) -> list[discord.Embed]:
    played = f"*Played on {format_discord_timestamp(datetime.fromisoformat(detail['starts_at']))}*"
    heading = [played, "**Final results**"] if final else [played]
    settings = get_settings()
    embeds = []
    for board in render_boards(detail, scoreboard, podium=settings.scoreboard_display_podium):
        title = f"{event_label(detail)} - {board.title}" if board.title else event_label(detail)
        embed = discord.Embed(title=title, description="\n".join([*heading, *board.notes]))
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
    def __init__(self, bot: FZDBot):
        self.bot = bot
        # Both derived from the last tick and disposable: what each live message
        # was last edited to, so an unchanged board costs no edit, and which
        # boards are failing, so a board that fails every tick alerts once.
        # After a restart every board is edited once and may alert once more.
        self._rendered: dict[int, object] = {}
        self._failing: set[int] = set()

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
        or before an event is chosen.
        """
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

    async def _post(self, interaction: discord.Interaction, scheduled_event_id: int) -> None:
        """Read the event and its whole board and post the embeds, ten to a message."""
        try:
            detail = await self.bot.api.event_detail(scheduled_event_id)
            scoreboard = await self.bot.api.scoreboard(scheduled_event_id)
        except FzdApiError as error:
            logger.warning("[scoreboard] refused for event=%s: %s", scheduled_event_id, error)
            await self._refuse(interaction, error.refusal())
            return

        embeds = _embeds(detail, scoreboard)
        for start in range(0, len(embeds), MAX_EMBEDS):
            await interaction.followup.send(embeds=embeds[start : start + MAX_EMBEDS])

    @app_commands.command(
        name="setup_scoreboard",
        description="Post an event's scoreboard here and keep it current until the event ends",
    )
    @app_commands.describe(event="Which event", group="One division or team; leave empty for every group")
    async def setup_scoreboard(self, interaction: discord.Interaction, event: str, group: str | None = None):
        if not event.strip().isdigit():
            await self._refuse(interaction, "Pick an event from the list.")
            return
        # `channel.send`, not the followup: a followup is edited through the
        # interaction's webhook, whose token dies after fifteen minutes.
        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await self._refuse(interaction, "Run this in the channel the board should live in.")
            return
        await interaction.response.defer(ephemeral=True)
        scheduled_event_id = int(event)
        try:
            detail = await self.bot.api.event_detail(scheduled_event_id)
            filters = _live_filters(detail, group)
            if filters is None:
                await self._refuse(interaction, "Pick a group from the list.")
                return
            for narrowed in filters:
                scoreboard = await self.bot.api.scoreboard(scheduled_event_id, **narrowed)
                message = await channel.send(embed=_embeds(detail, scoreboard)[0])
                await self.bot.api.register_scoreboard(message.id, channel.id, scheduled_event_id, **narrowed)
        except FzdApiError as error:
            logger.warning("[setup_scoreboard] refused for event=%s: %s", scheduled_event_id, error)
            await self._refuse(interaction, error.refusal())
            return
        boards = "one board" if len(filters) == 1 else f"{len(filters)} boards"
        await interaction.followup.send(
            f"Posted {boards} for {event_label(detail)}. Each updates every "
            f"{get_settings().scoreboard_refresh_seconds} s until the event ends; delete a message to stop its board.",
            ephemeral=True,
        )

    async def refresh_boards(self) -> None:
        """One tick: every registered board re-read, and edited where its
        render changed. A board's failure is logged, alerted once, and left for
        the next tick; it stops neither the others nor the loop.
        """
        try:
            boards = await self.bot.api.live_scoreboards()
        except FzdApiError as error:
            logger.warning("[live scoreboard] could not read the registry: %s", error)
            return
        now = datetime.now(UTC)
        reads: Reads = {}
        for board in boards:
            message_id = int(board["message_id"])
            try:
                await self._refresh(board, now, reads)
                self._failing.discard(message_id)
            except Exception as error:
                logger.exception("[live scoreboard] message=%s event=%s", message_id, board["scheduled_event_id"])
                if message_id not in self._failing:
                    self._failing.add(message_id)
                    await send_error_alert(self.bot, where="live scoreboard", error=error, details=board)

    async def _refresh(
        self,
        board: LiveScoreboardResponse,
        now: datetime,
        reads: Reads,
    ) -> None:
        message_id = int(board["message_id"])
        event_id = board["scheduled_event_id"]
        narrowed = (board["division_id"], board["team_id"])
        try:
            if event_id not in reads:
                reads[event_id] = (await self.bot.api.event_detail(event_id), {})
            detail, scoreboards = reads[event_id]
            if narrowed not in scoreboards:
                scoreboards[narrowed] = await self.bot.api.scoreboard(
                    event_id, division_id=narrowed[0], team_id=narrowed[1]
                )
        except FzdApiError as error:
            if error.status != 404:
                raise
            # The event is cancelled: there is nothing to draw, now or later.
            logger.warning("[live scoreboard] message=%s stopped, event=%s is gone", message_id, event_id)
            await self._stop(message_id)
            return

        final = now >= datetime.fromisoformat(detail["ends_at"])
        embed = _embeds(detail, scoreboards[narrowed], final=final)[0]
        rendered = embed.to_dict()
        if self._rendered.get(message_id) != rendered:
            message = self.bot.get_partial_messageable(int(board["channel_id"])).get_partial_message(message_id)
            try:
                await message.edit(embed=embed)
            except discord.NotFound:
                logger.info("[live scoreboard] message=%s is gone, stopping its board", message_id)
                await self._stop(message_id)
                return
            self._rendered[message_id] = rendered
        if final:
            await self._stop(message_id)

    async def _stop(self, message_id: int) -> None:
        await self.bot.api.stop_scoreboard(message_id)
        self._rendered.pop(message_id, None)

    @app_commands.command(name="fzd_show", description="Show most current FZD event scoreboard")
    async def show_scoreboard(self, interaction: discord.Interaction, event_type: str | None = None):
        logger.debug("[show_scoreboard] Invoked by %s with event_type=%s", interaction.user, event_type)
        await interaction.response.defer()
        try:
            eventinfo = await self.bot.api.latest_event(event_type, datetime.now(UTC))
        except FzdApiError as error:
            if error.status != 404:
                raise
            await self._refuse(interaction, "⚠️  No event found to show. If this is unexpected, contact a mod!")
            return
        await self._post(interaction, eventinfo["scheduled_event_id"])

    async def cog_load(self):
        self.show_scoreboard.autocomplete("event_type")(self.event_type_autocomplete)
        self.setup_scoreboard.autocomplete("event")(self.event_autocomplete)
        self.setup_scoreboard.autocomplete("group")(self.group_autocomplete)
        self.refresh = tasks.loop(seconds=get_settings().scoreboard_refresh_seconds)(self.refresh_boards)
        self.refresh.before_loop(self.bot.wait_until_ready)
        self.refresh.start()

    async def cog_unload(self):
        self.refresh.cancel()


async def setup(bot: FZDBot):
    await bot.add_cog(Scoreboard(bot), guild=discord.Object(id=get_settings().server_id))
