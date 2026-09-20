import discord
from discord import ui

from fzdbot.api_types import Ggp8StatOption
from fzdbot.utils.event_class import UserStats
from fzdbot.utils.view_utils import NextStep
from fzdbot.views.common import GenericButton, SessionView


class BasicStatsView(SessionView):
    def __init__(
        self,
        recent_dict: list[Ggp8StatOption],
        self_eval_dict: list[Ggp8StatOption],
        user_stats: UserStats,
        timeout=180,
    ):
        super().__init__(timeout=timeout)
        self.user_stats: UserStats = user_stats

        intro_text = "The following questions will help us determine which Skill Class you should race in.\nThe FZD staff will use your records and results from previous events, including non-FZD events.\n_Note: if this is your first event, the FZD staff might contact you to get more information about your in-game records._"
        self_eval_text = "What is your F-Zero 99 experience?"
        most_recent_text = "What was your most recent major F-ZERO 99 event that you played in?"

        self.container = ui.Container()
        self.container.add_item(ui.TextDisplay(content="# Tell us about your F-Zero 99 career!"))
        self.container.add_item(ui.TextDisplay(content=intro_text))
        self.container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        self.container.add_item(ui.TextDisplay(content=self_eval_text))
        eval_selection = self.SelfEvalSelection(self, self_eval_dict)
        self.container.add_item(ui.ActionRow(eval_selection))

        self.container.add_item(ui.TextDisplay(content=most_recent_text))
        recent_selection = self.RecentSelection(self, recent_dict)
        self.container.add_item(ui.ActionRow(recent_selection))

        self.container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self.continue_button = GenericButton(
            parent_view=self,
            selection_id=1,
            button_label="Continue",
            button_color=discord.ButtonStyle.green,
            button_disabled=True,
            next_step=NextStep.CONTINUE,
        )
        self.back_button = GenericButton(
            parent_view=self,
            selection_id=None,
            button_label="Back",
            button_color=discord.ButtonStyle.blurple,
            button_disabled=False,
            next_step=NextStep.MENU,
        )
        self.container.add_item(ui.ActionRow(self.back_button, self.continue_button))

        self.component_status_manager()
        self.add_item(self.container)

    #################################
    # Drowdown subclasses
    #################################

    class RecentSelection(ui.Select):
        def __init__(self, parent_view: "BasicStatsView", recent_dict_list: list[Ggp8StatOption]):
            self.parent_view = parent_view

            options = []
            for recent_dict in recent_dict_list:
                options.append(
                    discord.SelectOption(
                        label=recent_dict["text"],
                        description=None,
                        default=(self.parent_view.user_stats.most_recent_id == recent_dict["id"]),
                        value=str(recent_dict["id"]),
                    )
                )
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.user_stats.most_recent_id = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = int(option.value) == int(self.values[0])

            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)

    class SelfEvalSelection(ui.Select):
        def __init__(self, parent_view: "BasicStatsView", self_eval_dict_list: list[Ggp8StatOption]):
            self.parent_view = parent_view

            options = []
            for self_eval_dict in self_eval_dict_list:
                options.append(
                    discord.SelectOption(
                        label=self_eval_dict["text"],
                        description=None,
                        default=(self.parent_view.user_stats.self_eval_id == self_eval_dict["id"]),
                        value=str(self_eval_dict["id"]),
                    )
                )
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.user_stats.self_eval_id = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = int(option.value) == int(self.values[0])

            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)

    #################################
    # Class utility methods
    #################################

    def component_status_manager(self):
        if not self.user_stats.self_eval_id or not self.user_stats.most_recent_id:
            self.continue_button.disabled = True
        else:
            self.continue_button.disabled = False
