"""The GGP8 event roles, kept level with who is registered.

A tick reads the registrations and the guild, and changes the difference: the
role is added to everyone registered and holding a place who does not have it,
and taken from everyone who has it and is not. Nothing is held between ticks, so
a restart, a missed tick, a withdrawal or a role handed out by hand are all
corrected by the next one, and the first tick after a fresh start is the one-shot
that grants the lot.

Which event carries which role is configuration: `GGP8_EVENT_ROLES` maps a
`scheduled_event_id` to a role id, an event in neither the map nor the API's
GGP8 list is never touched, and an empty map leaves the loop stopped.

Reading the guild's members needs the members intent, which `main.py` asks for
where the map is not empty and Discord's developer portal has to grant. Without
it a role holds nobody as far as this cog can see, so it adds what it can and
removes nothing.
"""

import logging

import discord
from discord.ext import commands, tasks

from fzdbot.api_types import Ggp8RegistrationResponse
from fzdbot.error_alerts import send_error_alert
from fzdbot.event_roles import holders, unnamed
from fzdbot.fzd_api import FzdApiError
from fzdbot.main import FZDBot
from fzdbot.settings import get_settings

logger = logging.getLogger(__name__)

REASON = "FZD event registration"
"""What the guild's audit log says beside every one of these changes."""


class EventRoles(commands.Cog):
    def __init__(self, bot: FZDBot) -> None:
        self.bot = bot
        self.sync: tasks.Loop | None = None
        self._failing: set[int] = set()

    async def reconcile(self) -> None:
        """One tick. An event's failure is logged, alerted once, and left for the
        next tick; it stops neither the other events nor the loop.
        """
        settings = get_settings()
        guild = self.bot.get_guild(settings.server_id)
        if guild is None:
            logger.warning("[event roles] guild %s is not in the cache", settings.server_id)
            return

        try:
            registrations = await self.bot.api.ggp8_registrations()
        except FzdApiError as error:
            logger.warning("[event roles] could not read the registrations: %s", error)
            return

        for scheduled_event_id, role_id in settings.ggp8_event_roles.items():
            role = guild.get_role(role_id)
            if role is None:
                logger.warning(
                    "[event roles] event=%s names role=%s, which this guild does not have",
                    scheduled_event_id,
                    role_id,
                )
                continue
            try:
                await self._reconcile(guild, role, scheduled_event_id, registrations)
                self._failing.discard(scheduled_event_id)
            except Exception as error:
                logger.exception("[event roles] event=%s role=%s", scheduled_event_id, role_id)
                if scheduled_event_id not in self._failing:
                    self._failing.add(scheduled_event_id)
                    await send_error_alert(
                        self.bot,
                        where="event roles",
                        error=error,
                        details={"scheduled_event_id": scheduled_event_id, "role_id": role_id},
                    )

    async def _reconcile(
        self,
        guild: discord.Guild,
        role: discord.Role,
        scheduled_event_id: int,
        registrations: list[Ggp8RegistrationResponse],
    ) -> None:
        desired = holders(registrations, scheduled_event_id)
        holding = list(role.members)

        if not desired:
            # The one guard here, and it buys the difference between a stale role
            # and every registrant losing theirs at once: an event that answers no
            # registrations is as easily an id configured wrongly as an event
            # nobody has entered. Removals resume on the tick after one
            # registration exists.
            logger.warning(
                "[event roles] event=%s has no registrations; %s keeps its %s members",
                scheduled_event_id,
                role.name,
                len(holding),
            )
            return

        for member in [member for member in holding if member.id not in desired]:
            await self._edit(member, role, scheduled_event_id, add=False)

        absent = []
        for snowflake in desired - {member.id for member in holding}:
            member = guild.get_member(snowflake)
            if member is None:
                absent.append(snowflake)
                continue
            await self._edit(member, role, scheduled_event_id, add=True)

        # Counted rather than listed: a registrant who never joined the guild is
        # absent on every tick, and a line each would be the whole log. The ids
        # are one DEBUG away when somebody wants to know which.
        if absent:
            logger.info(
                "[event roles] event=%s: %s of %s registered are not in the guild",
                scheduled_event_id,
                len(absent),
                len(desired),
            )
            logger.debug("[event roles] event=%s not in the guild: %s", scheduled_event_id, absent)

        nameless = unnamed(registrations, scheduled_event_id)
        if nameless:
            logger.info(
                "[event roles] event=%s: %s registered under no Discord id: %s",
                scheduled_event_id,
                len(nameless),
                ", ".join(nameless),
            )

    async def _edit(self, member: discord.Member, role: discord.Role, scheduled_event_id: int, *, add: bool) -> None:
        """One member's role changed, or the refusal logged and the pass carried on.

        A member holding a role above the bot's own cannot be edited at all, and
        the staff who register for an event are exactly those members, so their
        refusal must not take everybody after them in the pass with it.

        Discord's own text is logged because the two refusals that look alike
        here read differently there: missing Manage Roles is refused for every
        member of every event, a role above the bot's for one member of one.
        """
        direction = "+" if add else "-"
        try:
            if add:
                await member.add_roles(role, reason=REASON)
            else:
                await member.remove_roles(role, reason=REASON)
        except discord.Forbidden as refusal:
            logger.warning(
                "[event roles] %s%s refused for %s on event=%s: %s (code %s)",
                direction,
                role.name,
                member.id,
                scheduled_event_id,
                refusal.text,
                refusal.code,
            )
            return
        logger.info("[event roles] %s%s for %s on event=%s", direction, role.name, member.id, scheduled_event_id)

    async def cog_load(self) -> None:
        settings = get_settings()
        if not settings.ggp8_event_roles:
            logger.info("[event roles] GGP8_EVENT_ROLES is empty; no role is synced")
            return
        self.sync = tasks.loop(seconds=settings.event_role_sync_seconds)(self.reconcile)
        self.sync.before_loop(self.bot.wait_until_ready)
        self.sync.start()

    async def cog_unload(self) -> None:
        if self.sync is not None:
            self.sync.cancel()


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(EventRoles(bot), guild=discord.Object(id=get_settings().server_id))
