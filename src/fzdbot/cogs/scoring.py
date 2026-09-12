# Scoring cog class which defines commands related to changing scores in the database:
#         /add_score, /edit_score, /delete_score

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.error_alerts import send_error_alert
from fzdbot.fzd_api import FzdApiError
from fzdbot.settings import get_settings
from fzdbot.utils.warnings import InputWarnings
from fzdbot.views.confirm_delete import ConfirmDeleteScore

logger = logging.getLogger(__name__)


class Scoring(commands.Cog):
    def __init__(self, bot: commands.Bot, machine_dict: list[dict[str, str]]):
        self.bot = bot
        self.machine_dict = machine_dict
        self._active_event_at: datetime | None = None
        self._active_event: dict | None = None

    @classmethod
    async def grab_machines(cls, bot: commands.Bot) -> list[dict[str, str]]:
        """Fetch machine dictionaries used to initialize the cog."""
        return await bot.api.machines()

    @staticmethod
    async def respond(interaction: discord.Interaction, content: str, *, ephemeral: bool = False) -> None:
        if interaction.response.is_done():
            if ephemeral:
                await interaction.edit_original_response(content=content)
            else:
                await interaction.followup.send(content, ephemeral=False)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    async def active_event(self) -> dict | None:
        now = datetime.now(timezone.utc)
        if self._active_event_at is not None and now - self._active_event_at < timedelta(seconds=10):
            return self._active_event

        events = await self.bot.api.active_events()
        self._active_event_at = now
        self._active_event = events[0] if events else None
        return self._active_event

    # =============================================================================================================
    #   /add_score
    # =============================================================================================================

    # Add a score to an event
    @app_commands.command(
        name="fzd_add_score", description="Add score to FZD scoreboard database"
    )  # , guild=GUILD_ID)
    @app_commands.describe(score="Enter an integer value for the score during an event")
    @app_commands.describe(machine="Select the machine used")
    async def add_score(self, interaction: discord.Interaction, score: str, machine: str = None):
        maxscore = 1000000  # arbitrarily set for now
        try:
            if int(score) < 0:
                raise ValueError(f"score can't be a negative integer {interaction.user}")
            elif int(score) > maxscore:
                raise OverflowError(f"score entered too large! {interaction.user}")

            await interaction.response.defer()
            machine_list = [s["name"] for s in self.machine_dict if "name" in s]
            current_event = await self.active_event()

            # Warnings
            if current_event is None:
                await InputWarnings.no_event(interaction, "points")
                return
            if current_event["scoring_method"] != "points":
                await InputWarnings.wrong_scoring_method(interaction, current_event["event"], "points")
                return
            if machine not in machine_list and machine is not None:
                await InputWarnings.machine_not_found(interaction, machine, machine_list)
                return
            if current_event.get("machine_input_required") is True and machine is None:
                await InputWarnings.machine_needed(interaction)
                return

            if machine is not None:
                machine_choice = next((item for item in self.machine_dict if item.get("name") == machine), None)
                machine_choice_id = machine_choice["machine_id"] if machine_choice else None
                machine_choice_name = machine_choice["name"] if machine_choice else None
            else:
                machine_choice_id = None
                machine_choice_name = None

            return_score = await self.bot.api.add_score(
                interaction.user.id,
                interaction.user.name,
                interaction.user.display_name[:10],
                current_event["scheduled_event_id"],
                int(score),
                machine_choice_id,
            )
            await self.respond(
                interaction,
                f"✅ User {interaction.user} has entered a score of {return_score['points']} to {current_event['event']} using machine {machine_choice_name}",
            )  # , ephemeral=True)
            logger.info(
                "User %s entered score=%s for event=%s machine=%s",
                interaction.user,
                score,
                current_event["event"],
                machine_choice_name,
            )

        except (
            ValueError
        ):  # should catch negative numbers and any errors with int(score) if score is not a base 10 integer
            await self.respond(
                interaction, "❌ ERROR! 'score' must be entered as a positive integer!  ", ephemeral=True
            )
        except OverflowError:
            await self.respond(
                interaction,
                f"❌ ERROR! 'score' should not be larger than {maxscore}. Please be nice to Nightmare's bot.",
                ephemeral=True,
            )
        except FzdApiError as error:
            logger.warning("FZD API error in add_score for user=%s: %s", interaction.user, error)
            await self.respond(interaction, f"❌ ERROR! {error}", ephemeral=True)
        except Exception as error:
            await self.respond(
                interaction, "❌ ERROR! Something went wrong, contact FZD staff for help! ", ephemeral=True
            )
            logger.exception("Exception in add_score for user=%s", interaction.user)
            await send_error_alert(
                self.bot,
                where="fzd_add_score",
                error=error,
                interaction=interaction,
            )

    # =============================================================================================================
    #   /add_rank
    # =============================================================================================================

    # Add a rank to an event
    @app_commands.command(
        name="fzd_add_rank", description="Add rank placement to FZD scoreboard (i.e. for Kingmaker events)"
    )  # , guild=GUILD_ID)
    @app_commands.describe(rank="Enter an integer value for the placement rank (1-99) during an event")
    @app_commands.describe(machine="Select the machine used")
    async def add_rank(self, interaction: discord.Interaction, rank: str, machine: str = None):
        maxrank = 99
        try:
            if int(rank) < 1 or int(rank) > maxrank:
                raise ValueError(f"rank must be between 1 and 99 {interaction.user}")

            await interaction.response.defer()
            machine_list = [s["name"] for s in self.machine_dict if "name" in s]
            current_event = await self.active_event()

            logger.debug("add_rank current_event=%r", current_event)
            if current_event is None:
                await InputWarnings.no_event(interaction, "rank")
                return
            if current_event["scoring_method"] == "points":
                await InputWarnings.wrong_scoring_method(interaction, current_event["event"], "points")
                return
            if machine not in machine_list and machine is not None:
                await InputWarnings.machine_not_found(interaction, machine, machine_list)
                return
            if current_event.get("machine_input_required") is True and machine is None:
                await InputWarnings.machine_needed(interaction)
                return

            if machine is not None:
                machine_choice = next((item for item in self.machine_dict if item.get("name") == machine), None)
                machine_choice_id = machine_choice["machine_id"] if machine_choice else None
                machine_choice_name = machine_choice["name"] if machine_choice else None
            else:
                machine_choice_id = None
                machine_choice_name = None

            return_score = await self.bot.api.add_score(
                interaction.user.id,
                interaction.user.name,
                interaction.user.display_name[:10],
                current_event["scheduled_event_id"],
                int(rank),
                machine_choice_id,
            )

            await self.respond(
                interaction,
                f"✅ User {interaction.user} has entered rank {rank} → {return_score['points']} points have been added to {current_event['event']} using machine {machine_choice_name}",
            )  # , ephemeral=True)
            logger.info(
                "User %s entered rank=%s (%s points) for event=%s machine=%s",
                interaction.user,
                rank,
                return_score,
                current_event["event"],
                machine_choice_name,
            )

        except (
            ValueError
        ):  # should catch negative numbers and any errors with int(score) if score is not a base 10 integer
            await self.respond(interaction, "❌ ERROR! 'rank' must be between 1 and 99!  ", ephemeral=True)
        except FzdApiError as error:
            logger.warning("FZD API error in add_rank for user=%s: %s", interaction.user, error)
            await self.respond(interaction, f"❌ ERROR! {error}", ephemeral=True)
        except Exception as error:
            await self.respond(
                interaction, "❌ ERROR! Something went wrong, contact FZD staff for help! ", ephemeral=True
            )
            logger.exception("Exception in add_rank for user=%s", interaction.user)
            await send_error_alert(
                self.bot,
                where="fzd_add_rank",
                error=error,
                interaction=interaction,
            )

    # ------------------------------------------------------------------
    # Autocomplete handler for editScore and deleteScore
    # ------------------------------------------------------------------
    async def user_scores_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            active_event = await self.active_event()
            if active_event is None:
                user_scores = [{"points": "NO CURRENT EVENT", "score_id": "-999"}]
            else:
                user_scores = await self.bot.api.list_scores(
                    interaction.user.id, active_event["scheduled_event_id"]
                )
                if not user_scores:
                    user_scores = [{"points": "NO USER SCORES FOUND", "score_id": "-999"}]
        except FzdApiError as error:
            if error.status == 404:
                user_scores = [{"points": "NO USER SCORES FOUND", "score_id": "-999"}]
            else:
                logger.warning("FZD API error in score autocomplete for user=%s: %s", interaction.user, error)
                return []

        # Filter based on what the user is currently typing
        choices = [
            (str(opt["points"]), opt["score_id"])
            for opt in user_scores
            if current.lower() in str(opt["points"]).lower()
        ]
        # Return up to 25 results (discord limit)
        return [app_commands.Choice(name=opt, value=f"{opt}|{idopt}") for opt, idopt in choices[:25]]

    async def user_scores_autocomplete_nokingmaker(self, interaction: discord.Interaction, current: str):
        try:
            active_event = await self.active_event()
            if active_event is None:
                user_scores = [{"points": "NO CURRENT EVENT", "score_id": "-999"}]
            elif active_event["scoring_method"] == "placement":
                user_scores = [{"points": "DISABLED FOR THIS EVENT", "score_id": "-888"}]
            else:
                user_scores = await self.bot.api.list_scores(
                    interaction.user.id, active_event["scheduled_event_id"]
                )
                if not user_scores:
                    user_scores = [{"points": "NO USER SCORES FOUND", "score_id": "-999"}]
        except FzdApiError as error:
            if error.status == 404:
                user_scores = [{"points": "NO USER SCORES FOUND", "score_id": "-999"}]
            else:
                logger.warning("FZD API error in score autocomplete for user=%s: %s", interaction.user, error)
                return []

        # Filter based on what the user is currently typing
        choices = [
            (str(opt["points"]), opt["score_id"])
            for opt in user_scores
            if current.lower() in str(opt["points"]).lower()
        ]
        # Return up to 25 results (discord limit)
        return [app_commands.Choice(name=opt, value=f"{opt}|{idopt}") for opt, idopt in choices[:25]]

    async def machine_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        machine_list = [s["name"] for s in self.machine_dict if "name" in s]
        options = [machine for machine in machine_list if current.lower() in machine.lower()]
        return [app_commands.Choice(name=machine, value=machine) for machine in options[:4]]

    # =============================================================================================================
    #   /edit_score
    # =============================================================================================================

    # This command queries the database for scores of a current event to edit for a user
    @app_commands.command(
        name="fzd_edit_score",
        description="Edit a submitted score, set it to new_score in FZD scoreboard database",
    )
    async def editScore(self, interaction: discord.Interaction, old_score: str, new_score: str):
        #  old_score is returned packed as "<score>|<id>" when a proper option is selected
        opts = []
        try:
            await interaction.response.defer()
            current_event = await self.active_event()
            if current_event is None:
                await InputWarnings.no_event(interaction, "edit")
                return
            if current_event["scoring_method"] == "placement":
                await InputWarnings.edit_disabled(interaction)
                return
            try:
                valid_options = await self.bot.api.list_scores(
                    interaction.user.id, current_event["scheduled_event_id"]
                )
            except FzdApiError as error:
                if error.status != 404:
                    raise
                valid_options = []
            if not valid_options:
                valid_options = [{"points": "NO USER SCORES FOUND", "score_id": "-999"}]
            opts = [str(s["points"]) for s in valid_options]
            score, idchoice = old_score.split("|")
            if score not in opts:
                raise ValueError("score {score} not one of the options {opts}")

            # Warnings
            if score == "NO USER SCORES FOUND":
                await InputWarnings.no_existing_score(interaction, interaction.user.name)
                return

            await self.bot.api.edit_score(interaction.user.id, int(idchoice), int(new_score))
            await self.respond(
                interaction,
                f"✅ User {interaction.user.name} has modified submitted score from {score} to {new_score}",
            )

        except (ValueError, TypeError) as e:
            logger.warning("Exception in editScore for user=%s: %s", interaction.user, e)
            await self.respond(
                interaction,
                "❌  ERROR! Both options 'old_score' and 'new_score'  must be entered as integers! \n"
                + f"    And 'old_score' must be one of the available options for you: {opts} \n"
                + f"    ---> You chose: '{old_score}'",
                ephemeral=True,
            )
        except FzdApiError as error:
            logger.warning("FZD API error in editScore for user=%s: %s", interaction.user, error)
            await self.respond(interaction, f"❌ ERROR! {error}", ephemeral=True)
        except Exception as error:
            logger.exception("Unexpected exception in editScore for user=%s", interaction.user)
            await send_error_alert(
                self.bot,
                where="fzd_edit_score",
                error=error,
                interaction=interaction,
                details={"old_score": old_score, "new_score": new_score},
            )
            await self.respond(
                interaction,
                "❌ ERROR! Something went wrong, contact FZD staff for help!",
                ephemeral=True,
            )

    # =============================================================================================================
    #   /delete_score
    # =============================================================================================================

    # This command queries the database for scores of a current event to delete for a user
    @app_commands.command(
        name="fzd_delete_score", description="Delete a score you have submitted during an ongoing event"
    )
    async def deleteScore(self, interaction: discord.Interaction, score_to_delete: str):
        #  score_to_delete is returned packed as "<score>|<id>" when a proper option is selected
        opts = []
        try:
            await interaction.response.defer(ephemeral=True)
            current_event = await self.active_event()
            if current_event is None:
                await InputWarnings.no_event(interaction, "delete")
                return
            try:
                valid_options = await self.bot.api.list_scores(
                    interaction.user.id, current_event["scheduled_event_id"]
                )
            except FzdApiError as error:
                if error.status != 404:
                    raise
                valid_options = []
            if not valid_options:
                valid_options = [{"points": "NO USER SCORES FOUND", "score_id": "-999"}]
            opts = [str(s["points"]) for s in valid_options]
            score, idchoice = score_to_delete.split("|")

            if score not in opts:
                raise ValueError("score {score} not one of the options {opts}")

            # Warnings
            if score == "NO USER SCORES FOUND":
                await InputWarnings.no_existing_score(interaction, interaction.user.name)
                return

            view = ConfirmDeleteScore(interaction)
            await interaction.followup.send(
                f"⚠️  Are you sure you want to delete '{score}' from your scores?",
                view=view,
                ephemeral=True,
            )

            timed_out = await view.wait()
            if timed_out or view.confirmed is None:
                await interaction.followup.send("Timed out — no changes were made.", ephemeral=True)
                return

            if view.confirmed:
                await self.bot.api.delete_score(interaction.user.id, int(idchoice))
                await interaction.followup.send(
                    content=f"✅ User {interaction.user.name} has successfully deleted '{score}' from their submitted scores",
                    ephemeral=False,
                )
            else:
                await interaction.followup.send("Cancelled — no changes were made.", ephemeral=True)

        except (ValueError, TypeError) as e:
            logger.warning("Exception in deleteScore for user=%s: %s", interaction.user, e)
            await self.respond(
                interaction,
                f"❌  ERROR! 'score_to_delete' must be one of the available options for you: {opts} \n"
                + f"    ---> You chose: '{score_to_delete}'",
                ephemeral=True,
            )
        except FzdApiError as error:
            logger.warning("FZD API error in deleteScore for user=%s: %s", interaction.user, error)
            await self.respond(interaction, f"❌ ERROR! {error}", ephemeral=True)
        except Exception as error:
            logger.exception("Unexpected exception in deleteScore for user=%s", interaction.user)
            await send_error_alert(
                self.bot,
                where="fzd_delete_score",
                error=error,
                interaction=interaction,
                details={"score_to_delete": score_to_delete},
            )
            await self.respond(
                interaction,
                "❌ ERROR! Something went wrong, contact FZD staff for help!",
                ephemeral=True,
            )

    # Bind autocomplete handler to edit and delete commands in cog
    async def cog_load(self):
        self.editScore.autocomplete("old_score")(self.user_scores_autocomplete_nokingmaker)
        self.deleteScore.autocomplete("score_to_delete")(self.user_scores_autocomplete)
        self.add_score.autocomplete("machine")(self.machine_autocomplete)
        self.add_rank.autocomplete("machine")(self.machine_autocomplete)


async def setup(bot: commands.Bot):
    settings = get_settings()
    GUILD_ID = discord.Object(id=settings.server_id)
    machine_dict = await Scoring.grab_machines(bot)
    await bot.add_cog(Scoring(bot, machine_dict), guild=GUILD_ID)
