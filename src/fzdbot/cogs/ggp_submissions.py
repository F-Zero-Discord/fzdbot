"""`/submit` and `/submit_inline`: a result on the running event, with a
machine named where the event records one, confirmed under a summary of the
caller's results on every slot of the schedule, the ones still to come marked
as not started. `/show_submissions`: the caller's own results on the most
recently started event — the running one while one runs, the one just finished
afterwards — one line per slot of its schedule, so the gaps are visible — and beside each result what
the board makes of it, read from the API's own standing for the player:
the multiplier applied, the loss to the leader and the cap on a time event,
and which results the mulligans dropped. Nothing here scores a result.

Nothing here asks whether an event is GGP8's: a weekly is taken the same
way, so the commands can be tried on one. The player types a value and, where
the event records it, picks a machine, and nothing else: which event and which
slot are read from the clock at the write, so there is no list of slots to read. The
running event is the one whose window holds now, and the active slot the one
of its schedule that started most recently. Any other slot — one that is
over, or one of another event running at the same time — is named with
`/submit_inline`'s `slot`, which offers every slot that has started.

`/submit` is the write as a form: one modal for the running event that
holds a dropdown of its started slots with the active one preselected, one
field for the value and, on an event that records the machine, a radio button
per machine. The form opens on a summary of what the caller holds on each
slot of the schedule, and each started slot in the dropdown repeats its own
line. A slot of
another event running at the same time is `/submit_inline`'s: the form's
field is a score or a time, and its machines are one event's.

`/submit_inline` is the write typed as arguments: one `result`, read as a
score or a time by the event's scoring method, so there is no choice of
command to make; the machine where one is recorded; and a `slot`, any started
slot of any running event, the active slot of the running event left out.
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
    MACHINE_OPTION,
    TIME_EXAMPLE,
    confirmation,
    machine_choices,
    machine_named,
    parse_score,
    parse_time,
    recency,
    refuse,
    slot_choices,
    slot_ids,
    upcoming,
)
from fzdbot.fzd_api import FzdApiError
from fzdbot.lineup_emoji import lineup_emoji
from fzdbot.main import FZDBot
from fzdbot.scoreboards import DNF as DNF_MARK
from fzdbot.scoreboards import event_label, format_loss, format_time, scoring_notes, slot_name
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

Method = Literal["score", "time"]

MODAL_TITLE_MAX = 45  # Discord refuses a longer modal title


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


def started_slots(schedule: list[SlotResponse], now: datetime) -> list[SlotResponse]:
    """Every slot that is not still to come, the one that started most
    recently first: the same list `/submit_inline`'s `slot` offers for the event.
    """
    return sorted((slot for slot in schedule if not upcoming(slot, now)), key=lambda slot: recency(slot, now))


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


def held_text(method: Method, previous: PlayerResultResponse) -> str:
    """`820 (Blue Falcon)`: the value held with the machine named on it, if any."""
    text = held_value(method, previous)
    if previous["machine"] is not None:
        text += f" ({previous['machine']})"
    return text


def multiplier_note(method: Method, slot: SlotResponse) -> str:
    """` · ×3 multiplier` on a score slot that multiplies, else empty."""
    if method == "score" and slot["multiplier"] != 1:
        return f" · ×{slot['multiplier']} multiplier"
    return ""


def slot_note(method: Method, slot: SlotResponse, previous: PlayerResultResponse | None) -> str:
    """What a slot's dropdown entry says under its name: `submitted 820 (Blue
    Falcon) · ×3 multiplier`, or `not submitted`. Plain text: a dropdown
    renders no markdown.
    """
    note = "not submitted" if previous is None else f"submitted {held_text(method, previous)}"
    return note + multiplier_note(method, slot)


def summary_line(
    method: Method, slot: SlotResponse, previous: PlayerResultResponse | None, icon: str, now: datetime
) -> str:
    """The slot's line in the form's heading:
    `<:GPKnight:…> **#1 Knight**: 820 points (Blue Falcon) · ×3 multiplier`,
    `<:MPMini:…> **#2 MP**: *not submitted*` on a started slot without a
    result, or `<:MPMini:…> **#3 MP**: *not started*` on one still to come.
    """
    if previous is None:
        state = "not started" if upcoming(slot, now) else "not submitted"
        return f"{icon}**{slot_name(slot)}**: *{state}*{multiplier_note(method, slot)}"
    value = previous["score"] if method == "score" else previous["time_cs"]
    shown = _value_phrase(method, value)
    if previous["machine"] is not None:
        shown += f" ({previous['machine']})"
    return f"{icon}**{slot_name(slot)}**: {shown}{multiplier_note(method, slot)}"


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
    """`250 points`, `a time of 1:30.44`, or `DNF`."""
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


def summary_block(
    guild: discord.Guild,
    method: Method,
    event: EventResponse,
    schedule: list[SlotResponse],
    results: list[PlayerResultResponse],
    now: datetime,
) -> str:
    """`### Your results on <event>` over one `summary_line` per slot of the
    whole schedule, in its order.
    """
    held = {row["slot_id"]: row for row in results}
    lines = [f"### Your results on {event_label(event)}"]
    for slot in sorted(schedule, key=lambda slot: slot["position"]):
        emoji = lineup_emoji(guild, slot)
        icon = f"{emoji} " if emoji is not None else ""
        lines.append(summary_line(method, slot, held.get(slot["slot_id"]), icon, now))
    return "\n".join(lines)


class SubmitModal(discord.ui.Modal):
    """One form for one event: a summary of what the caller holds on each
    slot of the schedule, a dropdown of the started slots with the active one
    preselected, a field for the value, and a radio button per machine where the event
    records one. The field and the machine are prefilled from what the caller
    holds on the preselected slot, and stay as typed when another slot is
    picked: a modal cannot react to a choice before it is submitted. Discord
    refuses the form without a slot and, where there is one, a machine picked,
    so nothing here checks for either. The slot, the value and the machine
    arrive with the submission; the write is the cog's.
    """

    def __init__(
        self,
        cog: "GgpSubmissions",
        guild: discord.Guild,
        event: EventResponse,
        schedule: list[SlotResponse],
        active: SlotResponse,
        machines: list[MachineResponse],
        results: list[PlayerResultResponse],
        now: datetime,
    ) -> None:
        super().__init__(title=event_label(event)[:MODAL_TITLE_MAX])
        self.cog = cog
        self.event = event
        self.schedule = schedule
        self.method: Method = method_of(event)
        held = {row["slot_id"]: row for row in results}
        previous = held.get(active["slot_id"])

        heading = summary_block(guild, self.method, event, schedule, results, now)
        if held:
            heading += "\n\n-# Submitting to a slot you already hold a result on replaces it."
        self.add_item(discord.ui.TextDisplay(heading))

        self.slot = discord.ui.Select()
        for slot in started_slots(schedule, now):
            self.slot.add_option(
                label=slot_name(slot),
                value=str(slot["slot_id"]),
                description=slot_note(self.method, slot, held.get(slot["slot_id"])),
                emoji=lineup_emoji(guild, slot),
                default=slot["slot_id"] == active["slot_id"],
            )
        self.add_item(discord.ui.Label(text="Slot", component=self.slot))

        self.value = discord.ui.TextInput(
            placeholder="87" if self.method == "score" else TIME_EXAMPLE,
            default=held_value(self.method, previous) if previous is not None else None,
            max_length=10,
        )
        self.add_item(
            discord.ui.Label(
                text="Score" if self.method == "score" else "Time (m:ss.cc)",
                description='or write "DNF" if you did not finish' if self.method == "time" else None,
                component=self.value,
            )
        )

        self.machine: discord.ui.RadioGroup | None = None
        if event["machine_input_required"]:
            self.machine = discord.ui.RadioGroup(required=True)
            for machine in machines:
                self.machine.add_option(
                    label=machine["name"],
                    value=str(machine["machine_id"]),
                    default=previous is not None and previous["machine_id"] == machine["machine_id"],
                )
            self.add_item(discord.ui.Label(text="Machine", component=self.machine))

    def picked_slot(self) -> SlotResponse:
        """The slot the player selected, as the row the dropdown was built from."""
        slot_id = int(self.slot.values[0])
        return next(slot for slot in self.schedule if slot["slot_id"] == slot_id)

    def picked_machine(self) -> MachineResponse | None:
        """The option the player selected, as the machine row it was built from;
        `None` on an event that records no machine.
        """
        if self.machine is None:
            return None
        option = next(option for option in self.machine.options if option.value == self.machine.value)
        return {"machine_id": int(option.value), "name": option.label}

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = parse(self.method, self.value.value)
        except ValueError:
            await refuse(interaction, parse_hint(self.method))
            return
        await interaction.response.defer(thinking=True, ephemeral=self.cog.confirm_ephemeral)
        await self.cog.write(
            interaction, self.event, self.picked_slot(), self.schedule, self.method, value, self.picked_machine()
        )


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
    ) -> tuple[EventResponse, list[SlotResponse], SlotResponse] | None:
        """The running event, its schedule and its active slot, or `None`
        after telling the user why there is nothing to submit to.
        """
        running = await self.bot.api.active_events()
        if not running:
            await refuse(interaction, "No event is running right now.")
            return None
        if len(running) > 1:
            names = " and ".join(event_label(event) for event in running)
            await refuse(interaction, f"{names} are both running right now; name the slot with `slot`.")
            return None
        event = running[0]

        schedule = await self.bot.api.schedule(event["scheduled_event_id"])
        slot = active_slot(schedule, now)
        if slot is not None:
            return event, schedule, slot

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
        schedule: list[SlotResponse],
        method: Method,
        value: int | None,
        machine: MachineResponse | None,
    ) -> None:
        """Set `value` on the slot for the caller, then confirm, naming what it
        replaced where the caller had a result on the slot, over the caller's
        results on the whole `schedule` read back after the write. A missing machine on an
        event that records one is the API's to refuse. The interaction is
        already deferred, with `confirm_ephemeral`, and the confirmation
        replaces that placeholder.
        """
        assert interaction.guild is not None  # the commands are registered to one guild
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
                machine["machine_id"] if machine is not None else None,
                now,
            )
            results = await self.bot.api.player_results(interaction.user.id, event["scheduled_event_id"])
        except FzdApiError as error:
            logger.warning("[ggp_submissions] %s refused for user=%s: %s", method, interaction.user, error)
            await refuse(interaction, error.refusal())
            return

        did = f"submitted {_value_phrase(method, value)}"
        if machine is not None:
            did += f" ({machine['name']})"
        did += f" for {event_label(event)} {slot_name(slot)}{_replaced_phrase(method, previous)}"
        lines = [confirmation(interaction.user, self.confirm_ephemeral, did)]
        if method == "score" and value is not None:
            lines += _score_notes(slot, value)
        lines += ["", summary_block(interaction.guild, method, event, schedule, results, now)]
        await interaction.followup.send("\n".join(lines), ephemeral=self.confirm_ephemeral)
        logger.info(
            "[ggp_submissions] user=%s event=%s slot=%s %s=%s",
            interaction.user,
            event["scheduled_event_id"],
            slot["slot_id"],
            method,
            value,
        )

    async def _named(
        self, interaction: discord.Interaction, slot: str
    ) -> tuple[EventResponse, list[SlotResponse], SlotResponse] | None:
        """The event, its schedule and the slot a `slot` choice names, or
        `None` after telling the user why there is nothing to submit to. The
        event has to be running still: a choice is offered while it is and can
        be sent after its window closed, and only a running event says whether
        the value is a score or a time.
        """
        ids = await slot_ids(interaction, slot)
        if ids is None:
            return None
        scheduled_event_id, slot_id = ids
        running = await self.bot.api.active_events()
        event = next((row for row in running if row["scheduled_event_id"] == scheduled_event_id), None)
        if event is None:
            await refuse(interaction, "That event is not running any more.")
            return None
        schedule = await self.bot.api.schedule(scheduled_event_id)
        return event, schedule, next(row for row in schedule if row["slot_id"] == slot_id)

    @app_commands.command(
        name="submit_inline", description="Submit your result in one line: the slot that just ran, or one named"
    )
    @app_commands.describe(
        result=f"Your points, or your time like {TIME_EXAMPLE}, or dnf",
        slot="A slot that has started; left out, the one that just ran",
        machine=MACHINE_OPTION,
    )
    async def submit_inline(
        self, interaction: discord.Interaction, result: str, slot: str | None = None, machine: str | None = None
    ) -> None:
        """Whether `result` is a score or a time is the event's to say, so the
        event is read before `result` is parsed.
        """
        await interaction.response.defer(ephemeral=self.confirm_ephemeral)
        now = datetime.now(UTC)
        try:
            active = await (self._active(interaction, now) if slot is None else self._named(interaction, slot))
            if active is None:
                return
            machine_row = machine_named(await self.bot.api.machines(), machine) if machine is not None else None
        except ValueError:
            await refuse(interaction, "Pick a machine from the list.")
            return
        except FzdApiError as error:
            logger.warning("[ggp_submissions] inline could not read the API for user=%s: %s", interaction.user, error)
            await refuse(interaction, error.refusal())
            return
        event, schedule, picked = active
        method = method_of(event)
        try:
            value = parse(method, result)
        except ValueError:
            await refuse(interaction, parse_hint(method))
            return
        await self.write(interaction, event, picked, schedule, method, value, machine_row)

    @app_commands.command(name="submit", description="Submit your result for the slot that just ran, or an earlier one")
    async def submit(self, interaction: discord.Interaction) -> None:
        """The modal is the response, so the reads run inside Discord's
        three-second budget with no deferral; a read that fails is an
        ephemeral sentence instead of the form.
        """
        now = datetime.now(UTC)
        try:
            active = await self._active(interaction, now)
            if active is None:
                return
            event, schedule, slot = active
            results = await self.bot.api.player_results(interaction.user.id, event["scheduled_event_id"])
            machines = await self.bot.api.machines() if event["machine_input_required"] else []
        except FzdApiError as error:
            logger.warning("[ggp_submissions] modal could not read the API for user=%s: %s", interaction.user, error)
            await refuse(interaction, error.refusal())
            return
        assert interaction.guild is not None  # the command is registered to one guild
        await interaction.response.send_modal(
            SubmitModal(self, interaction.guild, event, schedule, slot, machines, results, now)
        )

    @app_commands.command(
        name="show_submissions", description="Your results on the running or most recent event, slot by slot"
    )
    async def show_submissions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            chosen = await self.bot.api.latest_event(None, datetime.now(UTC))
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

    async def slot_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await slot_choices(self.bot.api, interaction.user.id, current)

    async def cog_load(self) -> None:
        self.submit_inline.autocomplete("slot")(self.slot_autocomplete)
        self.submit_inline.autocomplete("machine")(self.machine_autocomplete)


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(GgpSubmissions(bot), guild=discord.Object(id=get_settings().server_id))
