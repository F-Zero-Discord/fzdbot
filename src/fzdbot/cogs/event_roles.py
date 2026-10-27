"""The tournaments' group roles, kept level with who is registered.

A tick reads every published tournament, its groups and its registrants, and
the guild, and changes the difference: a group's role is added to everyone
registered in it and holding a place who does not have it, and taken from
everyone who has it and is not. Which role a group grants is the group's own
`role_id`, so a new tournament's roles are set on its rows and nothing here is
configured. `event_roles.role_holders` is the rule; this module is the diff and
the Discord calls.

Nothing is held between ticks, so a restart, a missed tick, a withdrawal or a
role handed out by hand are all corrected by the next one, and the first tick
after a fresh start is the one-shot that grants the lot. A tournament staff
have closed has its roles withdrawn the same way: its registrants are read and
its roles go to nobody.

Every published tournament is read on every tick, the closed ones included:
two reads each, so the rule of who holds what stays in one function rather
than being split between here and there.

Reading the guild's members needs the members intent, which `main.py` asks for
and Discord's developer portal has to grant. Without it a role holds nobody as
far as this cog can see, so it adds what it can and removes nothing.
"""

import logging
from datetime import UTC, datetime

import discord
from discord.ext import commands, tasks

from fzdbot.error_alerts import send_error_alert
from fzdbot.event_roles import role_holders
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
        """One tick. A role's failure is logged, alerted once, and left for the
        next tick; it stops neither the other roles nor the loop.
        """
        settings = get_settings()
        guild = self.bot.get_guild(settings.server_id)
        if guild is None:
            logger.warning("[event roles] guild %s is not in the cache", settings.server_id)
            return

        try:
            desired = await self._desired()
        except FzdApiError as error:
            logger.warning("[event roles] could not read the tournaments: %s", error)
            return

        for role_id, snowflakes in desired.items():
            role = guild.get_role(role_id)
            if role is None:
                logger.warning("[event roles] a group grants role=%s, which this guild does not have", role_id)
                continue
            try:
                await self._reconcile(guild, role, snowflakes)
                self._failing.discard(role_id)
            except Exception as error:
                logger.exception("[event roles] %s role=%s", role.name, role_id)
                if role_id not in self._failing:
                    self._failing.add(role_id)
                    await send_error_alert(
                        self.bot,
                        where="event roles",
                        error=error,
                        details={"role": role.name, "role_id": role_id},
                    )

    async def _desired(self) -> dict[int, set[int]]:
        """Every role a published tournament's groups grant, to who should hold
        it, across all of them in one pass: a role two tournaments share goes
        to the union of their holders.
        """
        now = datetime.now(UTC)
        desired: dict[int, set[int]] = {}
        for summary in await self.bot.api.tournaments(now):
            tournament = await self.bot.api.tournament(summary["slug"], now)
            registrants = await self.bot.api.tournament_registrants(summary["slug"])
            for role_id, snowflakes in role_holders(tournament, registrants).items():
                desired.setdefault(role_id, set()).update(snowflakes)
        return desired

    async def _reconcile(self, guild: discord.Guild, role: discord.Role, desired: set[int]) -> None:
        holding = list(role.members)

        for member in [member for member in holding if member.id not in desired]:
            await self._edit(member, role, add=False)

        absent = []
        for snowflake in desired - {member.id for member in holding}:
            member = guild.get_member(snowflake)
            if member is None:
                absent.append(snowflake)
                continue
            await self._edit(member, role, add=True)

        # Counted rather than listed: a registrant who never joined the guild is
        # absent on every tick, and a line each would be the whole log. The ids
        # are one DEBUG away when somebody wants to know which.
        if absent:
            logger.info(
                "[event roles] %s: %s of %s registered are not in the guild", role.name, len(absent), len(desired)
            )
            logger.debug("[event roles] %s not in the guild: %s", role.name, absent)

    async def _edit(self, member: discord.Member, role: discord.Role, *, add: bool) -> None:
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
                "[event roles] %s%s refused for %s: %s (code %s)",
                direction,
                role.name,
                member.id,
                refusal.text,
                refusal.code,
            )
            return
        logger.info("[event roles] %s%s for %s", direction, role.name, member.id)

    async def cog_load(self) -> None:
        settings = get_settings()
        logger.info("[event roles] syncing the tournaments' group roles every %s s", settings.event_role_sync_seconds)
        self.sync = tasks.loop(seconds=settings.event_role_sync_seconds)(self.reconcile)
        self.sync.before_loop(self.bot.wait_until_ready)
        self.sync.start()

    async def cog_unload(self) -> None:
        if self.sync is not None:
            self.sync.cancel()


async def setup(bot: FZDBot) -> None:
    await bot.add_cog(EventRoles(bot), guild=discord.Object(id=get_settings().server_id))
