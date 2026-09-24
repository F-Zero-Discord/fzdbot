"""`/ggp_submit`, `/ggp_submit_score` and `/ggp_submit_time`: a result on the
running event's active slot, with a machine always named.
`/ggp_show_submissions`: the caller's own results on an event, one line per
slot of its schedule, so the gaps are visible.

The names say GGP, but nothing here asks whether an event is GGP8's: a weekly
is taken the same way, so the commands can be tried on one. The player types
a value and picks a machine, and nothing else: which event and which slot are
read from the clock at the write, so there is no list of slots to read. The
running event is the one whose window holds now, and the active slot the one
of its schedule that started most recently. Any other slot — one that is
over, or one of another event running at the same time — is
`/ggp_edit_score`'s and `/ggp_edit_time`'s, which offer every slot that has
started.

`/ggp_submit` is the same write as a form: one modal that names the slot it
is for, holds one field for the value and a radio button per machine, and
shows what the caller already holds on the slot before they replace it.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.api_types import EventResponse, MachineResponse, PlayerResultResponse, SlotResponse
from fzdbot.cogs.submissions import (
    DNF,
    TIME_EXAMPLE,
    machine_choices,
    machine_named,
    parse_score,
    parse_time,
    recency,
    refuse,
)
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot
from fzdbot.scoreboards import DNF as DNF_MARK
from fzdbot.scoreboards import event_label, format_time, slot_name
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

Method = Literal["score", "time"]

MODAL_TITLE_MAX = 45  # Discord refuses a longer modal title
MAX_CHOICES = 25  # Discord accepts at most 25 autocomplete results


def active_slot(schedule: list[SlotResponse], now: datetime) -> SlotResponse | None:
    """The slot whose `starts_at` has passed most recently — at 20:00:00
    exactly, the one starting at 20:00 — or `None` when no slot has started.
    A slot with no start entered is never active.

    This is what makes the schedule's start times a contract: a slot takes
    results from its own start until the next slot's, so staff space the
    starts by the race plus the time players take to submit, 7 to 8 minutes
    for T&T.
    """
    if not schedule:
        return None
    first = min(schedule, key=lambda slot: recency(slot, now))
    return first if recency(first, now)[0] == 0 else None


def default_event(events: list[EventResponse], now: datetime) -> EventResponse | None:
    """The event that started most recently, or the first to come when none
    has started; `None` when there are no events.
    """
    started = [event for event in events if datetime.fromisoformat(event["starts_at"]) <= now]
    if started:
        return max(started, key=lambda event: datetime.fromisoformat(event["starts_at"]))
    return min(events, key=lambda event: datetime.fromisoformat(event["starts_at"]), default=None)


def method_of(event: EventResponse) -> Method:
    return "time" if event["scoring_method"] == "time" else "score"


def parse(method: Method, text: str) -> int | None:
    return parse_score(text) if method == "score" else parse_time(text)


def parse_hint(method: Method) -> str:
    if method == "score":
        return f"Enter your score as a whole number, like 87, or `{DNF}`."
    return f"Enter your time as minutes, seconds and centiseconds, like `{TIME_EXAMPLE}`, or `{DNF}`."


def held_value(method: Method, row: PlayerResultResponse) -> str:
    """The row's value as the player would type it: `820`, `1:30.44` or `dnf`."""
    value = row["score"] if method == "score" else row["time_cs"]
    if value is None:
        return DNF
    return str(value) if method == "score" else format_time(value)


def result_mark(method: Method, row: PlayerResultResponse) -> str:
    """The row's value as a board spells it: `820`, `1:30.44` or `DNF`."""
    value = row["score"] if method == "score" else row["time_cs"]
    if value is None:
        return DNF_MARK
    return str(value) if method == "score" else format_time(value)


def result_lines(method: Method, schedule: list[SlotResponse], results: list[PlayerResultResponse]) -> list[str]:
    """One line per slot of the schedule, in its order: the slot, then the
    caller's result with its machine, or `not submitted`.
    """
    held = {row["slot_id"]: row for row in results}
    lines: list[str] = []
    for slot in schedule:
        row = held.get(slot["slot_id"])
        if row is None:
            result = "not submitted"
        elif row["machine"] is None:
            result = result_mark(method, row)
        else:
            result = f"{result_mark(method, row)} · {row['machine']}"
        lines.append(f"{slot_name(slot)} — {result}")
    return lines


def _value_phrase(method: Method, value: int | None) -> str:
    if value is None:
        return "DNF"
    return f"a score of {value}" if method == "score" else f"a time of {format_time(value)}"


def _replaced_phrase(method: Method, row: PlayerResultResponse | None) -> str:
    """`, replacing 820` for the row the write replaces, empty when there was none."""
    if row is None:
        return ""
    value = row["score"] if method == "score" else row["time_cs"]
    if value is None:
        return ", replacing a DNF"
    return f", replacing {value if method == 'score' else format_time(value)}"


class SubmitModal(discord.ui.Modal):
    """One form for one slot: the event and slot it was built for, a field for
    the value, and a radio button per machine. Discord refuses the form without
    a machine picked, so nothing here checks for one. The value and the machine
    arrive with the submission; the write is the cog's.
    """

    def __init__(
        self,
        cog: "GgpSubmissions",
        event: EventResponse,
        slot: SlotResponse,
        machines: list[MachineResponse],
        previous: PlayerResultResponse | None,
    ) -> None:
        super().__init__(title=f"{event['event']} · {slot_name(slot)}"[:MODAL_TITLE_MAX])
        self.cog = cog
        self.event = event
        self.slot = slot
        self.method: Method = method_of(event)

        heading = f"**You are now submitting to**\n{event_label(event)} {slot_name(slot)}"
        if previous is not None:
            set_at = int(datetime.fromisoformat(previous["modified_dt"]).timestamp())
            heading += (
                f"\nYou already submitted {held_value(self.method, previous)} ({previous['machine']}) "
                f"at <t:{set_at}:t>. Submitting again replaces it."
            )
        self.add_item(discord.ui.TextDisplay(heading))

        self.value = discord.ui.TextInput(
            placeholder="87" if self.method == "score" else TIME_EXAMPLE,
            default=held_value(self.method, previous) if previous is not None else None,
            max_length=10,
        )
        self.add_item(
            discord.ui.Label(
                text="Score" if self.method == "score" else "Time (m:ss.cc)",
                description=f"or {DNF}",
                component=self.value,
            )
        )

        self.machine = discord.ui.RadioGroup(required=True)
        for machine in machines:
            self.machine.add_option(
                label=machine["name"],
                value=str(machine["machine_id"]),
                default=previous is not None and previous["machine_id"] == machine["machine_id"],
            )
        self.add_item(discord.ui.Label(text="Machine", component=self.machine))

    def picked_machine(self) -> MachineResponse:
        """The option the player selected, as the machine row it was built from."""
        option = next(option for option in self.machine.options if option.value == self.machine.value)
        return {"machine_id": int(option.value), "name": option.label}

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = parse(self.method, self.value.value)
        except ValueError:
            await refuse(interaction, parse_hint(self.method))
            return
        await interaction.response.defer(thinking=True)
        await self.cog.write(interaction, self.event, self.slot, self.method, value, self.picked_machine())


class GgpSubmissions(commands.Cog):
    def __init__(self, bot: FZDBot):
        self.bot = bot

    async def machine_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await machine_choices(self.bot.api, current)

    async def _active(
        self, interaction: discord.Interaction, now: datetime
    ) -> tuple[EventResponse, SlotResponse] | None:
        """The running event and its active slot, or `None` after telling
        the user why there is nothing to submit to.
        """
        running = await self.bot.api.active_events()
        if not running:
            await refuse(interaction, "No event is running right now.")
            return None
        if len(running) > 1:
            names = " and ".join(event_label(event) for event in running)
            await refuse(interaction, f"{names} are both running right now; use `/ggp_edit_score` to pick the slot.")
            return None
        event = running[0]

        schedule = await self.bot.api.schedule(event["scheduled_event_id"])
        slot = active_slot(schedule, now)
        if slot is not None:
            return event, slot

        if not schedule:
            sentence = "The running event has no schedule entered, so it cannot take results. Tell FZD staff."
        else:
            soonest = min(schedule, key=lambda slot: recency(slot, now))
            if soonest["starts_at"] is None:
                sentence = "The running event's schedule has no start times entered. Tell FZD staff."
            else:
                starts = int(datetime.fromisoformat(soonest["starts_at"]).timestamp())
                sentence = (
                    f"{event_label(event)} starts with {slot_name(soonest)} <t:{starts}:R>; nothing to submit to yet."
                )
        await refuse(interaction, sentence)
        return None

    async def write(
        self,
        interaction: discord.Interaction,
        event: EventResponse,
        slot: SlotResponse,
        method: Method,
        value: int | None,
        machine: MachineResponse,
    ) -> None:
        """Set `value` on the slot for the caller, then confirm publicly, naming
        what it replaced where the caller had a result on the slot. The
        interaction is already deferred publicly.
        """
        now = datetime.now(UTC)
        try:
            results = await self.bot.api.player_results(interaction.user.id, event["scheduled_event_id"])
            previous = next((row for row in results if row["slot_id"] == slot["slot_id"]), None)
            api_write = self.bot.api.set_score if method == "score" else self.bot.api.set_time
            await api_write(
                interaction.user.id,
                interaction.user.name,
                event["scheduled_event_id"],
                slot["slot_id"],
                value,
                machine["machine_id"],
                now,
            )
        except FzdApiError as error:
            logger.warning("[ggp_submissions] %s refused for user=%s: %s", method, interaction.user, error)
            await refuse(interaction, error.refusal())
            return

        await interaction.followup.send(
            f"✅ User {interaction.user.display_name} has set {_value_phrase(method, value)} ({machine['name']}) "
            f"for {event_label(event)} {slot_name(slot)}{_replaced_phrase(method, previous)}."
        )
        logger.info(
            "[ggp_submissions] user=%s event=%s slot=%s %s=%s",
            interaction.user,
            event["scheduled_event_id"],
            slot["slot_id"],
            method,
            value,
        )

    async def _submit(self, interaction: discord.Interaction, method: Method, value: int | None, machine: str) -> None:
        """Resolve the active slot and the machine named, then `write`."""
        await interaction.response.defer()
        try:
            active = await self._active(interaction, datetime.now(UTC))
            if active is None:
                return
            machine_row = machine_named(await self.bot.api.machines(), machine)
        except ValueError:
            await refuse(interaction, "Pick a machine from the list.")
            return
        except FzdApiError as error:
            logger.warning(
                "[ggp_submissions] %s could not read the API for user=%s: %s", method, interaction.user, error
            )
            await refuse(interaction, error.refusal())
            return
        event, slot = active
        await self.write(interaction, event, slot, method, value, machine_row)

    @app_commands.command(name="ggp_submit", description="Submit your result for the GGP slot that just ran")
    async def ggp_submit(self, interaction: discord.Interaction) -> None:
        """The modal is the response, so the reads run inside Discord's
        three-second budget with no deferral; a read that fails is an
        ephemeral sentence instead of the form.
        """
        try:
            active = await self._active(interaction, datetime.now(UTC))
            if active is None:
                return
            event, slot = active
            machines, results = await asyncio.gather(
                self.bot.api.machines(),
                self.bot.api.player_results(interaction.user.id, event["scheduled_event_id"]),
            )
        except FzdApiError as error:
            logger.warning("[ggp_submissions] modal could not read the API for user=%s: %s", interaction.user, error)
            await refuse(interaction, error.refusal())
            return
        previous = next((row for row in results if row["slot_id"] == slot["slot_id"]), None)
        await interaction.response.send_modal(SubmitModal(self, event, slot, machines, previous))

    @app_commands.command(name="ggp_submit_score", description="Set your score for the GGP slot that just ran")
    @app_commands.describe(score="Your points, or dnf", machine="The machine you raced")
    async def ggp_submit_score(self, interaction: discord.Interaction, score: str, machine: str) -> None:
        try:
            points = parse_score(score)
        except ValueError:
            await refuse(interaction, parse_hint("score"))
            return
        await self._submit(interaction, "score", points, machine)

    @app_commands.command(name="ggp_submit_time", description="Set your time for the GGP slot that just ran")
    @app_commands.describe(
        time=f"Minutes, seconds and centiseconds, like {TIME_EXAMPLE}, or dnf", machine="The machine you raced"
    )
    async def ggp_submit_time(self, interaction: discord.Interaction, time: str, machine: str) -> None:
        try:
            time_cs = parse_time(time)
        except ValueError:
            await refuse(interaction, parse_hint("time"))
            return
        await self._submit(interaction, "time", time_cs, machine)

    async def event_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            events = await self.bot.api.ggp8_and_active_events()
        except FzdApiError as error:
            logger.warning("[ggp_submissions] event autocomplete could not read the API: %s", error)
            return []
        typed = current.casefold()
        return [
            app_commands.Choice(name=event_label(event), value=str(event["scheduled_event_id"]))
            for event in events
            if typed in event_label(event).casefold()
        ][:MAX_CHOICES]

    @app_commands.command(name="ggp_show_submissions", description="Your results on an event, slot by slot")
    @app_commands.describe(event="A GGP8 event or one running now; left out, the one running or up next")
    async def ggp_show_submissions(self, interaction: discord.Interaction, event: str | None = None) -> None:
        if event is not None and not event.isdigit():
            await refuse(interaction, "Pick the event from the list the command offers.")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            events = await self.bot.api.ggp8_and_active_events()
            if event is None:
                chosen = default_event(events, datetime.now(UTC))
                if chosen is None:
                    await interaction.followup.send(
                        "No GGP8 event is scheduled and nothing is running.", ephemeral=True
                    )
                    return
            else:
                chosen = next((row for row in events if row["scheduled_event_id"] == int(event)), None)
                if chosen is None:
                    await interaction.followup.send("Pick the event from the list the command offers.", ephemeral=True)
                    return
            schedule, results = await asyncio.gather(
                self.bot.api.schedule(chosen["scheduled_event_id"]),
                self.bot.api.player_results(interaction.user.id, chosen["scheduled_event_id"]),
            )
        except FzdApiError as error:
            logger.warning("[ggp_submissions] show could not read the API for user=%s: %s", interaction.user, error)
            await interaction.followup.send(error.refusal(), ephemeral=True)
            return

        if not schedule:
            await interaction.followup.send(
                f"{event_label(chosen)} has no schedule entered, so it cannot take results. Tell FZD staff.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=event_label(chosen), description="\n".join(result_lines(method_of(chosen), schedule, results))
        )
        held = {row["slot_id"] for row in results}
        submitted = sum(slot["slot_id"] in held for slot in schedule)
        embed.set_footer(text=f"{submitted} of {len(schedule)} slots submitted")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_load(self) -> None:
        for command in (self.ggp_submit_score, self.ggp_submit_time):
            command.autocomplete("machine")(self.machine_autocomplete)
        self.ggp_show_submissions.autocomplete("event")(self.event_autocomplete)


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(GgpSubmissions(bot), guild=discord.Object(id=get_settings().server_id))
