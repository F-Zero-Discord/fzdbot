"""`/setup_scoreboard` and `/fzd_show`: post an event's standings.

Both read the event detail and the scoreboard from the API and hand the two to
`scoreboards.render_boards`; the bot holds no event id and no group id.

A live board is one board, so `/setup_scoreboard` takes the division on an
event that has divisions and posts that one message, registered with the API
under its message id. `/fzd_show` posts every board of its event at once, as
one embed each, and registers nothing.

One loop re-reads the registry every `scoreboard_refresh_seconds` while an
event is under way, renders every registered board whose event has started and
edits the message where the render changed, until the event's `ends_at`, when
the board is drawn once more as final and its row deleted. With nothing under
way it sleeps until the next event starts, at most `IDLE_SECONDS`, and
`/setup_scoreboard` restarts it so a new board is not left waiting. Reading the registry each tick is also how a restart resumes: nothing
is held here that the next tick does not read again. A board is stopped early by
deleting its message, or with `/stop_board` where this bot cannot; a message this
bot may not edit is left to whoever owns it until a restart.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from fzdbot.api_types import EventDetailResponse, EventGroupResponse, LiveScoreboardResponse, ScoreboardResponse
from fzdbot.error_alerts import send_error_alert
from fzdbot.formatters import format_discord_timestamp, format_scoreboard_for_discord_embed
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot
from fzdbot.scoreboards import Board, event_label, render_boards
from fzdbot.settings import get_settings

thumbnail = "https://media.discordapp.net/attachments/1399501477608951933/1400792457007861800/Supernova_Server_Icon.png?ex=689c6da3&is=689b1c23&hm=68b8d8790d30689fbad0dfb9341c78921ecf9afecc5919880c81680329c32644&=&format=webp&quality=lossless&width=1024&height=1024"

logger = logging.getLogger(__name__)

MAX_CHOICES = 25  # Discord accepts at most 25 autocomplete results

IDLE_SECONDS = 3600
"""The longest the loop sleeps when no event is under way: how late it notices a
board registered by something other than `/setup_scoreboard`, or an event moved
earlier."""


type Details = dict[int, EventDetailResponse]
"""One tick's event details, per event."""

type Scoreboards = dict[int, ScoreboardResponse]
"""One tick's scoreboards, per event. An event yet to start has none: there is
nothing on a board before it."""


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


def _boards(detail: EventDetailResponse, scoreboard: ScoreboardResponse) -> dict[int | None, Board]:
    """Every board the event draws, in order, by the division it is of. The
    `None` key is the one board of an event without divisions, and on a
    division event the board of the players it lists with no division.
    """
    settings = get_settings()
    boards = render_boards(
        detail, scoreboard, podium=settings.scoreboard_display_podium, debug=settings.debug_scoreboard
    )
    return {board.group_id: board for board in boards}


def _ended(detail: EventDetailResponse, now: datetime) -> bool:
    return now >= datetime.fromisoformat(detail["ends_at"])


def _embed(detail: EventDetailResponse, board: Board, *, now: datetime) -> discord.Embed:
    starts_at = datetime.fromisoformat(detail["starts_at"])
    heading = [f"*Played on {format_discord_timestamp(starts_at)}*"]
    if now < starts_at:
        heading.append("**Not started yet**")
    elif _ended(detail, now):
        heading.append("**Final results**")
    title = f"{event_label(detail)} - {board.title}" if board.title else event_label(detail)
    embed = discord.Embed(title=title, description="\n".join([*heading, *board.notes]))
    if not board.lines:
        embed.add_field(name="", value="NO RESULTS TO DISPLAY YET", inline=False)
        return embed
    embed.set_thumbnail(url=thumbnail)
    for block in format_scoreboard_for_discord_embed(
        board.lines, max_num_lines=get_settings().scoreboard_lines_per_block
    ):
        embed.add_field(name="", value=block, inline=False)
    return embed


class Scoreboard(commands.Cog):
    def __init__(self, bot: FZDBot):
        self.bot = bot
        # Both derived from the last tick and disposable: what each live message
        # was last edited to, so an unchanged board costs no edit, and which
        # boards are failing, so a board that fails every tick alerts once.
        # After a restart every board is edited once and may alert once more.
        self._rendered: dict[int, object] = {}
        self._failing: set[int] = set()
        # Messages this bot cannot edit: another instance's board, or a channel it
        # cannot see. Skipped until a restart; the registry is not this bot's to clear.
        self._unreachable: set[int] = set()
        # Declared, not built: `cog_load` builds it from `_tick`, which reads it back.
        self.refresh: tasks.Loop[Any]

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
        try:
            events = await self.bot.api.calendar()
        except FzdApiError as error:
            logger.warning("[setup_scoreboard] event autocomplete could not read the API: %s", error)
            return []
        needle = current.casefold()
        choices = [
            app_commands.Choice(name=event_label(event), value=str(event["scheduled_event_id"]))
            for event in events
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
        if detail["group_kind"] != "division":
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
        """Read the event and its whole board and post it."""
        try:
            detail = await self.bot.api.event_detail(scheduled_event_id)
            scoreboard = await self.bot.api.scoreboard(scheduled_event_id)
        except FzdApiError as error:
            logger.warning("[scoreboard] refused for event=%s: %s", scheduled_event_id, error)
            await self._refuse(interaction, error.refusal())
            return

        now = datetime.now(UTC)
        boards = _boards(detail, scoreboard)
        await interaction.followup.send(embeds=[_embed(detail, board, now=now) for board in boards.values()])

    @app_commands.command(
        name="setup_scoreboard",
        description="Post an event's scoreboard here and keep it current until the event ends",
    )
    @app_commands.describe(event="Which event", group="Which division; an event without divisions needs none")
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
            if detail["group_kind"] == "division" and group is None:
                await self._refuse(interaction, "This event has divisions; name the one this board is for.")
                return
            chosen = _find_group(detail, group) if group is not None else None
            scoreboard = await self.bot.api.scoreboard(scheduled_event_id)
            board = _boards(detail, scoreboard).get(chosen["group_id"] if chosen is not None else None)
            if board is None:
                await self._refuse(interaction, "Pick a division from the list.")
                return
            message = await channel.send(embed=_embed(detail, board, now=datetime.now(UTC)))
            await self.bot.api.register_scoreboard(
                message.id, channel.id, scheduled_event_id, division_id=board.group_id
            )
            # The loop may be sleeping until a later event; this board is due now.
            self.refresh.restart()
        except FzdApiError as error:
            logger.warning("[setup_scoreboard] refused for event=%s: %s", scheduled_event_id, error)
            await self._refuse(interaction, error.refusal())
            return
        await interaction.followup.send(
            f"Posted the board for {event_label(detail)}. It updates every "
            f"{get_settings().scoreboard_refresh_seconds} s until the event ends; delete the message to stop it.",
            ephemeral=True,
        )

    async def refresh_boards(self) -> float:
        """One tick: every registered board re-read, and edited where its
        render changed. A board's failure is logged, alerted once, and left for
        the next tick; it stops neither the others nor the loop. Answers the
        seconds until the loop is next of use.
        """
        try:
            boards = await self.bot.api.live_scoreboards()
        except FzdApiError as error:
            logger.warning("[live scoreboard] could not read the registry: %s", error)
            return get_settings().scoreboard_refresh_seconds
        now = datetime.now(UTC)
        details: Details = {}
        scoreboards: Scoreboards = {}
        failed = False
        for board in boards:
            message_id = int(board["message_id"])
            if message_id in self._unreachable:
                continue
            try:
                await self._refresh(board, now, details, scoreboards)
                self._failing.discard(message_id)
            except discord.Forbidden:
                self._unreachable.add(message_id)
                logger.warning(
                    "[live scoreboard] message=%s cannot be edited by this bot; leaving its board alone",
                    message_id,
                )
            except Exception as error:
                failed = True
                logger.exception("[live scoreboard] message=%s event=%s", message_id, board["scheduled_event_id"])
                if message_id not in self._failing:
                    self._failing.add(message_id)
                    await send_error_alert(self.bot, where="live scoreboard", error=error, details=board)
        return self._next_interval(details, now, failed=failed)

    def _next_interval(self, details: Details, now: datetime, *, failed: bool) -> float:
        """The refresh interval while an event is under way, or while a board failed and is
        worth retrying; otherwise the wait until the next event starts, and `IDLE_SECONDS`
        with nothing to wait for.
        """
        refresh = float(get_settings().scoreboard_refresh_seconds)
        if failed:
            return refresh
        waits = [float(IDLE_SECONDS)]
        for detail in details.values():
            starts_at = datetime.fromisoformat(detail["starts_at"])
            if now < starts_at:
                waits.append((starts_at - now).total_seconds() + 1)
            elif now < datetime.fromisoformat(detail["ends_at"]):
                waits.append(refresh)
        return max(1.0, min(waits))

    async def _tick(self) -> None:
        """`tasks.loop` sleeps by the interval, so each tick sets the next one."""
        self.refresh.change_interval(seconds=await self.refresh_boards())

    async def _refresh(
        self,
        board: LiveScoreboardResponse,
        now: datetime,
        details: Details,
        scoreboards: Scoreboards,
    ) -> None:
        message_id = int(board["message_id"])
        event_id = board["scheduled_event_id"]
        try:
            if event_id not in details:
                details[event_id] = await self.bot.api.event_detail(event_id)
            detail = details[event_id]
            if now < datetime.fromisoformat(detail["starts_at"]):
                # A board of an event yet to start carries what the command drew on it.
                return
            if event_id not in scoreboards:
                scoreboards[event_id] = await self.bot.api.scoreboard(event_id)
        except FzdApiError as error:
            if error.status != 404:
                raise
            # The event is cancelled: there is nothing to draw, now or later.
            logger.warning("[live scoreboard] message=%s stopped, event=%s is gone", message_id, event_id)
            await self._stop(message_id)
            return

        scoreboard = scoreboards[event_id]
        drawn = _boards(detail, scoreboard).get(board["division_id"])
        if drawn is None:
            # The event draws no board for this division: nothing to draw, now or later.
            logger.warning(
                "[live scoreboard] message=%s stopped, event=%s draws no board for division=%s",
                message_id,
                event_id,
                board["division_id"],
            )
            await self._stop(message_id)
            return
        final = _ended(detail, now)
        embed = _embed(detail, drawn, now=now)
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
        self._failing.discard(message_id)
        self._unreachable.discard(message_id)

    def _board_label(self, board: LiveScoreboardResponse, events: dict[int, str]) -> str:
        """The event a board draws and the channel it sits in, the two things that tell
        one registered board from another.
        """
        event = events.get(board["scheduled_event_id"], f"event {board['scheduled_event_id']}")
        name = getattr(self.bot.get_channel(int(board["channel_id"])), "name", None)
        where = f"#{name}" if name else f"channel {board['channel_id']}"
        return f"{event} · {where}"

    async def board_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Every registered board, whether or not this bot can reach its message."""
        try:
            boards = await self.bot.api.live_scoreboards()
            events = {event["scheduled_event_id"]: event_label(event) for event in await self.bot.api.calendar()}
        except FzdApiError as error:
            logger.warning("[stop_board] autocomplete could not read the API: %s", error)
            return []
        needle = current.casefold()
        choices = [
            app_commands.Choice(name=label[:100], value=board["message_id"])
            for board in boards
            if needle in (label := self._board_label(board, events)).casefold()
        ]
        return choices[:MAX_CHOICES]

    @app_commands.command(
        name="stop_board", description="Stop a live scoreboard from updating, leaving its message where it is"
    )
    @app_commands.describe(board="Which board")
    async def stop_board(self, interaction: discord.Interaction, board: str):
        if not board.strip().isdigit():
            await self._refuse(interaction, "Pick a board from the list.")
            return
        await interaction.response.defer(ephemeral=True)
        message_id = int(board)
        try:
            await self._stop(message_id)
        except FzdApiError as error:
            logger.warning("[stop_board] refused for message=%s: %s", message_id, error)
            await self._refuse(interaction, error.refusal())
            return
        await interaction.followup.send(
            "Stopped that board. Its message stays where it is and no longer updates.", ephemeral=True
        )
        logger.info("[stop_board] %s stopped message=%s", interaction.user, message_id)

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
        self.stop_board.autocomplete("board")(self.board_autocomplete)
        self.refresh = tasks.loop(seconds=get_settings().scoreboard_refresh_seconds)(self._tick)
        self.refresh.before_loop(self.bot.wait_until_ready)
        self.refresh.start()

    async def cog_unload(self):
        self.refresh.cancel()


async def setup(bot: FZDBot):
    await bot.add_cog(Scoreboard(bot), guild=discord.Object(id=get_settings().server_id))
