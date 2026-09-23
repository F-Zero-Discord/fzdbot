"""`/ggp8_rivals`, `/ggp8_rivals_delete` and `/ggp8_rivals_show`: name, clear,
or look over your rivals for GGP8's events. `/ggp8_elite_rival` and
`/ggp8_elite_rival_delete`: name or clear the Elite Rival a player holds in
each of them, beside the regular one.

GGP8-shaped on purpose and deleted with GGP8. The events offered are the ones
the API says are GGP8's and run a Rival Challenge; the players offered are that
event's registrants. The bot holds no event id, no event name and no list of
its own — every pick goes straight to the API, which owns the rules and answers
the refusal a user sees.
"""

import asyncio
import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.api_types import (
    ChallengerResponse,
    EliteRivalEventResponse,
    Ggp8RegistrationResponse,
    RivalCandidateResponse,
    RivalEventResponse,
    RivalPlayerResponse,
)
from fzdbot.formatters import format_discord_timestamp
from fzdbot.fzd_api import FzdApi, FzdApiError
from fzdbot.main import FZDBot
from fzdbot.scoreboards import event_label
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

MAX_CHOICES = 25  # Discord accepts at most 25 autocomplete results
MAX_CHOICE_NAME = 100  # and at most 100 characters per choice name


def _player_name(tag: str | None, discord_user_name: str | None, discord_user_id: str) -> str:
    """What the player has set, in that order; the Discord id is always there."""
    return tag or discord_user_name or discord_user_id


def _rival_name(player: RivalPlayerResponse) -> str:
    return _player_name(player["tag"], player["discord_user_name"], player["discord_user_id"])


def _group(registration: Ggp8RegistrationResponse) -> str | None:
    """The division or team a registration row names. An event runs on one or
    the other, so exactly one is set.
    """
    return registration["division"] or registration["team"]


def _matches(typed: str, *names: str | None) -> bool:
    needle = typed.casefold()
    return any(needle in name.casefold() for name in names if name)


def _names(players: list[RivalPlayerResponse], empty: str) -> str:
    """A quoted block, one player per line, or the placeholder in italics."""
    if not players:
        return f"> *{empty}*"
    return "\n".join(f"> **{_rival_name(player)}**" for player in players)


def _elite_block(elite: EliteRivalEventResponse | None) -> str:
    """The caller's Elite Rival for one event, or nothing where the event has no
    Elite Rival they hold or could pick."""
    if elite is None:
        return ""
    if elite["rival"]:
        rival = f"> **{_rival_name(elite['rival']['player'])}**"
    else:
        rival = "> *None yet — `/ggp8_elite_rival` to pick one*"
    return f"\n\n⭐ **Your Elite Rival**\n{rival}"


def _event_field(
    event: RivalEventResponse, elite: EliteRivalEventResponse | None, challengers: list[ChallengerResponse]
) -> tuple[str, str]:
    """One embed field: the event as its name, and under it the caller's pick,
    their Elite Rival and everyone who picked them, each as a quoted block.
    """
    starts_at = format_discord_timestamp(datetime.fromisoformat(event["starts_at"]))
    when = f"🔒 Started {starts_at}" if event["locked"] else f"Starts {starts_at}"

    if event["rival"]:
        rival = f"> **{_rival_name(event['rival']['player'])}**"
    elif not event["registered"]:
        rival = "> *You are not registered*"
    elif event["locked"]:
        rival = "> *No rival named*"
    else:
        rival = "> *No rival yet — `/ggp8_rivals` to name one*"

    picked_you = _names([challenger["player"] for challenger in challengers], "Nobody yet")
    return (
        event_label(event),
        f"{when}\n\n🎯 **Your rival**\n{rival}{_elite_block(elite)}\n\n⚔️ **Picked you**\n{picked_you}",
    )


def _candidate_option(candidate: RivalCandidateResponse) -> discord.SelectOption:
    return discord.SelectOption(
        label=_player_name(candidate["tag"], candidate["discord_user_name"], candidate["discord_user_id"]),
        description=candidate["group"]["alt_name"] or candidate["group"]["name"],
        value=candidate["discord_user_id"],
    )


def _elite_held(event: EliteRivalEventResponse) -> str:
    """One line: the event, the Elite Rival held in it, and a lock once it has started."""
    assert event["rival"] is not None, "only an event with a pick has a line"
    lock = " 🔒" if event["locked"] else ""
    return f"> **{event_label(event)}**: {_rival_name(event['rival']['player'])}{lock}"


def _elite_event_option(event: EliteRivalEventResponse) -> discord.SelectOption:
    held = f"Now: {_rival_name(event['rival']['player'])}" if event["rival"] else None
    return discord.SelectOption(label=event_label(event), value=str(event["scheduled_event_id"]), description=held)


class EliteEventSelect(discord.ui.Select):
    def __init__(self, parent_view: "EliteRivalView", events: list[EliteRivalEventResponse]) -> None:
        self.parent_view = parent_view
        super().__init__(placeholder="Pick the event", options=[_elite_event_option(event) for event in events])

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.event_chosen(interaction, int(self.values[0]))


class EliteCandidateSelect(discord.ui.Select):
    def __init__(self, parent_view: "EliteRivalView", event: EliteRivalEventResponse) -> None:
        self.parent_view = parent_view
        super().__init__(
            placeholder=f"Pick your Elite Rival for {event_label(event)}",
            options=[_candidate_option(candidate) for candidate in event["candidates"]],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.rival_chosen(interaction, int(self.values[0]))


class EliteRivalView(discord.ui.View):
    """Two steps on one ephemeral message: the event, then that event's Elite
    Rivals, then the write. Choosing the event reads the API again, so the
    candidates shown are the ones registered at that moment.
    """

    def __init__(self, api: FzdApi, open_events: list[EliteRivalEventResponse]) -> None:
        super().__init__(timeout=300)
        self.api = api
        self.event_select = EliteEventSelect(self, open_events)
        self.candidate_select: EliteCandidateSelect | None = None
        self.scheduled_event_id: int | None = None
        self.add_item(self.event_select)

    async def event_chosen(self, interaction: discord.Interaction, scheduled_event_id: int) -> None:
        await interaction.response.defer()
        try:
            elite = await self.api.elite_rival(interaction.user.id, datetime.now(UTC))
        except FzdApiError as error:
            await interaction.edit_original_response(content=f"❌ {error.refusal()}", view=None)
            return
        event = next((event for event in elite["events"] if event["scheduled_event_id"] == scheduled_event_id), None)
        if event is None or not event["candidates"]:
            await interaction.edit_original_response(
                content="That event has no Elite Rival you can pick any more. Run `/ggp8_elite_rival` again.",
                view=None,
            )
            return

        self.scheduled_event_id = scheduled_event_id
        for option in self.event_select.options:
            option.default = option.value == str(scheduled_event_id)
        if self.candidate_select is not None:
            self.remove_item(self.candidate_select)
        self.candidate_select = EliteCandidateSelect(self, event)
        self.add_item(self.candidate_select)
        await interaction.edit_original_response(view=self)

    async def rival_chosen(self, interaction: discord.Interaction, rival_discord_user_id: int) -> None:
        assert self.scheduled_event_id is not None, "the candidates are shown only once an event is chosen"
        await interaction.response.defer()
        try:
            result = await self.api.choose_elite_rival(
                interaction.user.id, self.scheduled_event_id, rival_discord_user_id, datetime.now(UTC)
            )
        except FzdApiError as error:
            await interaction.edit_original_response(content=f"❌ {error.refusal()}", view=None)
            return

        rival = result["rival"]
        assert rival is not None, "the PUT answers the event with the pick just made"
        await interaction.edit_original_response(
            content=f"⭐ Your Elite Rival for **{event_label(result)}** is now **{_rival_name(rival['player'])}**. "
            "Run `/ggp8_elite_rival` again to change it or pick one in another event, "
            "or `/ggp8_elite_rival_delete` to remove it.",
            view=None,
        )
        self.stop()


class Ggp8Rivals(commands.Cog):
    def __init__(self, bot: FZDBot):
        self.bot = bot

    async def event_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """GGP8's events that run a Rival Challenge. `/v1/ggp8/events` is the
        list, and the caller's rivals overview says which of them take a pick,
        so an event without one (Yahtzee) is never offered and no name is
        written here to exclude it.
        """
        try:
            events, overview = await asyncio.gather(
                self.bot.api.ggp8_events(),
                self.bot.api.rivals(interaction.user.id, datetime.now(UTC)),
            )
        except FzdApiError as error:
            logger.warning("[ggp8_rivals] event autocomplete could not read the API: %s", error)
            return []

        rival_event_ids = {event["scheduled_event_id"] for event in overview["events"]}
        choices = [
            app_commands.Choice(name=event_label(event), value=str(event["scheduled_event_id"]))
            for event in events
            if event["scheduled_event_id"] in rival_event_ids and _matches(current, event_label(event))
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
        players = {
            row["discord_user_id"]: row
            for row in registrations
            if row["scheduled_event_id"] == int(event) and row["discord_user_id"] is not None
        }
        own = players.get(str(interaction.user.id))
        own_group = _group(own) if own is not None else None

        named = sorted(
            (_player_name(row["tag"], row["discord_user_name"], discord_user_id), discord_user_id, row)
            for discord_user_id, row in players.items()
        )
        choices = []
        for name, discord_user_id, row in named:
            if row is own or not _matches(current, row["tag"], row["discord_user_name"]):
                continue
            label = name
            if own_group is not None and _group(row) != own_group:
                label = f"{label} — {_group(row)} (another division)"
            choices.append(app_commands.Choice(name=label[:MAX_CHOICE_NAME], value=discord_user_id))
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
            result = await self.bot.api.choose_rival(interaction.user.id, int(event), int(user), datetime.now(UTC))
        except FzdApiError as error:
            await interaction.followup.send(f"❌ {error.refusal()}", ephemeral=True)
            return

        rival = result["rival"]
        assert rival is not None, "the PUT answers the event with the pick just made"
        await interaction.followup.send(
            f"🎯 Your rival for **{event_label(result)}** is now **{_rival_name(rival['player'])}**. "
            "Run the command again to change it, or `/ggp8_rivals_delete` to remove it.",
            ephemeral=True,
        )

    @app_commands.command(name="ggp8_rivals_delete", description="Remove your rival for a GGP8 event")
    @app_commands.describe(event="The GGP8 event")
    async def delete_rival(self, interaction: discord.Interaction, event: str) -> None:
        if not event.isdigit():
            await interaction.response.send_message("Pick the event from the list the command offers.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.api.withdraw_rival(interaction.user.id, int(event), datetime.now(UTC))
        except FzdApiError as error:
            await interaction.followup.send(f"❌ {error.refusal()}", ephemeral=True)
            return

        await interaction.followup.send(f"You no longer have a rival for **{event_label(result)}**.", ephemeral=True)

    @app_commands.command(name="ggp8_rivals_show", description="Your rivals, and who has picked you, per GGP8 event")
    async def show_rivals(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            now = datetime.now(UTC)
            overview, elite = await asyncio.gather(
                self.bot.api.rivals(interaction.user.id, now),
                self.bot.api.elite_rival(interaction.user.id, now),
            )
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
            elite_event = next(
                (row for row in elite["events"] if row["scheduled_event_id"] == event["scheduled_event_id"]), None
            )
            name, value = _event_field(event, elite_event, challengers)
            embed.add_field(name=name, value=value, inline=False)

        if not embed.fields:
            embed.description = (
                "You are not registered for any event running a Rival Challenge, and nobody has picked you."
                if overview["events"]
                else "No event is running a Rival Challenge right now."
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def held_elite_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The events where the caller holds an Elite Rival that can still be cleared."""
        try:
            elite = await self.bot.api.elite_rival(interaction.user.id, datetime.now(UTC))
        except FzdApiError as error:
            logger.warning("[ggp8_rivals] Elite Rival autocomplete could not read the API: %s", error)
            return []

        choices = [
            app_commands.Choice(
                name=f"{event_label(event)}: {_rival_name(event['rival']['player'])}"[:MAX_CHOICE_NAME],
                value=str(event["scheduled_event_id"]),
            )
            for event in elite["events"]
            if event["rival"] and not event["locked"] and _matches(current, event_label(event))
        ]
        return choices[:MAX_CHOICES]

    @app_commands.command(name="ggp8_elite_rival", description="Pick your Elite Rival for a GGP8 event")
    async def elite_rival(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            elite = await self.bot.api.elite_rival(interaction.user.id, datetime.now(UTC))
        except FzdApiError as error:
            await interaction.followup.send(f"❌ {error.refusal()}", ephemeral=True)
            return

        held = [_elite_held(event) for event in elite["events"] if event["rival"]]
        open_events = [event for event in elite["events"] if event["candidates"]]
        lines = ["⭐ **Elite Rival**: one pick in each GGP8 event you play, beside your regular rival."]
        if held:
            lines += ["Your Elite Rivals:", *held]
        if not open_events:
            lines.append("No GGP8 event you are registered for has an Elite Rival you can pick now.")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        lines.append("Pick the event, then your Elite Rival. A new pick replaces the one you hold in that event.")
        await interaction.followup.send(
            "\n".join(lines), view=EliteRivalView(self.bot.api, open_events), ephemeral=True
        )

    @app_commands.command(name="ggp8_elite_rival_delete", description="Remove your Elite Rival for a GGP8 event")
    @app_commands.describe(event="The GGP8 event")
    async def delete_elite_rival(self, interaction: discord.Interaction, event: str) -> None:
        if not event.isdigit():
            await interaction.response.send_message("Pick the event from the list the command offers.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.api.withdraw_elite_rival(interaction.user.id, int(event), datetime.now(UTC))
        except FzdApiError as error:
            await interaction.followup.send(f"❌ {error.refusal()}", ephemeral=True)
            return

        await interaction.followup.send(
            f"You no longer have an Elite Rival for **{event_label(result)}**.", ephemeral=True
        )

    async def cog_load(self) -> None:
        self.set_rival.autocomplete("event")(self.event_autocomplete)
        self.set_rival.autocomplete("user")(self.player_autocomplete)
        self.delete_rival.autocomplete("event")(self.event_autocomplete)
        self.delete_elite_rival.autocomplete("event")(self.held_elite_autocomplete)


async def setup(bot: FZDBot) -> None:
    settings = get_settings()
    await bot.add_cog(Ggp8Rivals(bot), guild=discord.Object(id=settings.server_id))
