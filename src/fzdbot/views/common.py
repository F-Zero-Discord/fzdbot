import logging
from typing import Protocol

import discord
from discord import ui

from fzdbot.error_alerts import send_error_alert
from fzdbot.utils.view_utils import (
    NextStep
)

logger = logging.getLogger(__name__)


#################################
# Flow protocol
#################################
class FlowSession(Protocol):
    """ What a view is allowed to ask of the flow it belongs to.

        Views know nothing about the database or the cog. They only know that
        something owns the conversation and can move it on. Depending on this
        Protocol instead of the concrete session keeps fzdbot/views free of
        imports from fzdbot/cogs.
    """

    async def advance(self, interaction: discord.Interaction,
                      step: NextStep, source: "SessionView | None" = None) -> None: ...

    async def expire(self) -> None: ...


#################################
# View base classes
#################################
class SessionView(ui.LayoutView):
    """ A screen that belongs to a FlowSession.

        Every screen in a multi-step flow needs the same three things, so they
        live here instead of being copy-pasted into each view:

        session
            Who to tell when the user picks something.
        on_timeout
            What to do when the user walks away. discord.py calls this with no
            interaction, which is why the session keeps its own reference to the
            most recent one.
        on_error
            The hook discord.py actually calls when an item callback raises. Mind
            the signature: (interaction, error, item), positional-only, and it
            lives on the *view*. discord.py 2.7 has no Item.on_error, so an
            on_error defined on a Button is never called.

        The timeout defaults to 300s and is measured from the last interaction
        with this view, not from when it was sent.
    """

    def __init__(self, *, timeout: float | None = 300) -> None:
        super().__init__(timeout=timeout)
        self.session: FlowSession | None = None
        self.choice: int | None = None

    def apply_choice(self, session: FlowSession) -> None:
        """ Write this view's selection into the session.

            Buttons are generic, so only the view knows whether self.choice is
            an event id, a division id, or meaningless. Override where it
            matters; the default is deliberately a no-op.
        """
        return None

    async def on_timeout(self) -> None:
        if self.session is not None:
            await self.session.expire()

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception, item: ui.Item, /) -> None:
        # A view callback is not a command callback, so Bot.tree.on_error never
        # sees this. Log and alert here or it goes nowhere.
        logger.error(
            "Unhandled error in %s item %s for user %s",
            type(self).__name__,
            type(item).__name__,
            interaction.user,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_error_alert(
            interaction.client,
            where=f"view {type(self).__name__}",
            error=error,
            interaction=interaction,
        )

        # Inform the user safely via interaction response or followup
        message = "An unexpected error occurred while processing your click."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


#################################
# Button classes
#################################
class GenericButton(ui.Button):
    """ A button whose only job is to name the next step of the flow.

        The transition stays declarative data (next_step), but it is applied
        where the click actually arrives: in this callback, on the interaction
        Discord just handed us. Nothing waits, nothing is resumed elsewhere.
    """

    def __init__(self, parent_view: SessionView,
                 selection_id: int | None,
                 button_label: str,
                 button_color: discord.ButtonStyle,
                 button_disabled: bool,
                 next_step: NextStep):
        self.parent_view = parent_view
        self.selection_id = selection_id
        self.next_step = next_step
        super().__init__(label=button_label, style=button_color, disabled=button_disabled)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.choice = self.selection_id

        if self.parent_view.session is None:
            raise RuntimeError(
                f"{type(self.parent_view).__name__} was shown without a session attached; "
                "set view.session before sending it.")

        # No defer() here: the session decides whether this step needs a
        # deferral, because the session is what knows if the step hits the
        # database.
        await self.parent_view.session.advance(interaction, self.next_step, self.parent_view)
