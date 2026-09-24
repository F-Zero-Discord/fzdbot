"""The GGP8 event and division roles, kept level with who is registered.

A tick reads the registrations and the guild, and changes the difference: the
role is added to everyone registered and holding a place who does not have it,
and taken from everyone who has it and is not. Nothing is held between ticks, so
a restart, a missed tick, a withdrawal or a role handed out by hand are all
corrected by the next one, and the first tick after a fresh start is the one-shot
that grants the lot.

Which event carries which role is configuration: `GGP8_EVENT_ROLES` maps a
`scheduled_event_id` to a role id, and `GGP8_DIVISION_ROLES` a `divisions` id,
for a role per division on top of the event's. Anything in neither map, or
outside the API's GGP8 list, is never touched, and two empty maps leave the loop
stopped.

Reading the guild's members needs the members intent, which `main.py` asks for
where the map is not empty and Discord's developer portal has to grant. Without
it a role holds nobody as far as this cog can see, so it adds what it can and
removes nothing.
"""

import logging

import discord
from discord.ext import commands, tasks

from fzdbot.error_alerts import send_error_alert
from fzdbot.event_roles import division_holders, holders
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
        self._failing: set[str] = set()

    async def reconcile(self) -> None:
        """One tick. A role's failure is logged, alerted once, and left for the
        next tick; it stops neither the other roles nor the loop.
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

        targets = [
            (f"event={event_id}", role_id, holders(registrations, event_id))
            for event_id, role_id in settings.ggp8_event_roles.items()
        ] + [
            (f"division={division_id}", role_id, division_holders(registrations, division_id))
            for division_id, role_id in settings.ggp8_division_roles.items()
        ]
        for target, role_id, desired in targets:
            role = guild.get_role(role_id)
            if role is None:
                logger.warning("[event roles] %s names role=%s, which this guild does not have", target, role_id)
                continue
            try:
                await self._reconcile(guild, role, target, desired)
                self._failing.discard(target)
            except Exception as error:
                logger.exception("[event roles] %s role=%s", target, role_id)
                if target not in self._failing:
                    self._failing.add(target)
                    await send_error_alert(
                        self.bot,
                        where="event roles",
                        error=error,
                        details={"target": target, "role_id": role_id},
                    )

    async def _reconcile(self, guild: discord.Guild, role: discord.Role, target: str, desired: set[int]) -> None:
        holding = list(role.members)

        if not desired:
            # The one guard here, and it buys the difference between a stale role
            # and every registrant losing theirs at once: an event or a division
            # that answers no registrations is as easily an id configured wrongly
            # as one nobody has entered. Removals resume on the tick after one
            # registration exists.
            logger.warning(
                "[event roles] %s has no registrations; %s keeps its %s members", target, role.name, len(holding)
            )
            return

        for member in [member for member in holding if member.id not in desired]:
            await self._edit(member, role, target, add=False)

        absent = []
        for snowflake in desired - {member.id for member in holding}:
            member = guild.get_member(snowflake)
            if member is None:
                absent.append(snowflake)
                continue
            await self._edit(member, role, target, add=True)

        # Counted rather than listed: a registrant who never joined the guild is
        # absent on every tick, and a line each would be the whole log. The ids
        # are one DEBUG away when somebody wants to know which.
        if absent:
            logger.info("[event roles] %s: %s of %s registered are not in the guild", target, len(absent), len(desired))
            logger.debug("[event roles] %s not in the guild: %s", target, absent)

    async def _edit(self, member: discord.Member, role: discord.Role, target: str, *, add: bool) -> None:
        """One member's role changed, or the refusal logged and the pass carried on.

        A member holding a role above the bot's own cannot be edited at all, and
        the staff who register for an event are exactly those members, so their
        refusal must not take everybody after them in the pass with it.

        Discord's own text is logged because the two refusals that look alike
        here read differently there: missing Manage Roles is refused for every
        member of every role, a role above the bot's for one member of one.
        """
        direction = "+" if add else "-"
        try:
            if add:
                await member.add_roles(role, reason=REASON)
            else:
                await member.remove_roles(role, reason=REASON)
        except discord.Forbidden as refusal:
            logger.warning(
                "[event roles] %s%s refused for %s on %s: %s (code %s)",
                direction,
                role.name,
                member.id,
                target,
                refusal.text,
                refusal.code,
            )
            return
        logger.info("[event roles] %s%s for %s on %s", direction, role.name, member.id, target)

    async def cog_load(self) -> None:
        settings = get_settings()
        if not settings.ggp8_event_roles and not settings.ggp8_division_roles:
            logger.info("[event roles] GGP8_EVENT_ROLES and GGP8_DIVISION_ROLES are empty; no role is synced")
            return
        self.sync = tasks.loop(seconds=settings.event_role_sync_seconds)(self.reconcile)
        self.sync.before_loop(self.bot.wait_until_ready)
        self.sync.start()

    async def cog_unload(self) -> None:
        if self.sync is not None:
            self.sync.cancel()


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(EventRoles(bot), guild=discord.Object(id=get_settings().server_id))
