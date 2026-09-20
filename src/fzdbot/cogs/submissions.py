"""`/submit_score`, `/submit_time` and `/delete_submission`: a player's result
on one slot of a running event.

A result is set, never added: submitting to a slot again replaces what it held,
and `/delete_submission` returns the slot to nothing submitted. The slot picker
spans every event running now, so a GGP weekend with two events on at once is
one list. The interaction carries the event id and the slot id and nothing
else; which events are open, whether the slot takes a score or a time, and
whether a machine must be named are the API's rules, and its refusal is the
sentence the user reads.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.api_types import MachineResponse, SlotResponse
from fzdbot.fzd_api import FzdApi, FzdApiError
from fzdbot.main import FZDBot
from fzdbot.scoreboards import event_label, format_time, slot_name, track_label, vote_winner
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

MAX_CHOICES = 25  # Discord accepts at most 25 autocomplete results
MAX_CHOICE_NAME = 100  # and at most 100 characters per choice name

# A slot choice carries the event and the slot, both ids, as one value.
SLOT_VALUE = re.compile(r"^(\d+):(\d+)$")
NO_EVENT = "no-event"
NO_SCHEDULE = "no-schedule"

# Minutes, seconds and centiseconds, with any single non-digit between them:
# 1:30.44, 1.30.44 and "1 30 44" are the same time.
TIME = re.compile(r"^0?(\d)\D(\d{2})\D(\d{2})$")
TIME_EXAMPLE = "1:30.44"
DNF = "dnf"


def parse_score(text: str) -> int | None:
    """A whole number, or `None` for a DNF. `ValueError` for anything else."""
    if text.strip().casefold() == DNF:
        return None
    return int(text.strip())


def parse_time(text: str) -> int | None:
    """Centiseconds as entered, or `None` for a DNF. `ValueError` for anything
    that is not minutes, seconds under sixty and centiseconds.
    """
    if text.strip().casefold() == DNF:
        return None
    match = TIME.match(text.strip())
    if match is None:
        raise ValueError(text)
    minutes, seconds, centiseconds = (int(part) for part in match.groups())
    if seconds >= 60:
        raise ValueError(text)
    return (minutes * 60 + seconds) * 100 + centiseconds


def _choice_name(slot: SlotResponse, division_id: int | None) -> str:
    """`Prix #1 Knight League`; `Race #3 99 Mirror Sand Ocean` once the player's lobby has voted."""
    name = f"{slot['kind'].capitalize()} #{slot['position']} {slot['lineup_name'] or slot['lineup_short_name']}"
    winner = vote_winner(slot, division_id) or vote_winner(slot)
    if winner:
        name += f" {track_label(winner['track_name'], winner['track_type'])}"
    return name


def recency(slot: SlotResponse, now: datetime) -> tuple[int, float]:
    """Sort key: the slot that started most recently first, then the ones still
    to come soonest first, then the ones with no start entered.
    """
    if slot["starts_at"] is None:
        return (2, 0.0)
    starts_at = datetime.fromisoformat(slot["starts_at"])
    if starts_at <= now:
        return (0, (now - starts_at).total_seconds())
    return (1, (starts_at - now).total_seconds())


def machine_named(machines: list[MachineResponse], name: str) -> MachineResponse:
    """The machine row named, matched without case. `ValueError` for a name the API does not list."""
    wanted = name.strip().casefold()
    for row in machines:
        if row["name"].casefold() == wanted:
            return row
    raise ValueError(name)


async def machine_choices(api: FzdApi, current: str) -> list[app_commands.Choice[str]]:
    """Every machine whose name contains what has been typed so far."""
    try:
        machines = await api.machines()
    except FzdApiError as error:
        logger.warning("[machines] autocomplete could not read the API: %s", error)
        return []
    needle = current.casefold()
    choices = [
        app_commands.Choice(name=machine["name"], value=machine["name"])
        for machine in machines
        if needle in machine["name"].casefold()
    ]
    return choices[:MAX_CHOICES]


async def refuse(interaction: discord.Interaction, sentence: str) -> None:
    """Ephemeral either way. Once the public deferral is out, its placeholder
    is removed first, so the channel never shows a refusal.
    """
    if interaction.response.is_done():
        await interaction.delete_original_response()
        await interaction.followup.send(sentence, ephemeral=True)
    else:
        await interaction.response.send_message(sentence, ephemeral=True)


class Submissions(commands.Cog):
    def __init__(self, bot: FZDBot):
        self.bot = bot

    async def slot_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Every slot of every event running now, the one raced most recently
        first. One placeholder when there is nothing to pick: no event, or an
        event whose schedule has not been entered.
        """
        try:
            events = await self.bot.api.active_events()
            schedules = await asyncio.gather(*(self.bot.api.schedule(e["scheduled_event_id"]) for e in events))
        except FzdApiError as error:
            logger.warning("[submissions] slot autocomplete could not read the API: %s", error)
            return []

        if not events:
            return [app_commands.Choice(name="No event is running right now", value=NO_EVENT)]

        now = datetime.now(UTC)
        slots = sorted(
            ((event, slot) for event, schedule in zip(events, schedules, strict=False) for slot in schedule),
            key=lambda pair: recency(pair[1], now),
        )
        if not slots:
            return [app_commands.Choice(name="The running event has no schedule entered", value=NO_SCHEDULE)]

        groups = await self._groups(interaction.user.id, schedules, now)
        needle = current.casefold()
        named = [(_choice_name(slot, groups.get(event["scheduled_event_id"])), event, slot) for event, slot in slots]
        choices = [
            app_commands.Choice(name=name, value=f"{event['scheduled_event_id']}:{slot['slot_id']}")
            for name, event, slot in named
            if needle in name.casefold()
        ]
        return choices[:MAX_CHOICES]

    async def _groups(self, discord_user_id: int, schedules: list[list[SlotResponse]], now: datetime) -> dict[int, int]:
        """The group this player holds in each event, by event id. Read only
        when some slot holds a division's vote, which is the one thing here
        that depends on who is asking; a failed read shows the slots unlabelled.
        """
        votes = (vote for schedule in schedules for slot in schedule for vote in slot["vote_winners"])
        if all(vote["division_id"] is None for vote in votes):
            return {}
        try:
            registrations = await self.bot.api.registrations(discord_user_id, now)
        except FzdApiError as error:
            logger.warning("[submissions] slot autocomplete could not read registrations: %s", error)
            return {}
        return {
            event["scheduled_event_id"]: event["your_registration"]["group_id"]
            for event in registrations
            if event["your_registration"]
        }

    async def machine_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await machine_choices(self.bot.api, current)

    async def _machine_id(self, machine: str | None) -> int | None:
        if machine is None:
            return None
        return machine_named(await self.bot.api.machines(), machine)["machine_id"]

    async def _describe(self, scheduled_event_id: int, slot_id: int) -> str:
        """`2026-09-23 | Wacky Wednesday #1 Knight`: the event and the slot as a
        sentence names them, read after the write. Falls back to the ids when a read
        fails: the result is set either way, and the confirmation should say so.
        """
        try:
            events, schedule = await asyncio.gather(
                self.bot.api.active_events(), self.bot.api.schedule(scheduled_event_id)
            )
        except FzdApiError as error:
            logger.warning("[submissions] could not read the event for the confirmation: %s", error)
            return f"event {scheduled_event_id} slot {slot_id}"
        event = next((e for e in events if e["scheduled_event_id"] == scheduled_event_id), None)
        slot = next((s for s in schedule if s["slot_id"] == slot_id), None)
        where = event_label(event) if event else f"event {scheduled_event_id}"
        return f"{where} {slot_name(slot) if slot else f'slot {slot_id}'}"

    async def _confirm(self, interaction: discord.Interaction, scheduled_event_id: int, slot_id: int, did: str) -> None:
        """Public, in the channel the command was run in, so a lobby sees what
        was set. This replaces the deferral's placeholder.
        """
        where = await self._describe(scheduled_event_id, slot_id)
        await interaction.followup.send(f"✅ User {interaction.user.display_name} has {did} for {where}.")

    async def _slot_ids(self, interaction: discord.Interaction, slot: str) -> tuple[int, int] | None:
        """The event and slot ids a choice carries, or `None` after telling the
        user why there is nothing to submit to.
        """
        match = SLOT_VALUE.match(slot.strip())
        if match:
            return int(match.group(1)), int(match.group(2))
        if slot == NO_EVENT:
            sentence = "No event is running right now, so there is nothing to submit to."
        elif slot == NO_SCHEDULE:
            sentence = "The running event has no schedule entered, so it cannot take results. Tell FZD staff."
        else:
            sentence = "Pick a slot from the list."
        await refuse(interaction, sentence)
        return None

    @app_commands.command(name="submit_score", description="Set your score for a slot of the running event")
    @app_commands.describe(
        slot="Which slot the score is for",
        score="Your points, or dnf",
        machine="The machine you raced, where the event records one",
    )
    async def submit_score(
        self, interaction: discord.Interaction, slot: str, score: str, machine: str | None = None
    ) -> None:
        try:
            points = parse_score(score)
        except ValueError:
            await refuse(interaction, f"Enter your score as a whole number, like 87, or `{DNF}`.")
            return
        ids = await self._slot_ids(interaction, slot)
        if ids is None:
            return
        scheduled_event_id, slot_id = ids
        await interaction.response.defer()

        try:
            machine_id = await self._machine_id(machine)
            await self.bot.api.set_score(
                interaction.user.id,
                interaction.user.name,
                scheduled_event_id,
                slot_id,
                points,
                machine_id,
                datetime.now(UTC),
            )
        except ValueError:
            await refuse(interaction, "Pick a machine from the list.")
            return
        except FzdApiError as error:
            logger.warning("[submissions] set_score refused for user=%s: %s", interaction.user, error)
            await refuse(interaction, error.refusal())
            return

        did = "set DNF" if points is None else f"set a score of {points}"
        await self._confirm(interaction, scheduled_event_id, slot_id, did)
        logger.info(
            "[submissions] user=%s event=%s slot=%s score=%s",
            interaction.user,
            scheduled_event_id,
            slot_id,
            points,
        )

    @app_commands.command(name="submit_time", description="Set your time for a slot of the running event")
    @app_commands.describe(
        slot="Which slot the time is for",
        time=f"Minutes, seconds and centiseconds, like {TIME_EXAMPLE}, or dnf",
        machine="The machine you raced, where the event records one",
    )
    async def submit_time(
        self, interaction: discord.Interaction, slot: str, time: str, machine: str | None = None
    ) -> None:
        try:
            time_cs = parse_time(time)
        except ValueError:
            await refuse(
                interaction,
                f"Enter your time as minutes, seconds and centiseconds, like `{TIME_EXAMPLE}`, or `{DNF}`.",
            )
            return
        ids = await self._slot_ids(interaction, slot)
        if ids is None:
            return
        scheduled_event_id, slot_id = ids
        await interaction.response.defer()

        try:
            machine_id = await self._machine_id(machine)
            await self.bot.api.set_time(
                interaction.user.id,
                interaction.user.name,
                scheduled_event_id,
                slot_id,
                time_cs,
                machine_id,
                datetime.now(UTC),
            )
        except ValueError:
            await refuse(interaction, "Pick a machine from the list.")
            return
        except FzdApiError as error:
            logger.warning("[submissions] set_time refused for user=%s: %s", interaction.user, error)
            await refuse(interaction, error.refusal())
            return

        did = "set DNF" if time_cs is None else f"set a time of {format_time(time_cs)}"
        await self._confirm(interaction, scheduled_event_id, slot_id, did)
        logger.info(
            "[submissions] user=%s event=%s slot=%s time_cs=%s",
            interaction.user,
            scheduled_event_id,
            slot_id,
            time_cs,
        )

    @app_commands.command(name="delete_submission", description="Remove your result from a slot of the running event")
    @app_commands.describe(slot="Which slot to clear")
    async def delete_submission(self, interaction: discord.Interaction, slot: str) -> None:
        ids = await self._slot_ids(interaction, slot)
        if ids is None:
            return
        scheduled_event_id, slot_id = ids
        await interaction.response.defer()

        try:
            await self.bot.api.delete_result(interaction.user.id, scheduled_event_id, slot_id, datetime.now(UTC))
        except FzdApiError as error:
            logger.warning("[submissions] delete_result refused for user=%s: %s", interaction.user, error)
            await refuse(interaction, error.refusal())
            return

        await self._confirm(interaction, scheduled_event_id, slot_id, "removed their submission")
        logger.info("[submissions] user=%s event=%s slot=%s removed", interaction.user, scheduled_event_id, slot_id)

    async def cog_load(self) -> None:
        for command in (self.submit_score, self.submit_time, self.delete_submission):
            command.autocomplete("slot")(self.slot_autocomplete)
        for command in (self.submit_score, self.submit_time):
            command.autocomplete("machine")(self.machine_autocomplete)


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(Submissions(bot), guild=discord.Object(id=get_settings().server_id))
