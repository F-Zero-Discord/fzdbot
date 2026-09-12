import time
import asyncio
import logging
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from fzdbot.settings import get_settings
from fzdbot.fzd_api import FzdApiError
from fzdbot.utils.event_class import Event, UserRegistrations, UserStats
from fzdbot.utils.user_utils import default_display_name
from fzdbot.utils.view_utils import NextStep, DivTeam
from fzdbot.views.common import SessionView
from fzdbot.views.register_views import (
    CancelView,
    LoadView,
    RegisterMenuView,
    DivTeamAddView,
    DivTeamEditView,
    ConfirmView,
    ConfirmWithdrawlView,
    ExitView
)
from fzdbot.views.stats_menu_views import (
        StatViewHistoryClassic,
        BasicStatsView
)


logger = logging.getLogger(__name__)


#############################################
# Registration flow
#############################################

# Steps that must not be reached before the user's statistics are in hand.
_STATS_FIRST = frozenset({NextStep.ADD, NextStep.EDIT, NextStep.CONFIRM})


class RegSession:
    """ The state of one /ggp_register conversation.

        A Discord button click is a fresh inbound request, not a reply to the
        coroutine that sent the message, so there is nothing to "wait" for and
        nowhere for conversation state to live on the stack. It lives here
        instead. One session is created per invocation and stays alive as long
        as a view on screen holds a reference to it.

        Views call exactly two methods: advance() when the user picks
        something, and expire() when the user walks away.
    """

    def __init__(self, interaction: discord.Interaction) -> None:
        # origin answers "which message am I editing".
        # latest answers "which interaction token may I still use". Each click
        # arrives with a token good for 15 minutes, so holding the newest one is
        # what lets a session outlive the 15 minutes granted to the slash
        # command itself.
        self.origin: discord.Interaction = interaction
        self.latest: discord.Interaction = interaction

        self.current_view: SessionView | None = None
        self.user: UserRegistrations | None = None
        self.events: list[Event] = []
        self._prefetch: asyncio.Task | None = None

        # The registration currently being worked on.
        self.selected_event: Event | None = None
        self.div_team_str: DivTeam | None = None
        self.div_team_id: int | None = None      # what the user is registered for now
        self.new_div_team_id: int | None = None  # what the user is asking for
        self.user_stats: UserStats | None = None
        self.pending_step: NextStep | None = None  # where to resume after the stats screen
        self.notice: str | None = None  # one line for the menu to show on arrival

        # The answer lists the stats screen offers. They arrive with the answers
        # themselves, in the same call, so the screen needs no read of its own.
        self.recent_options: list[dict] = []
        self.self_eval_options: list[dict] = []


    @property
    def api(self):
        """ The bot's API client. `Interaction.client` is the bot."""
        return self.origin.client.api


    #############################################
    # Loading
    #############################################

    def start_prefetch(self) -> None:
        """ Load from the database while the user reads the rules screen.

            This is what the old TaskGroup was reaching for: start the work,
            give the user something to look at, and await the result the first
            time it is actually needed. By then it is normally already done.
        """
        self._prefetch = asyncio.create_task(self._load())
        self._prefetch.add_done_callback(self._prefetch_done)


    @staticmethod
    def _prefetch_done(task: asyncio.Task) -> None:
        """ If the user never clicks, nobody awaits the task, so retrieve any
            exception here to keep asyncio from complaining at GC time.
        """
        if not task.cancelled() and task.exception() is not None:
            logger.error("ggp_register: prefetch failed", exc_info=task.exception())


    async def _load(self) -> None:
        start = time.perf_counter()
        await self._read_registrations()
        logger.info("ggp_register: initial API call complete in %.4f seconds",
                    time.perf_counter() - start)


    async def _read_registrations(self) -> None:
        """ The events, their groups and where this player stands, in one call.

            The payload carries each group's capacity and headcount, so nothing
            here counts anything, and the caller's registrations come off the
            same answer rather than a second one.
        """
        payload = await self.api.registrations(self.origin.user.id, datetime.now(timezone.utc))
        self.events = [Event.from_api(event) for event in payload]
        self.user = UserRegistrations.from_api(self.origin, payload)


    async def ready(self) -> None:
        """ Block until the prefetch has landed. Cheap once it has.
        """
        if self._prefetch is not None:
            await self._prefetch
            self._prefetch = None


    #############################################
    # Transitions
    #############################################

    async def advance(self, interaction: discord.Interaction,
                      step: NextStep, source: SessionView | None = None) -> None:
        """ Move the flow to `step`, answering the interaction that asked for it.

            This is the only entry point a view uses. There is no loop: one
            click in, one screen out.
        """
        self.latest = interaction

        if source is not None:
            # Only the source view knows what its own widget meant.
            source.apply_choice(self)

        if step in _STATS_FIRST and self.user_stats is None:
            # Statistics are collected once per registration attempt, before the
            # screen that was asked for.
            await self._ack(interaction)
            await self.ready()
            (self.user_stats,
             self.recent_options,
             self.self_eval_options) = await UserStats.load_from_api(
                self.api, interaction.user.id, self.selected_event.scheduled_event_id)
            if not self._stats_complete():
                self.pending_step, step = step, NextStep.STATS

        elif step is NextStep.CONTINUE:
            # The stats screen is done; resume whatever asked for it.
            step, self.pending_step = self.pending_step or NextStep.MENU, None

        view = await self._enter(interaction, step)
        await self._show(interaction, view)


    async def _enter(self, interaction: discord.Interaction, step: NextStep) -> SessionView:
        """ Apply the side effects of arriving at `step` and return its screen.

            Exhaustive by construction: an unknown step raises, which surfaces
            through SessionView.on_error as a message to the user. A match with
            no default is how the old while loop managed to pin the event loop.
        """
        match step:
            case NextStep.MENU:
                if self._prefetch is not None and not self._prefetch.done():
                    # First trip through, and the database is still answering.
                    await self._ack(interaction)
                await self.ready()
                self.clear_selection()
                notice, self.notice = self.notice, None
                return RegisterMenuView(self.events, self.user, notice)

            case NextStep.STATS:
                return await self._stats_view(interaction)

            case NextStep.ADD:
                return DivTeamAddView(self.selected_event)

            case NextStep.EDIT:
                self.div_team_id = next(
                    reg["div_team_id"] for reg in self.user.registrations
                    if reg["scheduled_event_id"] == self.selected_event.scheduled_event_id)
                return DivTeamEditView(event=self.selected_event,
                                       existing_div_team_id=self.div_team_id)

            case NextStep.CONFIRM:
                if self.selected_event.has_solo_division and self.new_div_team_id is None:
                    # Nothing to choose: the event has exactly one division.
                    self.new_div_team_id = self.selected_event.divisions[0].id
                return ConfirmView(self.selected_event, self.new_div_team_id)

            case NextStep.WITHDRAW_CONF:
                if self.selected_event.has_solo_division and self.div_team_id is None:
                    self.div_team_id = self.selected_event.divisions[0].id
                return ConfirmWithdrawlView(self.selected_event, self.div_team_id)

            case NextStep.COMMIT_ADD:
                await self._commit_add(interaction)
                return await self._enter(interaction, NextStep.MENU)

            case NextStep.COMMIT_WITHDRAW:
                await self._commit_withdraw(interaction)
                return await self._enter(interaction, NextStep.MENU)

            case NextStep.LEAVE:
                if self._prefetch is not None and not self._prefetch.done():
                    # Left at the rules screen, before the database answered.
                    # Nobody will ever await this task, so stop it here rather
                    # than let it finish into a session that is over.
                    self._prefetch.cancel()
                    self._prefetch = None
                    return ExitView()
                try:
                    await self.ready()
                except Exception:
                    # The exit screen is the wrong place to surface a load
                    # failure: say goodbye plainly instead of claiming anything
                    # about what the user is registered for.
                    logger.exception("ggp_register: prefetch failed; showing the plain exit screen")
                    return ExitView()
                return ExitView(self.events, self.user)

            case _:
                raise RuntimeError(f"ggp_register: no screen for step {step!r}")


    async def expire(self) -> None:
        """ A view hit its timeout, so the user walked away.

            There is no interaction here, because nobody clicked. The newest
            token we were handed is how we reach the message: an ephemeral
            message cannot be fetched and edited as a plain Message.
        """
        if self._prefetch is not None and not self._prefetch.done():
            self._prefetch.cancel()
        try:
            await self.latest.edit_original_response(view=CancelView(
                "Session timed out. Run `/ggp_register` again to pick up where you left off."))
        except discord.HTTPException as error:
            # Token expired or message gone. Nothing left to clean up.
            logger.warning("ggp_register: could not post the timeout notice: %r", error)


    #############################################
    # Screen-building helpers
    #############################################

    def select_event(self, scheduled_event_id: int | None) -> None:
        """ Called by RegisterMenuView.apply_choice.
        """
        if scheduled_event_id is None:
            # The Leave button carries no event.
            return
        self.selected_event = next(
            event for event in self.events
            if event.scheduled_event_id == scheduled_event_id)
        self.div_team_str = self.selected_event.div_or_team()


    def clear_selection(self) -> None:
        """ Reset everything about the registration being worked on.

            One place, instead of the five copies the while loop needed.
        """
        self.selected_event = None
        self.div_team_str = None
        self.div_team_id = None
        self.new_div_team_id = None
        self.user_stats = None
        self.pending_step = None


    def _stats_view_type(self) -> str:
        """ Which statistics screen this event wants.

            Hard-coded, as before. Becomes self.selected_event.mode once the
            per-mode screens are finished.
        """
        return "basic"


    def _stats_complete(self) -> bool:
        """ Whether the statistics screen has anything left to ask.
        """
        if self._stats_view_type() != "basic":
            return False
        return bool(self.user_stats.self_eval_id and self.user_stats.most_recent_id)


    async def _stats_view(self, interaction: discord.Interaction) -> SessionView:
        """ The statistics screen, built from options already in hand.

            No "99" screen: it asks about `stats_ggp_99`, and the API serves no
            endpoint over that table, so there is nothing to build it from.
        """
        await self._ack(interaction)
        match self._stats_view_type():
            case "classic":
                return StatViewHistoryClassic(self.user_stats)
            case "basic":
                return BasicStatsView(self.recent_options, self.self_eval_options, self.user_stats)
            case other:
                raise RuntimeError(f"ggp_register: no stats screen for mode {other!r}")


    #############################################
    # Writes
    #############################################

    def _div_team_name(self, div_team_id: int) -> str:
        """ The name the user saw on the dropdown, for talking back to them.
        """
        pool = self.selected_event.divisions or self.selected_event.teams
        return next((dt.name for dt in pool if dt.id == div_team_id), str(div_team_id))


    async def _commit_add(self, interaction: discord.Interaction) -> None:
        """ Store the questionnaire, then register, then re-read.

            The questionnaire goes first because it stands on its own: it is an
            answer about the player, not about this registration, and a group
            that fills a moment later does not make it wrong.

            There is no capacity check here. The API counts inside the write and
            answers 409 when the group is full, which is the only answer that
            cannot already be stale by the time it is read.
        """
        await self._ack(interaction)

        if self.user_stats is not None:
            saved = await self.user_stats.save_to_api(
                self.api, interaction.user.id, interaction.user.name,
                default_display_name(interaction.user))
            if saved:
                logger.info("User stats of %s for %s have been saved.",
                            interaction.user.name, self.selected_event.event_name)

        try:
            await self.api.register(
                interaction.user.id,
                interaction.user.name,
                default_display_name(interaction.user),
                self.selected_event.scheduled_event_id,
                self.new_div_team_id,
                datetime.now(timezone.utc),
            )
        except FzdApiError as error:
            if error.status != 409:
                raise
            self.notice = (f"**{self._div_team_name(self.new_div_team_id)}** filled up while you "
                           f"were choosing, so nothing was registered. "
                           f"Pick another {self.div_team_str}.")
            logger.info("%s not added to %s: %s %s is full", interaction.user.name,
                        self.selected_event.event_name, self.div_team_str, self.new_div_team_id)
            await self._read_registrations()
            return

        logger.info("%s added to %s, %s %s", interaction.user.name,
                    self.selected_event.event_name, self.div_team_str, self.new_div_team_id)
        await self._read_registrations()


    async def _commit_withdraw(self, interaction: discord.Interaction) -> None:
        """ Leave the group. The API removes whichever group of this event the
            player holds, so there is nothing to name and nothing to look up.
        """
        await self._ack(interaction)
        await self.api.withdraw(
            interaction.user.id,
            self.selected_event.scheduled_event_id,
            datetime.now(timezone.utc),
        )
        logger.info("%s removed from %s, %s %s", interaction.user.name,
                    self.selected_event.event_name, self.div_team_str, self.div_team_id)
        await self._read_registrations()


    #############################################
    # Interaction plumbing
    #############################################

    @staticmethod
    async def _ack(interaction: discord.Interaction) -> None:
        """ Acknowledge now, edit later.

            Discord discards an interaction that goes 3 seconds without a
            response, so anything that touches the database calls this first.
            For a component interaction defer() is a silent acknowledgement:
            no "thinking" indicator appears.
        """
        if not interaction.response.is_done():
            await interaction.response.defer()


    async def _show(self, interaction: discord.Interaction, view: SessionView) -> None:
        """ Put `view` on screen and hand it the session so it can advance too.
        """
        # Set before the edit: a very fast click must never reach a view that
        # has no session behind it yet.
        view.session = self

        if interaction.response.is_done():
            # Already acknowledged, because something slow happened.
            await interaction.edit_original_response(view=view)
        else:
            # Nothing slow happened: swap the screen in a single round trip.
            await interaction.response.edit_message(view=view)

        self._retire(view)


    def _retire(self, view: SessionView) -> None:
        """ Make `view` the live screen and stop the one it replaced.

            This is what View.stop() is actually for: retiring a view, not
            signalling control flow. It unregisters the old components, so a
            click that was already in flight against the previous screen is
            ignored instead of acting on stale state, and it cancels the old
            view's timeout, which would otherwise fire five minutes later and
            declare a session dead while the user is still clicking.
        """
        previous, self.current_view = self.current_view, view
        if previous is not None and previous is not view:
            previous.stop()


#############################################
# Slash Commands
#############################################

class EventRegister(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot: commands.Bot = bot

    """ Commands """
    @app_commands.command(
        name="ggp_register", description="Register for an event"
    )
    async def event_register(self, interaction: discord.Interaction):
        """ Open the registration flow.

            The whole job of a command callback: acknowledge the interaction
            inside Discord's 3 second window, put the first screen up, and
            return. Every screen after this one is driven by button callbacks
            through RegSession, so this coroutine does not stay alive waiting
            on a user who may never click again.
        """
        session = RegSession(interaction)

        load_view = LoadView()
        # Attach before sending, so there is no window in which a click arrives
        # at a view with no session behind it.
        load_view.session = session
        await interaction.response.send_message(view=load_view, ephemeral=True)
        session.current_view = load_view

        # Acknowledged; now warm the cache while the user reads the rules.
        session.start_prefetch()


async def setup(bot: commands.Bot):
    server_id = get_settings().server_id
    GUILD_ID = discord.Object(id=server_id)
    await bot.add_cog(EventRegister(bot), guild=GUILD_ID)
