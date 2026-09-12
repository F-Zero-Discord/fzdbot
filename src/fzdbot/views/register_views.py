import discord
from discord import ui

from fzdbot.settings import get_settings
from fzdbot.utils.event_class import Division, Event, Team, UserRegistrations
from fzdbot.utils.status_policies import registered_summary, user_event_status
from fzdbot.utils.view_utils import DivTeam, NextStep, discord_timestamp
from fzdbot.views.common import GenericButton, SessionView


def channel_mention(channel_id: int | None, fallback: str) -> str:
    """A clickable channel, or a readable name when the id is not configured.

    `<#id>` is preferred over a link button or a hard-coded #name: Discord
    renders it with the channel's current name, so a rename does not leave
    stale copy behind.
    """
    return f"<#{channel_id}>" if channel_id else fallback


def room_left(div_team: Division | Team) -> str:
    """What the status line says about a division or team that has room in it.

    A division whose capacity is NULL is not capped, and there is no number of
    spots to count down for one.
    """
    if div_team.capacity is None:
        return f"Open - {div_team.num_registered} registered"
    return f"{div_team.capacity - div_team.num_registered} spots available!"


#################################
# LayoutView classes
#################################


class CancelView(SessionView):
    def __init__(self, message: str):
        super().__init__(timeout=None)  # terminal screen: nothing to time out
        container = ui.Container()

        container.add_item(ui.TextDisplay(content=message))
        self.add_item(container)


class LoadView(SessionView):
    def __init__(self):
        super().__init__()

        loading_screen_text = """Welcome to your portal to sign up for FZD events! If we have a pending event ready for your signup, you'll find it here.

For events with divisions or teams, we will endeavor to place you in your requested division or team, but we may need to make changes to account for capacity and balance.

In addition to server RULES, Event RULES are designed to foster a fun and competitive environment for all participants. Each event has its own set of rules and instructions. If you have a question, please ask. We only ask that you agree to read and follow the rules for the events you register for. 

Please confirm that you will read and follow the rules for each event you register for."""

        container = ui.Container()
        container.add_item(ui.TextDisplay(content="# Register for an Event"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(content=loading_screen_text))

        self.affirm_button = GenericButton(
            parent_view=self,
            selection_id=None,
            button_label="I Agree",
            button_color=discord.ButtonStyle.success,
            button_disabled=False,
            next_step=NextStep.MENU,
        )
        self.cancel_button = GenericButton(
            parent_view=self,
            selection_id=None,
            button_label="Not Right Now",
            button_color=discord.ButtonStyle.secondary,
            button_disabled=False,
            next_step=NextStep.LEAVE,
        )

        container.add_item(ui.ActionRow(self.affirm_button, self.cancel_button))
        self.add_item(container)


class RegisterMenuView(SessionView):
    def __init__(self, events: list[Event], user: UserRegistrations, notice: str | None = None):
        super().__init__()

        container = ui.Container()
        container.add_item(ui.TextDisplay(content="# Register for an Event"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        if notice:
            container.add_item(ui.TextDisplay(content=notice))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Build sections for each event in events
        section_text: list[ui.TextDisplay] = []
        section_button: list[GenericButton] = []
        section: list[ui.Section] = []

        # Select button for each status.next_step
        for i, event in enumerate(events):
            status = user_event_status(event, user)
            section_text.append(
                ui.TextDisplay(
                    content=f"### {event.event_name}\n\t{discord_timestamp(event.start_time, 'long')}\n\t{status['label']}"
                )
            )
            section_button.append(
                GenericButton(
                    parent_view=self,
                    selection_id=event.scheduled_event_id,
                    button_label=status["button_label"],
                    button_color=status["button_color"],
                    button_disabled=status["button_disabled"],
                    next_step=status["next_step"],
                )
            )
            section.append(ui.Section(section_text[i], accessory=section_button[i]))
            container.add_item(section[i])

        # Build the Cancel button section
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(
            ui.ActionRow(
                GenericButton(
                    parent_view=self,
                    selection_id=None,
                    button_label="Done",
                    button_color=discord.ButtonStyle.secondary,
                    button_disabled=False,
                    next_step=NextStep.LEAVE,
                )
            )
        )

        self.add_item(container)

    ###########################
    # Class methods
    ###########################

    def apply_choice(self, session) -> None:
        """self.choice is a scheduled_event_id, or None for the Leave button."""
        session.select_event(self.choice)


class DivTeamAddView(SessionView):
    def __init__(self, event: Event):
        super().__init__()
        self.event: Event = event
        self.div_team_string: DivTeam | None = None

        if self.event.divisions:
            self.div_team_string = DivTeam.DIVISION
            div_team_list = self.event.divisions
        elif self.event.teams:
            self.div_team_string = DivTeam.TEAM
            div_team_list = self.event.teams
        else:
            raise ValueError("To create a DivTeamView there need to be divisions (plural) or teams.")

        container = ui.Container()
        container.add_item(ui.TextDisplay(f"# Chooose a {self.div_team_string.capitalize()}"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Build dropdown
        container.add_item(ui.ActionRow(self.div_team_selection(self, div_team_list)))
        self.status_text = ui.TextDisplay(content="-")
        container.add_item(self.status_text)

        # Build the Continue button section
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self.continue_button = GenericButton(
            parent_view=self,
            selection_id=self.choice,
            button_label="Continue",
            button_color=discord.ButtonStyle.primary,
            button_disabled=True,
            next_step=NextStep.CONFIRM,
        )
        self.back_button = GenericButton(
            parent_view=self,
            selection_id=0,
            button_label="Back",
            button_color=discord.ButtonStyle.secondary,
            button_disabled=False,
            next_step=NextStep.MENU,
        )
        container.add_item(ui.ActionRow(self.back_button, self.continue_button))

        self.add_item(container)

        self.set_status()

    #################################
    # Drowdown subclass
    #################################
    class div_team_selection(ui.Select):
        def __init__(self, parent_view: SessionView, div_team_list: list[Division] | list[Team]):
            self.parent_view = parent_view

            options = []
            for div_team in div_team_list:
                options.append(discord.SelectOption(label=div_team.name, description=None, value=div_team.id))
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.choice = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = int(option.value) == int(self.values[0])

            self.parent_view.set_status()
            await interaction.response.edit_message(view=self.parent_view)

    ###########################
    # Class methods
    ###########################

    def set_status(self) -> None:
        """Enable/disable Continue button and set status textbox."""
        # Find div_team with dropdown choice id
        if not self.choice:
            self.continue_button.disabled = True
            self.status_text.content = "-"
            return

        # Get div_team object selected
        match self.div_team_string:
            case DivTeam.DIVISION:
                div_team = [dt for dt in self.event.divisions if dt.id == self.choice][0]
            case DivTeam.TEAM:
                div_team = [dt for dt in self.event.teams if dt.id == self.choice][0]
            case _:
                raise ValueError(f"Self.div_team must be 'division' or 'team', not {div_team}")

        # Set statuses
        if div_team.at_capacity:
            self.continue_button.disabled = True
            print(f"{self.div_team_string.capitalize()} {div_team.name} full!")
            self.status_text.content = f"{self.div_team_string.capitalize()} {div_team.name} full!"
        else:
            self.continue_button.disabled = False
            self.status_text.content = room_left(div_team)
            self.continue_button.selection_id = self.choice

    def apply_choice(self, session) -> None:
        """self.choice is the division/team the dropdown is sitting on."""
        session.new_div_team_id = self.choice


class DivTeamEditView(SessionView):
    def __init__(self, event: Event, existing_div_team_id: int):
        super().__init__()
        self.choice: int | None = existing_div_team_id
        self.event: Event = event
        self.existing_div_team_id: int = existing_div_team_id
        self.div_team_string: DivTeam | None = None

        if self.event.divisions:
            self.div_team_string = DivTeam.DIVISION
            div_team_list = self.event.divisions
        elif self.event.teams:
            self.div_team_string = DivTeam.TEAM
            div_team_list = self.event.teams
        else:
            raise ValueError("To create a DivTeamView there need to be divisions (plural) or teams.")

        container = ui.Container()
        container.add_item(ui.TextDisplay("# Edit Your Registration"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Build dropdown part of container
        container.add_item(ui.ActionRow(self.div_team_selection(self, div_team_list)))
        self.status_text = ui.TextDisplay(content="-")
        container.add_item(self.status_text)

        # Build the button ActionRow
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self.edit_button = GenericButton(
            parent_view=self,
            selection_id=self.choice,
            button_label="Change",
            button_color=discord.ButtonStyle.primary,
            button_disabled=True,
            next_step=NextStep.CONFIRM,
        )
        self.withdraw_button = GenericButton(
            parent_view=self,
            selection_id=self.choice,
            button_label="Withdraw from Event",
            button_color=discord.ButtonStyle.danger,
            button_disabled=False,
            next_step=NextStep.WITHDRAW_CONF,
        )
        self.back_button = GenericButton(
            parent_view=self,
            selection_id=0,
            button_label="Back",
            button_color=discord.ButtonStyle.secondary,
            button_disabled=False,
            next_step=NextStep.MENU,
        )
        container.add_item(ui.ActionRow(self.back_button, self.edit_button, self.withdraw_button))

        self.add_item(container)

        self.set_status()

    #################################
    # Drowdown subclass
    #################################
    class div_team_selection(ui.Select):
        def __init__(self, parent_view: SessionView, div_team_list: list[Division] | list[Team]):
            self.parent_view = parent_view

            options = []
            for div_team in div_team_list:
                if div_team.id == self.parent_view.existing_div_team_id:
                    label = f"{div_team.name} (current)"
                else:
                    label = f"{div_team.name}"
                options.append(discord.SelectOption(label=label, description=None, value=div_team.id))
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.choice = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = int(option.value) == int(self.values[0])

            self.parent_view.set_status()
            await interaction.response.edit_message(view=self.parent_view)

    ###########################
    # Class methods
    ###########################

    def set_status(self) -> None:
        """Enable/disable Continue button and set status textbox."""
        # Find div_team with dropdown choice id
        if not self.choice:
            self.edit_button.disabled = True
            self.status_text.content = "-"
            return

        # Get div_team object selected
        match self.div_team_string:
            case DivTeam.DIVISION:
                div_team = [d for d in self.event.divisions if d.id == self.choice][0]
            case DivTeam.TEAM:
                div_team = [t for t in self.event.teams if t.id == self.choice][0]
            case _:
                raise ValueError(f"Self.div_team must be 'division' or 'team', not {div_team}")

        # Set statuses
        if div_team.at_capacity:
            self.edit_button.disabled = True
            print(f"{self.div_team_string.capitalize()} {div_team.name} full!")
            self.status_text.content = f"{self.div_team_string.capitalize()} {div_team.name} full!"
        else:
            self.edit_button.disabled = False
            self.status_text.content = room_left(div_team)
            self.edit_button.selection_id = self.choice

        if self.choice == self.existing_div_team_id:
            # Nothing to change: this is what the user is already registered for.
            # The old flow forced this button back on, because the statistics
            # screen was shown after this one and had no way to tell it that the
            # selection still stood. The session now collects statistics before
            # this screen is built, so a fresh view starts from the real state.
            self.edit_button.disabled = True

    def apply_choice(self, session) -> None:
        """self.choice is the division/team the dropdown is sitting on."""
        session.new_div_team_id = self.choice


class ConfirmView(SessionView):
    def __init__(self, event: Event, div_team_id: int):
        super().__init__()

        div_team_str = event.div_or_team()

        # get division/team name
        match div_team_str:
            case DivTeam.DIVISION:
                if len(event.divisions) == 1:
                    div_team_str = DivTeam.NEITHER
                    div_team_name = ""
                else:
                    div_team_name = [d.name for d in event.divisions if d.id == div_team_id][0]
            case DivTeam.TEAM:
                div_team_name = [t.name for t in event.teams if t.id == div_team_id][0]
            case _:
                raise ValueError(f"Self.div_team must be 'division' or 'team', not {div_team_str}")

        choice_text = f"### {event.event_name}\n\t{discord_timestamp(event.start_time, 'long')}\n"
        if div_team_str != DivTeam.NEITHER:
            choice_text += f"**{div_team_str.capitalize()}**\n\t{div_team_name}"
        confirm_text = "### Are you ready! Confirm below."

        container = ui.Container()
        container.add_item(ui.TextDisplay(content="# Confirm Your Choice"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(content=f"{choice_text}\n\n\n\n{confirm_text}"))

        self.cancel_button = GenericButton(
            parent_view=self,
            selection_id=None,
            button_label="Never Mind",
            button_color=discord.ButtonStyle.secondary,
            button_disabled=False,
            next_step=NextStep.MENU,
        )
        self.affirm_button = GenericButton(
            parent_view=self,
            selection_id=None,
            button_label="Let's Go!",
            button_color=discord.ButtonStyle.success,
            button_disabled=False,
            next_step=NextStep.COMMIT_ADD,
        )

        container.add_item(ui.ActionRow(self.cancel_button, self.affirm_button))
        self.add_item(container)


class ConfirmWithdrawlView(SessionView):
    def __init__(self, event: Event, div_team_id: int):
        super().__init__()

        div_team_str = event.div_or_team()

        # get division/team name
        match div_team_str:
            case DivTeam.DIVISION:
                if len(event.divisions) == 1:
                    div_team_str = DivTeam.NEITHER
                    div_team_name = ""
                else:
                    div_team_name = [d.name for d in event.divisions if d.id == div_team_id][0]
            case DivTeam.TEAM:
                div_team_name = [t.name for t in event.teams if t.id == div_team_id][0]
            case _:
                raise ValueError(f"Self.div_team must be 'division' or 'team', not {div_team_str}")

        choice_text = f"### {event.event_name}\n\t{discord_timestamp(event.start_time, 'long')}\n"
        if div_team_str != DivTeam.NEITHER:
            choice_text += f"**{div_team_str.capitalize()}**\n\t{div_team_name}"
        confirm_text = "### Are you sure you want to withdraw your registration?"

        container = ui.Container()
        container.add_item(ui.TextDisplay(content="# Withdraw Your Registration?"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(content=f"{choice_text}\n\n\n\n{confirm_text}"))

        self.affirm_button = GenericButton(
            parent_view=self,
            selection_id=None,
            button_label="Withdraw",
            button_color=discord.ButtonStyle.danger,
            button_disabled=False,
            next_step=NextStep.COMMIT_WITHDRAW,
        )
        self.cancel_button = GenericButton(
            parent_view=self,
            selection_id=None,
            button_label="Nevermind",
            button_color=discord.ButtonStyle.secondary,
            button_disabled=False,
            next_step=NextStep.MENU,
        )
        container.add_item(ui.ActionRow(self.affirm_button, self.cancel_button))
        self.add_item(container)


class ExitView(SessionView):
    """The last screen of the flow.

    Terminal in the strict sense: no buttons, no timeout, and the message is
    ephemeral, so there is nothing left for the user to click. Anything the
    copy tells them to do next has to be `/ggp_register` again.

    Three shapes, depending on how much the session got to know:
      - nothing loaded, because the user left at the rules screen
      - loaded, but the user is signed up for nothing
      - loaded, with registrations to confirm back
    """

    def __init__(self, events: list[Event] | None = None, user: UserRegistrations | None = None):
        super().__init__(timeout=None)  # terminal screen: nothing to time out

        settings = get_settings()
        self.rules = channel_mention(settings.rules_channel_id, "the rules channel")
        self.help = channel_mention(settings.help_channel_id, "the help channel")
        self.faq = channel_mention(settings.faq_channel_id, "the FAQ channel")

        if user is None:
            self.add_item(self.farewell_container())
            return

        summary = registered_summary(events or [], user)
        if not summary:
            self.add_item(self.nothing_registered_container())
            return

        self.add_item(self.registered_container(summary))

    ###########################
    # Containers
    ###########################

    def farewell_container(self) -> ui.Container:
        """The user left before anything was loaded, so claim nothing about
        what they are or are not signed up for.
        """
        container = ui.Container(accent_colour=discord.Colour.light_grey())
        container.add_item(
            ui.TextDisplay(
                "### Thanks for stopping by\n"
                "Run `/ggp_register` whenever you're ready. The door is always open.\n"
                f"Questions? Ask in {self.help} - there is no such thing as a dumb one."
            )
        )
        return container

    def nothing_registered_container(self) -> ui.Container:
        container = ui.Container(accent_colour=discord.Colour.light_grey())
        container.add_item(
            ui.TextDisplay(
                "### Nothing registered. No problem\n"
                f"You are not signed up for any upcoming events. {self.rules} and {self.faq} are "
                "a good look at what an event involves, and `/ggp_register` is there whenever "
                "you change your mind.\n"
                f"Questions? Ask in {self.help} - there is no such thing as a dumb one."
            )
        )
        return container

    def registered_container(self, summary: list[tuple[Event, DivTeam | None, str | None]]) -> ui.Container:
        container = ui.Container(accent_colour=discord.Colour.green())
        container.add_item(ui.TextDisplay(content="# You're all set - see you on track!"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(content=self.registrations_text(summary)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(content=self.next_steps_text(summary)))
        return container

    ###########################
    # Copy
    ###########################

    @staticmethod
    def registrations_text(summary: list[tuple[Event, DivTeam | None, str | None]]) -> str:
        plural = "" if len(summary) == 1 else "s"
        text = f"**Registered for {len(summary)} event{plural}**"

        for event, div_team_str, div_team_name in summary:
            text += (
                f"\n### {event.event_name}\n"
                f"{discord_timestamp(event.start_time, 'full')} "
                f"({discord_timestamp(event.start_time, 'relative')})"
            )
            if div_team_name:
                text += f"\n{div_team_str.capitalize()}: **{div_team_name}**"

        return text

    def next_steps_text(self, summary: list[tuple[Event, DivTeam | None, str | None]]) -> str:
        text = (
            "**Before race day**\n"
            "Read the rules for each event you signed up for. They are in "
            f"{self.rules}, and {self.faq} covers the questions that come up most."
        )

        if any(div_team_name for _, _, div_team_name in summary):
            # Only worth saying when the user actually picked something that can move.
            text += "\nDivision and team placements are confirmed closer to the event; watch for a ping."

        text += (
            "\n\n**Changed your mind?** Run `/ggp_register` again any time to edit or "
            "withdraw.\n"
            f"Questions? Ask in {self.help} - there is no such thing as a dumb one."
        )

        return text
