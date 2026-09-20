from typing import Literal
import discord


class InputWarnings:
    """ """

    def __init__(interaction: discord.Interaction): ...

    @staticmethod
    async def respond(interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    @staticmethod
    async def no_event(interaction: discord.Interaction, command: Literal["points", "rank", "edit", "delete"]):
        match command:
            case "points":
                await InputWarnings.respond(
                    interaction, "⚠️  Warning: No event is currently active, score was not added!  "
                )
            case "rank":
                await InputWarnings.respond(
                    interaction, "⚠️  Warning: No event is currently active, rank was not added!  "
                )
            case "edit":
                await InputWarnings.respond(
                    interaction,
                    "⚠️   No current event active, can't edit scores! If you need help, contact an FZD mod",
                )
            case "delete":
                await InputWarnings.respond(
                    interaction,
                    "⚠️   No current event active, can't delete scores! If you need help, contact an FZD mod",
                )

    @staticmethod
    async def wrong_scoring_method(
        interaction: discord.Interaction, event_name: str, method: Literal["points", "rank"]
    ):
        match method:
            case "points":
                await InputWarnings.respond(
                    interaction,
                    f"⚠️  Warning: {event_name} is normal scoring, please submit race/GP points using /fzd_add_score ",
                )
            case "rank":
                await InputWarnings.respond(
                    interaction, f"⚠️  Warning: {method} requires rank results, please use /fzd_add_rank "
                )

    @staticmethod
    async def no_existing_score(interaction: discord.Interaction, name: str):
        await InputWarnings.respond(
            interaction, f"⚠️   No submitted scores found for user {name}! If you need help, contact an FZD mod"
        )

    @staticmethod
    async def edit_disabled(interaction: discord.Interaction):
        await InputWarnings.respond(
            interaction,
            "⚠️   The /fzd_edit_score command does not work with Rank-based Kingmaker-style events! \n"
            + "        If you need to edit a score, you may instead: \n"
            + "               (1) delete the score first with /fzd_delete_score, then \n"
            + "               (2) resubmit your rank with /fzd_add_rank \n"
            + "        Or contact an FZD mod for help!",
        )

    @staticmethod
    async def machine_not_found(interaction: discord.Interaction, machine: str, machine_list: list[str]):
        await InputWarnings.respond(
            interaction, f"⚠️  Warning: {machine} not one of the options {machine_list}. Score not added."
        )

    @staticmethod
    async def machine_needed(interaction: discord.Interaction):
        await InputWarnings.respond(
            interaction, "⚠️  Warning: machine option required for this event. Score not added."
        )
