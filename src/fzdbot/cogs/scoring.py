# Scoring cog class which defines commands related to changing scores in the database:
#         /add_score, /edit_score, /delete_score

import logging

from typing import Literal
import datetime as dt
from datetime import datetime, timedelta, timezone
import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from fzdbot.error_alerts import send_error_alert
from fzdbot.utils.db_utils import get_or_create_db_user
from fzdbot.fzd_db import (
    check_for_active_event,
    get_user_id,
    delete_score,
    edit_score,
    get_db_connection,  # connect_to_database
    get_machines,
    get_machine_config_db,
    get_event_config_flags,
    get_user_scores,
    get_user_times,
    get_event_lineups_and_scores,
    get_default_lineups,
    submit_score_sql,
    submit_time_sql
)
from fzdbot.settings import get_settings
from fzdbot.constants import (
    get_score_constants, 
    get_rank_constants, 
    get_time_constants,
    AUTOCOMPLETE_CACHE_SECONDS,
    AUTOASSIGN_LINEUP_WINDOW
)
from fzdbot.views.confirm_delete import ConfirmDeleteScore
from fzdbot.utils.warnings import (
    InputWarnings,
    NoUserInDatabaseError,
    NoActiveEventError,
    WrongEventTypeError,
    InvalidMachineError,
    InvalidLineupError,
    OptionRequiredError,
    OutOfBoundsError,
    NoResultsToModifyError,
    WrongSelectionError,
)

logger = logging.getLogger(__name__)


class Scoring(commands.Cog):

    # ==========================================================================================
    #   Class Cache Variables
    # ==========================================================================================
    # Set the autocomplete cache and cache expiration
    _OPTIONS_CACHE = {
        "lineup_option_list": [], # list of dict[lineup_id, combined string, time]
        "machine_option_list": [], # list of dict[db_id, name]
        "last_updated": 0
    }
    _EVENT_CONFIG_CACHE = {
        "scoring_method": None,
        "is_lineup_input_required": False,
        "autoassign_score_to_lineup": False,
        "is_machine_input_required": False,
        "is_registration_event": False,
        "last_updated": 0
    }
    _ACTIVE_TTL_SECONDS: int = AUTOCOMPLETE_CACHE_SECONDS
    _AUTOASSIGN_LINEUP_WINDOW: int = AUTOASSIGN_LINEUP_WINDOW


    # ==========================================================================================
    #   Init Method
    # =============================================================================


    def __init__(self, bot: commands.Bot, machine_dict: list[dict[str, str]]):
        self.bot = bot
        self.machine_dict = machine_dict


    # ==========================================================================================
    #   Validation Methods
    # ==========================================================================================

    @staticmethod
    async def validate_user(interaction: discord.Interaction, db_user_id: int) -> int | None:
        """ Checks to see if user is in database. If not, try to create user in database. If 
            that doesn't work, validation fails.
        """
        if not db_user_id:
            # Ensure db_user_id created in database
            async with get_db_connection() as db:
                db_user_id = await get_or_create_db_user(db, interaction.user)
        if not db_user_id:
            InputWarnings.user_not_found(interaction)

        return db_user_id

    
    @staticmethod
    async def validate_event(interaction: discord.Interaction, 
        event: dict, command_scoring_method: Literal["points","placement","time"],
        command_func: Literal["points","placement","edit","delete","time"]) -> None:
        """ Validates both that an event is running and that the user chose the correct
            slash command for the scoring_method/score action.
        """
        if event["name"] == "NULL":
            await InputWarnings.no_event(interaction, command_func)
        if (event["scoring_method"] == "placement" or event["scoring_method"] == "time") and command_func == "edit":
            await InputWarnings.edit_disabled(interaction)
        if event["scoring_method"] != command_scoring_method:
            await InputWarnings.wrong_scoring_method(interaction, 
                event["name"], event["scoring_method"])


    @staticmethod
    async def validate_result(interaction: discord.Interaction, 
        result_list: list[str], scoring_method: Literal["points","placement","time"]) -> int | timedelta:
        """ 
        """
        match scoring_method:
            case "points":
                score_const = get_score_constants()
                result = result_list[0]
                if int(result) < score_const.MIN_SCORE or int(result) > score_const.MAX_SCORE:
                    await InputWarnings.out_of_bounds(interaction, result, score_const.MIN_SCORE, score_const.MAX_SCORE)
                else:
                    return int(result)
            case "placement":
                rank_const = get_rank_constants()
                result = result_list[0]
                if int(result) < rank_const.MIN_RANK or int(result) > rank_const.MAX_RANK:
                    await InputWarnings.out_of_bounds(interaction, result, rank_const.MIN_RANK, rank_const.MAX_RANK)
                else:
                    return int(result)
            case "time":
                time_const = get_time_constants()
                minutes, seconds, centiseconds = result_list
                
                # Check if time parameters are integers. Note that "is_digit" returns 
                #   False for negative numbers.
                if not minutes.isdigit():
                    await InputWarnings.not_integer(interaction, "minutes", "time")
                if not seconds.isdigit():
                    await InputWarnings.not_integer(interaction, "seconds", "time")
                if not centiseconds.isdigit():
                    await InputWarnings.not_integer(interaction, "centiseconds", "time")
                minutes_int = int(minutes)
                seconds_int = int(seconds)
                centiseconds_int = int(centiseconds)
                # Check if time parameters (demonstrated to be integers) are within bounds.
                #   (Note checking for values less than zero belt and suspenders when 
                #   minimums are set to zero.)
                if (minutes_int < time_const.MIN_MINUTES) or (minutes_int > time_const.MAX_MINUTES):
                    await InputWarnings.out_of_bounds(interaction, minutes_int, 
                        time_const.MIN_MINUTES, time_const.MAX_MINUTES)
                if (seconds_int < time_const.MIN_SECONDS) or (seconds_int > time_const.MAX_SECONDS):
                    await InputWarnings.out_of_bounds(interaction, seconds_int, 
                        time_const.MIN_SECONDS, time_const.MAX_SECONDS)  
                if (centiseconds_int < time_const.MIN_CENTISECONDS) or (centiseconds_int > time_const.MAX_CENTISECONDS):
                    await InputWarnings.out_of_bounds(interaction, centiseconds_int, 
                        time_const.MIN_CENTISECONDS, time_const.MAX_CENTISECONDS)
                return dt.time(hour=0, minute=minutes_int, 
                    second=seconds_int, microsecond=centiseconds_int*10000)
            case _:
                raise ValueError(f"Scoring method must be 'points', 'placement' pr 'time', not {scoring_method}.")


    @classmethod
    async def validate_machine(self, interaction: discord.Interaction, machine: str) -> tuple[int,str]:
        """
        """
        # If an unallowed string passed it is provided to the slash command as a value.
        if machine is not None and not machine.isnumeric():
            await InputWarnings.machine_not_found(interaction, machine, self._OPTIONS_CACHE["machine_option_list"])
        # If the machine is valid, but not valid for this event.
        if machine is not None and not any(int(option.get("value")) == int(machine) for option in self._OPTIONS_CACHE["machine_option_list"]) and machine is not None:
            await InputWarnings.machine_not_found(interaction, machine, self._OPTIONS_CACHE["machine_option_list"])
        if machine is not None:
            machine_name = next(
                (item["name"] for item in self._OPTIONS_CACHE["machine_option_list"] if item.get("value") == machine), None)
            return int(machine), machine_name
        else:
            return None, None

    
    @classmethod
    async def validate_lineup(self, interaction: discord.Interaction, lineup: str) -> tuple[int,str]:
        """
        """
        if lineup is not None and not lineup.isnumeric():
            await InputWarnings.lineup_not_found(interaction, lineup)
        if lineup is not None and not any(int(option.get("value")) == int(lineup) for option in self._OPTIONS_CACHE["lineup_option_list"]) and lineup is not None:
            await InputWarnings.lineup_not_found(interaction, lineup)
        if lineup is not None:
            lineup_name = next(
                (item["name"] for item in self._OPTIONS_CACHE["lineup_option_list"] if item.get("value") == lineup), None)
            return int(lineup), lineup_name
        else:
            return None, None
            

    @classmethod
    async def validate_required_options(self, interaction: discord.Interaction, 
        machine: str, lineup: str) -> None:
        """
        """
        if ((self._EVENT_CONFIG_CACHE["is_machine_input_required"] is True and machine is None) and 
            (self._EVENT_CONFIG_CACHE["is_lineup_input_required"] is True and lineup is None)):
            await InputWarnings.machine_and_lineup_needed(interaction)
        if self._EVENT_CONFIG_CACHE["is_machine_input_required"] is True and machine is None:
            await InputWarnings.machine_needed(interaction)
        if self._EVENT_CONFIG_CACHE["is_lineup_input_required"] is True and lineup is None:
            await InputWarnings.lineup_needed(interaction)

    
    @staticmethod
    async def validate_modify_score(interaction: discord.Interaction, 
        score_to_modify: str, scoring_method: Literal["points","placement","time"],
        func: Literal["edit","delete"]) -> tuple[int,int]:
        """
        """
        match func:
            case "edit":
                check_for_score_method=True
            case "delete":
                check_for_score_method=False

        opts = []
        async with get_db_connection() as db:
            match scoring_method:
                case "points" | "placement":
                    valid_options = await get_user_scores(
                        db, interaction.user.name, check_for_score_method=check_for_score_method)
                case "time":
                    valid_options = await get_user_times(db, interaction.user.name)
                case _:
                    raise ValueError(f"Scoring method must be 'points', 'placement', or 'time', not {current_event["scoring_method"]}.")

        if not valid_options:
            await InputWarnings.no_existing_score(interaction)
        opts = [s["score"] for s in valid_options if "score" in s]
        print(f"score_to_modify: {score_to_modify}")
        if "|" in score_to_modify:
            score, idchoice = score_to_modify.split("|")
        else:
            await InputWarnings.result_not_found(interaction)
        if score not in opts:
            await InputWarnings.result_not_found(interaction)

        match scoring_method:
            case "points" | "placement":
                return int(score), int(idchoice)
            case "time":
                return str(score), int(idchoice)


    # ==========================================================================================
    #   Class and Static Methods
    # ==========================================================================================

    @staticmethod
    async def grab_machines() -> list[dict[str, str]]:
        """Fetch machine dictionaries used to initialize the cog."""
        async with get_db_connection() as db:
            return await get_machines(db)


    @classmethod
    async def get_event_config_from_db(self, discord_name: str) -> tuple[dict, int]:
        """ Gets config information to support autocomplete and slash commands.
            Triggered upon autocomplete when cache expired.
        """
        # Clear existing cache
        self._OPTIONS_CACHE["lineup_option_list"] = []
        self._OPTIONS_CACHE["machine_option_list"] = []
        self._EVENT_CONFIG_CACHE["scoring_method"] = None
        self._EVENT_CONFIG_CACHE["is_lineup_input_required"] = False
        self._EVENT_CONFIG_CACHE["autoassign_score_to_lineup"] = False
        self._EVENT_CONFIG_CACHE["is_machine_input_required"] = False
        self._EVENT_CONFIG_CACHE["is_registration_event"] = False

        # Initialize local variables
        event_config_flag_dict: dict | None = None
        
        # Load config information from the database
        async with get_db_connection() as db:
            active_event = await check_for_active_event(db)
            user_id = await get_user_id(db, discord_name)

            if active_event["name"] != "NULL" and user_id:
                lineup_dict_list = await get_event_lineups_and_scores(
                    db, active_event["id"], active_event["scoring_method"], user_id
                    )
                
                machine_dict_list = await get_machine_config_db(db, active_event["id"])
                if not machine_dict_list:
                    machine_dict_list = await get_machines(db)

                event_config_flag_dict = await get_event_config_flags(db, active_event["id"])

        # Set event flags
        if event_config_flag_dict:
            self._EVENT_CONFIG_CACHE["is_lineup_input_required"] = event_config_flag_dict["is_lineup_input_required"]
            self._EVENT_CONFIG_CACHE["autoassign_score_to_lineup"] = event_config_flag_dict["autoassign_score_to_lineup"]
            self._EVENT_CONFIG_CACHE["is_machine_input_required"] = event_config_flag_dict["is_machine_input_required"]
            self._EVENT_CONFIG_CACHE["is_registration_event"] = event_config_flag_dict["is_registration_event"]
        
        # Assign scoring_method, lineup_option_list, and machine_option_list
        if active_event["name"] != "NULL":
            self._EVENT_CONFIG_CACHE["scoring_method"] = active_event["scoring_method"]
            # Set scoring label based on scoring_method
            match self._EVENT_CONFIG_CACHE["scoring_method"]:
                case "points" | "placement":
                    scoring_label = "score"
                case "time":
                    scoring_label = "time"
                case _:
                    raise ValueError(f"Scoring method must be 'points', 'placement', or 'time', not '{active_event["scoring_method"]}'.")

            # Get the lineup list in format easy to create app_commands.Choice entries with.
            if lineup_dict_list:
                # Display text gathered from multiple fields
                for lineup_dict in lineup_dict_list:
                    lineup_string = f"{lineup_dict["lineup_num"]}: {lineup_dict["lineup_name"]}"
                    if lineup_dict["score"]:
                        match scoring_label:
                            case "score":
                                lineup_string += f" - {scoring_label} {lineup_dict["score"]}"
                            case "time":
                                lineup_string += f" - {scoring_label} {(lineup_dict["score"] + datetime.min).strftime("%M:%S:%f")[:-4]}"
                    self._OPTIONS_CACHE["lineup_option_list"].append({
                            "name": lineup_string, 
                            "value": str(lineup_dict["event_lineup_id"]), 
                            "start_time": lineup_dict["start_time"].replace(tzinfo=timezone.utc) if lineup_dict["start_time"] is not None else None
                            })
                # else condition managed in autocomplete method.

            # Get the machine list in format easy to create app_commands.Choice entries with.
            for machine_dict in machine_dict_list:
                self._OPTIONS_CACHE["machine_option_list"].append(
                    {"name": machine_dict["name"], "value": str(machine_dict["id"])})

        self._OPTIONS_CACHE["last_updated"] = time.monotonic()

        return active_event, user_id


    @classmethod
    def check_for_active_lineup(self) -> tuple[int | None, str | None]:
        """ Checks cache data to see if there is a lineup with a 
            start time associated with the result submission.
        """
        # First Check: Check to see if the lineups have times associated 
        #   with them. Create new list with all lineups where "start_time" 
        #   exists. If resulting list does not exist, return None
        print("First Check")
        lineup_option_list = [
            lineup for lineup in self._OPTIONS_CACHE["lineup_option_list"] if lineup["start_time"] is not None
            ]
        print(f"lineup_option_list: {lineup_option_list}")
        if not lineup_option_list:
            return None, None

        # Second Check: Remove all lineups with times > datetime.now. If 
        #   none are left, return None.
        print("Second Check")
        now = datetime.now(timezone.utc)
        lineup_option_list = [lineup for lineup in lineup_option_list if lineup["start_time"] <= now]
        if not lineup_option_list:
            return None, None
        
        # Third Check: Find the lineup in the list with the time closest 
        #   to datetime.now but are not after.
        print("Third Check")
        closest_lineup: None | dict = None
        for lineup in lineup_option_list:
            if closest_lineup == None:
                closest_lineup = lineup
            elif abs(now - lineup["start_time"]) < abs(now - closest_lineup["start_time"]):
                closest_lineup = lineup

        # Fourth Check: See if closest past datetime is within window.
        #   Rather than let the last race/lineup be used until the end 
        #   of the event, only allow assignment to lineup until a config
        #   expiry. Submissions after that time are not assigned to a 
        #   lineup. If past expiry, return None.
        print("Fourth Check")
        if abs(now - closest_lineup["start_time"]) > timedelta(seconds=self._AUTOASSIGN_LINEUP_WINDOW):
            return None, None
        else:
            return int(closest_lineup["value"]), closest_lineup["name"]


    # ==================================================================
    # Autocomplete handlers
    # ==================================================================

    async def user_scores_autocomplete(self, interaction: discord.Interaction, current: str):
        """ Score order is presented to the user by order of entry if there are no
            lineups, or by lineup order if there are lineups.
        """
        clock = time.monotonic()

        if clock - self._OPTIONS_CACHE["last_updated"] < self._ACTIVE_TTL_SECONDS:
            # Do not call database to update event config info
            ...
        else:
            active_event, user_id = await self.get_event_config_from_db(interaction.user.name)
            if not active_event:
                return [app_commands.Choice(name="No active event", value="-1")]
            if not user_id:
                return [app_commands.Choice(name="No user id. Use command `/fzd_set_name` to add yourself.", value="-2")]  
        
        async with get_db_connection() as db:
            match self._EVENT_CONFIG_CACHE["scoring_method"]:
                case "points" | "placement":
                    user_score_info = await get_user_scores(db, interaction.user.name)
                    scoring_label = "score"
                case "time":
                    user_score_info = await get_user_times(db, interaction.user.name)
                    scoring_label = "time"
                case _:
                    raise ValueError(f"Scoring method must be 'points', 'placement', or 'time', not {self._EVENT_CONFIG_CACHE["scoring_method"]}.")

        sort_flag = False
        unsorted_scores = []
        for i, score in enumerate(user_score_info):
            if score["lineup_num"] and score["lineup_name"]:
                sort_flag = True
                unsorted_scores.append(
                    {"display": f"{score["lineup_num"]}: {score["lineup_name"]} - {scoring_label} {score["score"]}",
                    "score": score["score"],
                    "id": score["id"]}
                )
            else:
                unsorted_scores.append(
                    {"display": score["score"],
                    "score": score["score"],
                    "id": score["id"]}
                )
            if sort_flag:
                user_scores = sorted(unsorted_scores, key=lambda x:x["display"])
            else:
                user_scores = unsorted_scores

        # Filter based on what the user is currently typing
        choices = [(opt["display"], opt["score"], opt["id"]) for opt in user_scores if current.casefold() in opt["display"].casefold()]
        # Return up to 25 results (discord limit)
        return [app_commands.Choice(name=opt[0], value=f"{opt[1]}|{opt[2]}") for opt in choices[:25]]


    async def user_scores_autocomplete_nokingmaker(self, interaction: discord.Interaction, current: str):
        """ Score order is presented to the user by order of entry if there are no
            lineups, or by lineup order if there are lineups.
        """
        async with get_db_connection() as db:
            user_score_info = await get_user_scores(db, interaction.user.name, check_for_score_method=True)

        sort_flag = False
        unsorted_scores = []
        for i, score in enumerate(user_score_info):
            if score["lineup_num"] and score["lineup_name"]:
                sort_flag = True
                unsorted_scores.append(
                    {"display": f"{score["lineup_num"]}: {score["lineup_name"]} - score {score["score"]}",
                    "score": score["score"],
                    "id": score["id"]}
                )
            else:
                unsorted_scores.append(
                    {"display": score["score"],
                    "score": score["score"],
                    "id": score["id"]}
                )
            if sort_flag:
                user_scores = sorted(unsorted_scores, key=lambda x:x["display"])
            else:
                user_scores = unsorted_scores

        # Filter based on what the user is currently typing
        choices = [(opt["display"], opt["score"], opt["id"]) for opt in user_scores if current.casefold() in opt["display"].casefold()]
        # Return up to 25 results (discord limit)
        return [app_commands.Choice(name=opt[0], value=f"{opt[1]}|{opt[2]}") for opt in choices[:25]]


    async def machine_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """ Autocomplete to present machine options.
        """
        clock = time.monotonic()

        if self._OPTIONS_CACHE["machine_option_list"] is not None and clock - self._OPTIONS_CACHE["last_updated"] < self._ACTIVE_TTL_SECONDS:
            # Use existing cache values
            # Assume no need to check whether the event is active, given the short
            #   cache window.
            ...
        else:
            active_event, user_id = await self.get_event_config_from_db(interaction.user.name)
            if not active_event:
                return [app_commands.Choice(name="No active event", value="-1")]
            if not user_id:
                return [app_commands.Choice(name="No user id. Use command `/fzd_set_name` to add yourself.", value="-2")]
        
        options = [opt for opt in self._OPTIONS_CACHE["machine_option_list"] if current.casefold() in opt["name"].casefold()]
        return [app_commands.Choice(name=option["name"], value=option["value"]) for option in options[:4]]


    async def lineup_score_autocomplete(self, 
            interaction: discord.Interaction, 
            current: str) -> list[app_commands.Choice[str]]:
        """ Autocomplete to present lineup options.
        """
        clock = time.monotonic()

        if self._OPTIONS_CACHE["lineup_option_list"] is not None and clock - self._OPTIONS_CACHE["last_updated"] < self._ACTIVE_TTL_SECONDS:
            # Use existing cache values
            # Assume no need to check whether the event is active, given the short
            #   cache window.
            ...
        else:
            # Refresh lineup information from database and get info to confirm
            #   that there is an active event and user is in the database.
            active_event, user_id = await self.get_event_config_from_db(interaction.user.name)
            if not active_event:
                return [app_commands.Choice(name="No active event", value=str(-1))]
            if not user_id:
                return [app_commands.Choice(name="No user id. Use command `/fzd_set_name` to add yourself.", value=str(-2))]
        
        if not self._OPTIONS_CACHE["lineup_option_list"]:
            return [app_commands.Choice(name="No lineups are available for this event.", value=str(-3))]
                    
        options = [opt for opt in self._OPTIONS_CACHE["lineup_option_list"] if current.casefold() in opt["name"].casefold()]
        # Note discord limit is 25 options; only first 25 options provided if exceeded.
        return [app_commands.Choice(name=option["name"], value=str(option["value"])) for option in options[:25]]
            

    # ==========================================================================================
    #   Begin slash commands
    # ==========================================================================================

    # =============================================================================================================
    #   /add_score
    # =============================================================================================================

    # Add a score to an event
    @app_commands.command(
        name="fzd_add_score", description="Add score to FZD scoreboard database"
    )  # , guild=GUILD_ID)
    @app_commands.describe(score="Enter an integer value for the score during an event")
    @app_commands.describe(machine="Select the machine used")
    @app_commands.describe(lineup="Select the race or prix you participated in")
    async def add_score(self, interaction: discord.Interaction, 
        score: str, machine: str | None = None, lineup: str | None = None) -> None:

        try:
            current_event, db_user_id = await self.get_event_config_from_db(interaction.user.name)

            # Validate user, event, and user input
            db_user_id = await Scoring.validate_user(interaction, db_user_id)
            await Scoring.validate_event(interaction, current_event, "points", "points")
            score_int = await Scoring.validate_result(interaction, [score], "points")
            machine_id, machine_name = await Scoring.validate_machine(interaction, machine)
            lineup_id, lineup_name = await Scoring.validate_lineup(interaction, lineup)
            await Scoring.validate_required_options(interaction, machine, lineup)

            # If event calls for time-based lineup assignment, do it here
            if not lineup_id:
                lineup_id, lineup_name = Scoring.check_for_active_lineup()

            user_data = [
                db_user_id,
                current_event["id"],
                score_int,
                current_event["scoring_method"],
                machine_id,
                lineup_id,
            ]

            # Add score to database
            async with get_db_connection() as db:
                return_score = await submit_score_sql(db, user_data)  # interaction.user
            await interaction.response.send_message(
                f"✅ User {interaction.user} has entered a score of {return_score} to {current_event['name']} using machine {machine_name} for race/prix '{lineup_name}'"
            )  # , ephemeral=True)
            logger.info(
                "User %s entered score=%s for event=%s machine=%s",
                interaction.user,
                score,
                current_event["name"],
                machine_name,
            )

        except NoUserInDatabaseError as e: logger.warning(f"{e.name}: {e.message}")
        except NoActiveEventError as e: logger.warning(f"{e.name}: {e.message}")
        except WrongEventTypeError as e: logger.warning(f"{e.name}: {e.message}")
        except InvalidMachineError as e: logger.warning(f"{e.name}: {e.message}")
        except InvalidLineupError as e: logger.warning(f"{e.name}: {e.message}")
        except OptionRequiredError as e: logger.warning(f"{e.name}: {e.message}")
        except OutOfBoundsError as e: logger.warning(f"{e.name}: {e.message}")
        except ValueError as e: logger.warning(f"{e}")
        except Exception as error:
            await interaction.response.send_message(
                "❌ ERROR! Something went wrong, contact FZD staff for help! ", ephemeral=True
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
    @app_commands.describe(lineup="Select the race or prix you participated in")
    async def add_rank(self, interaction: discord.Interaction, 
        rank: str, machine: str | None = None, lineup: str | None = None) -> None:

        try:
            current_event, db_user_id = await self.get_event_config_from_db(interaction.user.name)

            # Validate user, event, and user input
            db_user_id = await Scoring.validate_user(interaction, db_user_id)
            await Scoring.validate_event(interaction, current_event, "placement", "placement")
            rank_int = await Scoring.validate_result(interaction, [rank], "placement")
            machine_id, machine_name = await Scoring.validate_machine(interaction, machine)
            lineup_id, lineup_name = await Scoring.validate_lineup(interaction, lineup)
            await Scoring.validate_required_options(interaction, machine, lineup)

            # If event calls for time-based lineup assignment, do it here
            if not lineup_id:
                lineup_id, lineup_name = Scoring.check_for_active_lineup()

            user_data = [
                db_user_id,
                current_event["id"],
                rank_int,
                current_event["scoring_method"],
                machine_id,
                lineup_id,
            ]

            # Add rank to database
            async with get_db_connection() as db:
                return_score = await submit_score_sql(db, user_data)  # interaction.user

            await interaction.response.send_message(
                f"✅ User {interaction.user} has entered rank {rank} → {return_score} points have been added to {current_event['name']} using machine {machine_name} for race/prix '{lineup_name}'"
            )  # , ephemeral=True)
            logger.info(
                "User %s entered rank=%s (%s points) for event=%s machine=%s",
                interaction.user,
                rank,
                return_score,
                current_event["name"],
                machine_name,
            )

        except NoUserInDatabaseError as e: logger.warning(f"{e.name}: {e.message}")
        except NoActiveEventError as e: logger.warning(f"{e.name}: {e.message}")
        except WrongEventTypeError as e: logger.warning(f"{e.name}: {e.message}")
        except InvalidMachineError as e: logger.warning(f"{e.name}: {e.message}")
        except InvalidLineupError as e: logger.warning(f"{e.name}: {e.message}")
        except OptionRequiredError as e: logger.warning(f"{e.name}: {e.message}")
        except OutOfBoundsError as e: logger.warning(f"{e.name}: {e.message}")
        except ValueError as e: logger.warning(f"{e}")
        except Exception as error:
            await interaction.response.send_message(
                "❌ ERROR! Something went wrong, contact FZD staff for help! ", ephemeral=True
            )
            logger.exception("Exception in add_rank for user=%s", interaction.user)
            await send_error_alert(
                self.bot,
                where="fzd_add_rank",
                error=error,
                interaction=interaction,
            )


    # =============================================================================================================
    #   /add_time
    # =============================================================================================================

    # Add a time to an event
    @app_commands.command(
        name="fzd_add_time", description="Add a time to FZD scoreboard."
    )  # , guild=GUILD_ID)
    @app_commands.describe(minutes="Enter an integer value for the minutes")
    @app_commands.describe(seconds="Enter an integer value for the seconds (0-59)")
    @app_commands.describe(minutes="Enter an integer value for the centiseconds (0-99)")
    @app_commands.describe(machine="Select the machine used")
    @app_commands.describe(lineup="Select the race you participated in")
    async def add_time(
        self, interaction: discord.Interaction, 
        minutes: str, seconds: str, centiseconds: str, 
        machine: str | None = None, lineup: str | None = None) -> None:

        # Time limit constants
        time_const = get_time_constants()

        try:
            # Get user and event info, and update config class variables
            current_event, db_user_id = await self.get_event_config_from_db(interaction.user.name)
            
            # Validate user, event, and user input
            db_user_id = await Scoring.validate_user(interaction, db_user_id)
            await Scoring.validate_event(interaction, current_event, "time", "time")
            result_time = await Scoring.validate_result(interaction, [minutes, seconds, centiseconds], "time")
            machine_id, machine_name = await Scoring.validate_machine(interaction, machine)
            lineup_id, lineup_name = await Scoring.validate_lineup(interaction, lineup)
            await Scoring.validate_required_options(interaction, machine, lineup)

            # If event calls for time-based lineup assignment, do it here
            if not lineup_id:
                lineup_id, lineup_name = Scoring.check_for_active_lineup()

            user_data = [
                db_user_id,
                current_event["id"],
                result_time,
                current_event["scoring_method"],
                machine_id,
                lineup_id,
            ]

           # Add time to database
            async with get_db_connection() as db:
                return_score = await submit_time_sql(db, user_data)  # interaction.user
            await interaction.response.send_message(
                f"✅ User {interaction.user} has entered a time of {result_time.strftime("%M:%S:%f")[:-4]} to {current_event['name']} using machine {machine_name} for race/prix '{lineup_name}'"
            )  # , ephemeral=True)
            logger.info(
                "User %s entered score=%s for event=%s machine=%s",
                interaction.user,
                result_time,
                current_event["name"],
                machine_name,
            )

        except NoUserInDatabaseError as e: logger.warning(f"{e.name}: {e.message}")
        except NoActiveEventError as e: logger.warning(f"{e.name}: {e.message}")
        except WrongEventTypeError as e: logger.warning(f"{e.name}: {e.message}")
        except InvalidMachineError as e: logger.warning(f"{e.name}: {e.message}")
        except InvalidLineupError as e: logger.warning(f"{e.name}: {e.message}")
        except OptionRequiredError as e: logger.warning(f"{e.name}: {e.message}")
        except OutOfBoundsError as e: logger.warning(f"{e.name}: {e.message}")
        except ValueError as e: logger.warning(f"{e}")
        except Exception as error:
            await interaction.response.send_message(
                "❌ ERROR! Something went wrong, contact FZD staff for help! ", ephemeral=True
            )
            logger.exception("Exception in add_time for user=%s", interaction.user)
            await send_error_alert(
                self.bot,
                where="fzd_add_time",
                error=error,
                interaction=interaction,
            )


    # # =============================================================================================================
    # #   /edit_score
    # # =============================================================================================================

    # # This command queries the database for scores of a current event to edit for a user
    # @app_commands.command(
    #     name="fzd_edit_score",
    #     description="Edit a submitted score, set it to new_score in FZD scoreboard database",
    # )
    # async def editScore(self, interaction: discord.Interaction, old_score: str, new_score: str) -> None:
    #     #  old_score is returned packed as "<score>|<id>" when a proper option is selected

    #     try:
    #         current_event, db_user_id = await self.get_event_config_from_db(interaction.user.name)

    #         # Validate user, event, and user input
    #         db_user_id = await Scoring.validate_user(interaction, db_user_id)
    #         await Scoring.validate_event(interaction, current_event, "points", "edit")
    #         old_score_int, idchoice = await Scoring.validate_modify_score(interaction, 
    #             old_score,current_event["scoring_method"], "delete")
    #         new_score_int = await Scoring.validate_result(interaction, [new_score], "points")


    #         # Edit score in database
    #         async with get_db_connection() as db:
    #             await edit_score(db, (new_score_int, idchoice))
    #         await interaction.response.send_message(
    #             f"✅ User {interaction.user.name} has modified submitted score from {old_score_int} to {new_score}"
    #         )

    #     except NoUserInDatabaseError as e: logger.warning(f"{e.name}: {e.message}")
    #     except NoActiveEventError as e: logger.warning(f"{e.name}: {e.message}")
    #     except WrongEventTypeError as e: logger.warning(f"{e.name}: {e.message}")
    #     except OutOfBoundsError as e: logger.warning(f"{e.name}: {e.message}")
    #     except NoResultsToModifyError as e: logger.warning(f"{e.name}: {e.message}")
    #     except WrongSelectionError as e: logger.warning(f"{e.name}: {e.message}")
    #     except ValueError as e: logger.warning(f"{e}")
    #     except Exception as error:
    #         logger.exception("Unexpected exception in editScore for user=%s", interaction.user)
    #         await send_error_alert(
    #             self.bot,
    #             where="fzd_edit_score",
    #             error=error,
    #             interaction=interaction,
    #             details={"old_score": old_score, "new_score": new_score},
    #         )
    #         await interaction.response.send_message(
    #             "❌ ERROR! Something went wrong, contact FZD staff for help!",
    #             ephemeral=True,
    #         )

    # =============================================================================================================
    #   /delete_score
    # =============================================================================================================

    # This command queries the database for scores of a current event to delete for a user
    @app_commands.command(
        name="fzd_delete_score", description="Delete a score you have submitted during an ongoing event"
    )
    async def deleteScore(self, interaction: discord.Interaction, score_to_delete: str) -> None:
        #  score_to_delete is returned packed as "<score>|<id>" when a proper option is selected
        opts = []
        try:
            current_event, db_user_id = await self.get_event_config_from_db(interaction.user.name)

            # Validate user, event, and user input
            db_user_id = await Scoring.validate_user(interaction, db_user_id)
            await Scoring.validate_event(interaction, current_event, current_event["scoring_method"], "delete")
            score_int, idchoice = await Scoring.validate_modify_score(interaction, 
                score_to_delete, current_event["scoring_method"], "delete")


            view = ConfirmDeleteScore(interaction)
            await interaction.response.send_message(
                f"⚠️  Are you sure you want to delete '{score_to_delete}' from your scores?",
                view=view,
                ephemeral=True,
            )

            timed_out = await view.wait()
            if timed_out or view.confirmed is None:
                await interaction.followup.send("Timed out — no changes were made.", ephemeral=True)
                return

            if view.confirmed:
                async with get_db_connection() as db:
                    await delete_score(db, [idchoice])
                await interaction.followup.send(
                    content=f"✅ User {interaction.user.name} has successfully deleted '{score_to_delete}' from their submitted scores",
                    ephemeral=False,
                )
            else:
                await interaction.followup.send("Cancelled — no changes were made.", ephemeral=True)


        except NoUserInDatabaseError as e: logger.warning(f"{e.name}: {e.message}")
        except NoActiveEventError as e: logger.warning(f"{e.name}: {e.message}")
        except WrongEventTypeError as e: logger.warning(f"{e.name}: {e.message}")
        except OutOfBoundsError as e: logger.warning(f"{e.name}: {e.message}")
        except NoResultsToModifyError as e: logger.warning(f"{e.name}: {e.message}")
        except WrongSelectionError as e: logger.warning(f"{e.name}: {e.message}")
        except ValueError as e: logger.warning(f"{e}")
        except Exception as error:
            logger.exception("Unexpected exception in deleteScore for user=%s", interaction.user)
            await send_error_alert(
                self.bot,
                where="fzd_delete_score",
                error=error,
                interaction=interaction,
                details={"score_to_delete": score_to_delete},
            )
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ ERROR! Something went wrong, contact FZD staff for help!",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ ERROR! Something went wrong, contact FZD staff for help!",
                    ephemeral=True,
                )


    # Bind autocomplete handler to edit and delete commands in cog
    async def cog_load(self):
        # self.editScore.autocomplete("old_score")(self.user_scores_autocomplete_nokingmaker)
        self.deleteScore.autocomplete("score_to_delete")(self.user_scores_autocomplete)
        self.add_score.autocomplete("machine")(self.machine_autocomplete)
        self.add_score.autocomplete("lineup")(self.lineup_score_autocomplete)
        self.add_rank.autocomplete("machine")(self.machine_autocomplete)
        self.add_rank.autocomplete("lineup")(self.lineup_score_autocomplete)
        self.add_time.autocomplete("machine")(self.machine_autocomplete)
        self.add_time.autocomplete("lineup")(self.lineup_score_autocomplete)


async def setup(bot: commands.Bot):
    settings = get_settings()
    GUILD_ID = discord.Object(id=settings.server_id)
    machine_dict = await Scoring.grab_machines()
    await bot.add_cog(Scoring(bot, machine_dict), guild=GUILD_ID)
