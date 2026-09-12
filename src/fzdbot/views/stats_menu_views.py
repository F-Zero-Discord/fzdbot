import discord
from discord import ui
from fzdbot.utils.event_class import UserStats
from fzdbot.utils.view_utils import NextStep
from fzdbot.utils.status_policies import user_event_status
from fzdbot.views.common import GenericButton, SessionView

#####################################
# Modal classes
#####################################

class Race99Modal(ui.Modal):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(title=f"Provide your race statistics.")

        default_99_entries = self.parent_view.user_stats.races_regular
        default_99_wins = self.parent_view.user_stats.wins_regular

        self.add_item(ui.TextDisplay("Under 'F-ZERO 99 CAREER STATS', what is your 'Total Race Entries'?"))
        self.races_99_input = ui.Label(
            text="Entries",
            component=ui.TextInput(
                default=default_99_entries,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.races_99_input)

        self.add_item(ui.TextDisplay("Under 'F-ZERO 99 CAREER STATS', what is your 'Total Wins'?"))
        self.wins_99_input = ui.Label(
            text="Wins",
            component=ui.TextInput(
                default=default_99_wins,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.wins_99_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Check for valid input
        if not self.races_99_input.component.value.isnumeric() or not self.wins_99_input.component.value.isnumeric():
            raise ValueError(
                f"'Races' and 'Wins' need to be numeric, not {self.races_99_input.component.value} and {self.wins_99_input.component.value}.")
        else:
            self.parent_view.user_stats.races_regular = int(self.races_99_input.component.value)
            self.parent_view.user_stats.wins_regular = int(self.wins_99_input.component.value)
            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # This prints the error straight to your terminal console
        print(f"Error in modal {self.title}: {error}")
        
        # It is highly recommended to notify the user as well
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Something went wrong while processing your request.", 
                ephemeral=True
            )


class RaceClassicModal(ui.Modal):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(title=f"Provide your race statistics.")

        default_classic_entries = self.parent_view.user_stats.races_regular
        default_classic_wins = self.parent_view.user_stats.wins_regular

        self.add_item(ui.TextDisplay("Under 'CLASSIC CAREER STATS', what is your 'Total Race Entries'?"))
        self.races_classic_input = ui.Label(
            text="Entries",
            component=ui.TextInput(
                default=default_classic_entries,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.races_classic_input)

        self.add_item(ui.TextDisplay("Under 'CLASSIC CAREER STATS', what is your 'Total Wins'?"))
        self.wins_classic_input = ui.Label(
            text="Wins",
            component=ui.TextInput(
                default=default_classic_wins,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.wins_classic_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Check for valid input
        if not self.races_classic_input.component.value.isnumeric() or not self.wins_classic_input.component.value.isnumeric():
            raise ValueError(
                f"'Races' and 'Wins' need to be numeric, not {self.races_classic_input.component.value} and {self.wins_classic_input.component.value}.")
        else:
            self.parent_view.user_stats.races_regular = int(self.races_classic_input.component.value)
            self.parent_view.user_stats.wins_regular = int(self.wins_classic_input.component.value)
            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # This prints the error straight to your terminal console
        print(f"Error in modal {self.title}: {error}")
        
        # It is highly recommended to notify the user as well
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Something went wrong while processing your request.", 
                ephemeral=True
            )


class RaceGPModal(ui.Modal):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(title=f"Provide your race statistics.")

        default_gp_races = self.parent_view.user_stats.races_gp
        default_gp_wins = self.parent_view.user_stats.wins_gp

        self.add_item(ui.TextDisplay("Under 'GRAND PRIX CAREER STATS', what is your 'Total Race Entries'?"))
        self.races_gp_input = ui.Label(
            text="Entries",
            component=ui.TextInput(
                default=default_gp_races,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.races_gp_input)

        self.add_item(ui.TextDisplay("Under 'GRAND PRIX CAREER STATS', what is your 'Total Wins'?"))
        self.wins_gp_input = ui.Label(
            text="Wins",
            component=ui.TextInput(
                default=default_gp_wins,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.wins_gp_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Check for valid input
        if not self.races_gp_input.component.value.isnumeric() or not self.wins_gp_input.component.value.isnumeric():
            raise ValueError(
                f"'Races' and 'Wins' need to be numeric, not {self.races_gp_input.component.value} and {self.wins_gp_input.component.value}.")
        else:
            self.parent_view.user_stats.races_gp = int(self.races_gp_input.component.value)
            self.parent_view.user_stats.wins_gp = int(self.wins_gp_input.component.value)
            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # This prints the error straight to your terminal console
        print(f"Error in modal {self.title}: {error}")
        
        # It is highly recommended to notify the user as well
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Something went wrong while processing your request.", 
                ephemeral=True
            )


class RaceProModal(ui.Modal):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(title=f"Provide your race statistics.")

        default_pro_races = self.parent_view.user_stats.races_pro
        default_pro_wins = self.parent_view.user_stats.wins_pro

        self.add_item(ui.TextDisplay("Under 'PRO TRACKS CAREER STATS', what is your 'Total Race Entries'?"))
        self.races_pro_input = ui.Label(
            text="Entries",
            component=ui.TextInput(
                default=default_pro_races,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.races_pro_input)

        self.add_item(ui.TextDisplay("Under 'PRO TRACKS CAREER STATS', what is your 'Total Wins'?"))
        self.wins_pro_input = ui.Label(
            text="Wins",
            component=ui.TextInput(
                default=default_pro_wins,
                style=discord.TextStyle.short,
                max_length=6,
                required=True,
            )
        )
        self.add_item(self.wins_pro_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Check for valid input
        if not self.races_pro_input.component.value.isnumeric() or not self.wins_pro_input.component.value.isnumeric():
            raise ValueError(
                f"'Races' and 'Wins' need to be numeric, not {self.races_pro_input.component.value} and {self.wins_pro_input.component.value}.")
        else:
            self.parent_view.user_stats.races_pro = int(self.races_pro_input.component.value)
            self.parent_view.user_stats.wins_pro = int(self.wins_pro_input.component.value)
            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # This prints the error straight to your terminal console
        print(f"Error in modal {self.title}: {error}")
        
        # It is highly recommended to notify the user as well
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Something went wrong while processing your request.", 
                ephemeral=True
            )


#################################
# LayoutView classes
#################################

class StatViewHistory99(SessionView):
    def __init__(self, ggp_dict: dict, recent_dict: dict, user_stats: UserStats):
        super().__init__()
        self.user_stats: UserStats = user_stats

        intro_text = "The following questions will help us determine which Skill Class you should race in. Please go to Workshop → Records in-game, then find the appropriate information."
        best_result_text = "What was your best result during any of the previous GGPs?"
        most_recent_text = "What was your most recent major F-ZERO 99 event that you played in?"

        self.container = ui.Container()
        self.container.add_item(ui.TextDisplay(content="# Provide Your Statistics"))
        self.container.add_item(ui.TextDisplay(content=intro_text))
        self.container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        self.container.add_item(ui.TextDisplay(content=best_result_text))
        best_selection = self.GGPSelection(self, ggp_dict)
        self.container.add_item(ui.ActionRow(best_selection))

        self.container.add_item(ui.TextDisplay(content=most_recent_text))
        recent_selection = self.RecentSelection(self,recent_dict)
        self.container.add_item(ui.ActionRow(recent_selection))

        self.container_middle = ui.Container()
        self.component_status_manager()

        self.container_bottom = ui.Container()
        # Build the Continue button section
        self.container_bottom.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self.continue_button = GenericButton(parent_view=self, 
                                        selection_id=1, 
                                        button_label="Continue", 
                                        button_color=discord.ButtonStyle.green, 
                                        button_disabled=False, 
                                        next_step=NextStep.CONTINUE
                                          )
        self.back_button = GenericButton(parent_view=self, 
                                        selection_id=None, 
                                        button_label="Back", 
                                        button_color=discord.ButtonStyle.blurple, 
                                        button_disabled=False, 
                                        next_step=NextStep.MENU
                                            )
        self.container_bottom.add_item(ui.ActionRow(self.back_button, self.continue_button))

        self.add_item(self.container)
        self.add_item(self.container_middle)
        self.add_item(self.container_bottom)


    #################################
    # Drowdown subclasses
    #################################

    class GGPSelection(ui.Select):
        def __init__(self, parent_view: SessionView, ggp_dict_list: list[dict]):
            self.parent_view = parent_view

            options = []
            for ggp_dict in ggp_dict_list:
                options.append(discord.SelectOption(label=ggp_dict["best_result"], 
                                        description=None,
                                        default=(self.parent_view.user_stats.best_result_id == ggp_dict["id"]),
                                        value=ggp_dict["id"]
                ))
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.user_stats.best_result_id = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = (int(option.value) == int(self.values[0]))

            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)


    class RecentSelection(ui.Select):
        def __init__(self, parent_view: SessionView, recent_dict_list: list[dict]):
            self.parent_view = parent_view

            options = []
            for recent_dict in recent_dict_list:
                options.append(discord.SelectOption(label=recent_dict["most_recent"], 
                                        description=None,
                                        default=(self.parent_view.user_stats.most_recent_id == recent_dict["id"]),
                                        value=recent_dict["id"]
                ))
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.user_stats.most_recent_id = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = (int(option.value) == int(self.values[0]))

            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)


    class MachineSelection(ui.Select):
        def __init__(self, parent_view: SessionView, machine_dict_list: list[dict]):
            self.parent_view = parent_view

            options = []
            for machine_dict in machine_dict_list:
                options.append(discord.SelectOption(label=machine_dict["name"], 
                                        description=None, 
                                        value=machine_dict["value"]
                ))
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.machine_id = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = (int(option.value) == int(self.values[0]))

            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)


    #################################
    # Button subclasses
    #################################

    class Button99(ui.Button):
        def __init__(self, parent_view: SessionView):
            self.parent_view = parent_view
            super().__init__(label="Add", 
                            style=discord.ButtonStyle.green,
                            disabled=False
                        )
        async def callback(self, interaction: discord.Interaction):
            # open modal
            await interaction.response.send_modal(Race99Modal(self.parent_view))


    class ButtonGP(ui.Button):
        def __init__(self, parent_view: SessionView):
            self.parent_view = parent_view
            super().__init__(label="Add", 
                            style=discord.ButtonStyle.green,
                            disabled=False
                        )
        async def callback(self, interaction: discord.Interaction):
            # open modal
            await interaction.response.send_modal(RaceGPModal(self.parent_view))


    class ButtonPro(ui.Button):
        def __init__(self, parent_view: SessionView):
            self.parent_view = parent_view
            super().__init__(label="Add", 
                            style=discord.ButtonStyle.green,
                            disabled=False
                        )
        async def callback(self, interaction: discord.Interaction):
            # open modal
            await interaction.response.send_modal(RaceProModal(self.parent_view))


    #################################
    # Class utility methods
    #################################

    def component_status_manager(self):
        """
        """
        self.container_middle.clear_items()

        button99_label = "Add"
        i99_race_text = "None"
        i99_win_text = "None"
        if self.user_stats.races_regular:
            button99_label = "Edit"
            i99_race_text = f"{self.user_stats.races_regular}"
        if self.user_stats.wins_regular:
            button99_label = "Edit"
            i99_win_text = f"{self.user_stats.wins_regular}"
        self.i99_stat_text = f"F-Zero 99 Career Stats\n\tTotal Races: {i99_race_text}\n\tTotal Wins: {i99_win_text}"
        self.i99_stat_button = self.Button99(self)
        self.i99_stat_button.label = button99_label
        self.i99_stats_section = ui.Section(self.i99_stat_text, accessory=self.i99_stat_button)

        buttonpro_label = "Add"
        pro_race_text = "None"
        pro_win_text = "None"
        if self.user_stats.races_pro:
            buttonpro_label = "Edit"
            pro_race_text = f"{self.user_stats.races_pro}"
        if self.user_stats.wins_pro:
            buttonpro_label = "Edit"
            pro_win_text = f"{self.user_stats.wins_pro}"
        self.pro_stat_text = f"Pro Tracks Career Stats\n\tTotal Races: {pro_race_text}\n\tTotal Wins: {pro_win_text}"
        self.pro_stat_button = self.ButtonPro(self)
        self.pro_stat_button.label = buttonpro_label
        self.pro_stats_section = ui.Section(self.pro_stat_text, accessory=self.pro_stat_button)

        buttongp_label = "Add"
        gp_race_text = "None"
        gp_win_text = "None"
        if self.user_stats.races_gp:
            buttongp_label = "Edit"
            gp_race_text = f"{self.user_stats.races_gp}"
        if self.user_stats.wins_gp:
            buttongp_label = "Edit"
            gp_win_text = f"{self.user_stats.wins_gp}"
        self.gp_stat_text = f"Grand Prix Career Stats\n\tTotal Races: {gp_race_text}\n\tTotal Wins: {gp_win_text}"
        self.gp_stat_button = self.ButtonGP(self)
        self.gp_stat_button.label = buttongp_label
        self.gp_stats_section = ui.Section(self.gp_stat_text, accessory=self.gp_stat_button)

        self.container_middle.add_item(self.i99_stats_section)
        self.container_middle.add_item(self.pro_stats_section)
        self.container_middle.add_item(self.gp_stats_section)


class StatViewHistoryClassic(SessionView):
    def __init__(self, user_stats: UserStats):
        super().__init__()
        self.user_stats: UserStats = user_stats

        intro_text = "The following questions will help us determine which Skill Class you should race in. Please go to Workshop → Records in-game, then find the appropriate information."

        self.container = ui.Container()
        self.container.add_item(ui.TextDisplay(content="# Provide Your CLASSIC Statistics"))
        self.container.add_item(ui.TextDisplay(content=intro_text))
        self.container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        self.container_middle = ui.Container()
        self.component_status_manager()

        # Build the Continue button section
        self.container_bottom = ui.Container()
        self.container_bottom.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self.continue_button = GenericButton(parent_view=self, 
                                        selection_id=1, 
                                        button_label="Continue", 
                                        button_color=discord.ButtonStyle.green, 
                                        button_disabled=False, 
                                        next_step=NextStep.CONTINUE
                                          )
        self.back_button = GenericButton(parent_view=self, 
                                        selection_id=None, 
                                        button_label="Back", 
                                        button_color=discord.ButtonStyle.blurple, 
                                        button_disabled=False,
                                        next_step=NextStep.MENU
                                            )
        self.container_bottom.add_item(ui.ActionRow(self.back_button, self.continue_button))

        self.add_item(self.container)
        self.add_item(self.container_middle)
        self.add_item(self.container_bottom)


    #################################
    # Button subclasses
    #################################

    class ButtonClassic(ui.Button):
        def __init__(self, parent_view: SessionView):
            self.parent_view = parent_view
            super().__init__(label="Add", 
                            style=discord.ButtonStyle.green,
                            disabled=False
                        )
        async def callback(self, interaction: discord.Interaction):
            # open modal
            await interaction.response.send_modal(RaceClassicModal(self.parent_view))


    #################################
    # Class utility methods
    #################################

    def component_status_manager(self):
        """
        """
        self.container_middle.clear_items()

        buttonclassic_label = "Add"
        classic_race_text = "None"
        classic_win_text = "None"
        if self.user_stats.races_regular:
            buttonclassic_label = "Edit"
            classic_race_text = f"{self.user_stats.races_regular}"
        if self.user_stats.wins_regular:
            buttonclassic_label = "Edit"
            classic_win_text = f"{self.user_stats.wins_regular}"
        self.classic_stat_text = f"F-Zero 99 Career Stats\n\tTotal Races: {classic_race_text}\n\tTotal Wins: {classic_win_text}"
        self.classic_stat_button = self.ButtonClassic(self)
        self.classic_stat_button.label = buttonclassic_label
        self.classic_stats_section = ui.Section(self.classic_stat_text, accessory=self.classic_stat_button)

        self.container_middle.add_item(self.classic_stats_section)


class BasicStatsView(SessionView):
    def __init__(self, recent_dict: list[dict],
                 self_eval_dict: list[dict],
                 user_stats: UserStats, 
                 timeout = 180):
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
        recent_selection = self.RecentSelection(self,recent_dict)
        self.container.add_item(ui.ActionRow(recent_selection))

        self.container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self.continue_button = GenericButton(parent_view=self, 
                                        selection_id=1, 
                                        button_label="Continue", 
                                        button_color=discord.ButtonStyle.green, 
                                        button_disabled=True, 
                                        next_step=NextStep.CONTINUE
                                            )
        self.back_button = GenericButton(parent_view=self, 
                                        selection_id=None, 
                                        button_label="Back", 
                                        button_color=discord.ButtonStyle.blurple, 
                                        button_disabled=False,
                                        next_step=NextStep.MENU
                                            )
        self.container.add_item(ui.ActionRow(self.back_button, self.continue_button))

        self.component_status_manager()
        self.add_item(self.container)


    #################################
    # Drowdown subclasses
    #################################

    class RecentSelection(ui.Select):
        def __init__(self, parent_view: SessionView, recent_dict_list: list[dict]):
            self.parent_view = parent_view

            options = []
            for recent_dict in recent_dict_list:
                options.append(discord.SelectOption(label=recent_dict["text"], 
                                        description=None,
                                        default=(self.parent_view.user_stats.most_recent_id == recent_dict["id"]),
                                        value=recent_dict["id"]
                ))
            super().__init__(options=options)

        async def callback(self, interaction: discord.Interaction):
            # Assign output to class variable
            self.parent_view.user_stats.most_recent_id = int(self.values[0])

            # Set default dropdown option to user's selection
            for option in self.options:
                option.default = (int(option.value) == int(self.values[0]))

            self.parent_view.component_status_manager()
            await interaction.response.edit_message(view=self.parent_view)


    class SelfEvalSelection(ui.Select):
            def __init__(self, parent_view: SessionView, self_eval_dict_list: list[dict]):
                self.parent_view = parent_view
    
                options = []
                for self_eval_dict in self_eval_dict_list:
                    options.append(discord.SelectOption(label=self_eval_dict["text"], 
                                            description=None,
                                            default=(self.parent_view.user_stats.self_eval_id == self_eval_dict["id"]),
                                            value=self_eval_dict["id"]
                    ))
                super().__init__(options=options)
    
            async def callback(self, interaction: discord.Interaction):
                # Assign output to class variable
                self.parent_view.user_stats.self_eval_id = int(self.values[0])
    
                # Set default dropdown option to user's selection
                for option in self.options:
                    option.default = (int(option.value) == int(self.values[0]))
    
                self.parent_view.component_status_manager()
                await interaction.response.edit_message(view=self.parent_view)


    #################################
    # Class utility methods
    #################################

    def component_status_manager(self):
        """
        """
        if not self.user_stats.self_eval_id or not self.user_stats.most_recent_id:
            self.continue_button.disabled = True
        else:
            self.continue_button.disabled = False