"""`/ggp_submit`, `/ggp_submit_score` and `/ggp_submit_time`: a result on the
running event's active slot, with a machine always named.
`/ggp_show_submissions`: the caller's own results on an event, one line per
slot of its schedule, so the gaps are visible — and beside each result what
the board makes of it, read from the API's own standing for the player:
the multiplier applied, the loss to the leader and the cap on a time event,
and which results the mulligans dropped. Nothing here scores a result.

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

from fzdbot.api_types import (
    EventResponse,
    MachineResponse,
    PlayerResultResponse,
    PlayerStandingResponse,
    SlotResponse,
    SlotResultResponse,
)
from fzdbot.cogs.submissions import (
    DNF,
    TIME_EXAMPLE,
    confirmation,
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
from fzdbot.scoreboards import event_label, format_loss, format_time, scoring_notes, slot_name
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


def result_lines(schedule: list[SlotResponse], standing: PlayerStandingResponse) -> list[str]:
    """One line per slot of the schedule, in its order: the slot, then what
    the caller submitted and what the board makes of it, with the machine,
    ~~struck~~ where the result does not count. Every slot reads
    `not submitted` for a player with no row on the board.
    """
    row = standing["row"]
    if row is None:
        return [f"{slot_name(slot)} — not submitted" for slot in schedule]
    timed = standing["scoring_method"] == "time"
    results = {result["slot_id"]: result for result in row["results"]}
    lines: list[str] = []
    for slot in schedule:
        result = results[slot["slot_id"]]
        shown = _time_mark(result) if timed else _score_mark(result, slot["multiplier"])
        if result["machine"] is not None:
            shown += f" · {result['machine']}"
        if not result["counted"]:
            shown = f"~~{shown}~~ (dropped)"
        lines.append(f"{slot_name(slot)} — {shown}")
    return lines


def _score_mark(result: SlotResultResponse, multiplier: int) -> str:
    """`820`, or `250 ×3 = 750` on a slot that multiplies; `DNF`; `not submitted`."""
    if not result["submitted"]:
        return "not submitted"
    score = result["score"]
    if score is None:
        return DNF_MARK
    return f"{score} ×{multiplier} = {result['value']}" if multiplier != 1 else str(score)


def _time_mark(result: SlotResultResponse) -> str:
    """`2:09.40 (+5.04s)`; `2:29.40 (+25.04s, capped to +20.00s)` where the
    loss exceeds the cap; `DNF (+20.00s)` and `not submitted (+20.00s)`, which
    cost the cap. No loss on a slot nobody has finished, which costs nobody
    anything, nor for a no-finish on an uncapped event, which has no value.
    """
    if not result["submitted"]:
        shown = "not submitted"
    elif result["time_cs"] is None:
        shown = DNF_MARK
    else:
        shown = format_time(result["time_cs"])
    value = result["value"]
    loss = result["loss_cs"]
    if not result["open"] or value is None:
        return shown
    if loss is not None and loss != value:
        return f"{shown} ({format_loss(loss)}, capped to {format_loss(value)})"
    return f"{shown} ({format_loss(value)})"


def standing_footer(schedule: list[SlotResponse], standing: PlayerStandingResponse) -> str:
    """`3 of 5 slots submitted · total 1650 · rank 2`, the total and the rank
    left out where the board has none for the player.
    """
    row = standing["row"]
    if row is None:
        return f"0 of {len(schedule)} slots submitted"
    submitted = sum(result["submitted"] for result in row["results"])
    parts = [f"{submitted} of {len(schedule)} slots submitted"]
    if row["total"] is not None:
        timed = standing["scoring_method"] == "time"
        parts.append(f"total {format_loss(row['total']) if timed else row['total']}")
    if row["rank"] is not None:
        parts.append(f"rank {row['rank']}")
    return " · ".join(parts)


def _value_phrase(method: Method, value: int | None) -> str:
    if value is None:
        return "DNF"
    return f"{value} points" if method == "score" else f"a time of {format_time(value)}"


def _score_notes(slot: SlotResponse, score: int) -> list[str]:
    """The lines after a score's confirmation: what the slot's multiplier
    makes of it, and a warning where it is more than the slot's mode can
    score, which is most often a score multiplied before it was entered.
    """
    notes: list[str] = []
    multiplier = slot["multiplier"]
    if multiplier != 1:
        notes.append(f"With the ×{multiplier} multiplier, that is {score * multiplier} points.")
    ceiling = slot["max_score"]
    if ceiling is not None and score > ceiling:
        notes.append(
            f"⚠️ That is more than the {ceiling} a {slot['mode']} can score. "
            "Did you multiply it first? Submit it again as the game shows it. ⚠️"
        )
    return notes


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
        multiplier = slot["multiplier"] if self.method == "score" else 1
        if multiplier != 1:
            heading += (
                f"\n⚠️ This slot has a ×{multiplier} multiplier. "
                "Enter your score as the game shows it. Do not multiply before submission. ⚠️"
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
                description=self._value_hint(multiplier),
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

    def _value_hint(self, multiplier: int) -> str | None:
        if self.method == "time":
            return 'or write "DNF" if you did not finish'
        return f"before the ×{multiplier} multiplier" if multiplier != 1 else None

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
        await interaction.response.defer(thinking=True, ephemeral=self.cog.confirm_ephemeral)
        await self.cog.write(interaction, self.event, self.slot, self.method, value, self.picked_machine())


class GgpSubmissions(commands.Cog):
    def __init__(self, bot: FZDBot):
        self.bot = bot
        self.confirm_ephemeral = get_settings().submission_confirmation_ephemeral

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
        """Set `value` on the slot for the caller, then confirm, naming what it
        replaced where the caller had a result on the slot. The interaction is
        already deferred, with `confirm_ephemeral`, and the confirmation
        replaces that placeholder.
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

        did = (
            f"submitted {_value_phrase(method, value)} ({machine['name']}) "
            f"for {event_label(event)} {slot_name(slot)}{_replaced_phrase(method, previous)}"
        )
        lines = [confirmation(interaction.user, self.confirm_ephemeral, did)]
        if method == "score" and value is not None:
            lines += _score_notes(slot, value)
        await interaction.followup.send("\n".join(lines), ephemeral=self.confirm_ephemeral)
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
        await interaction.response.defer(ephemeral=self.confirm_ephemeral)
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
            schedule, standing = await asyncio.gather(
                self.bot.api.schedule(chosen["scheduled_event_id"]),
                self.bot.api.player_standing(interaction.user.id, chosen["scheduled_event_id"]),
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

        notes = scoring_notes(standing, standing["scoring_method"] == "time")
        lines = result_lines(schedule, standing)
        embed = discord.Embed(
            title=event_label(chosen), description="\n".join([*notes, "", *lines] if notes else lines)
        )
        embed.set_footer(text=standing_footer(schedule, standing))
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_load(self) -> None:
        for command in (self.ggp_submit_score, self.ggp_submit_time):
            command.autocomplete("machine")(self.machine_autocomplete)
        self.ggp_show_submissions.autocomplete("event")(self.event_autocomplete)


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(GgpSubmissions(bot), guild=discord.Object(id=get_settings().server_id))
