from datetime import datetime, timezone
from enum import StrEnum

class NextStep(StrEnum):
    LOADING = "loading"
    LEAVE = "leave"
    MENU = "menu"
    ADD = "add"
    EDIT = "edit"
    # Steps of the /ggp_register flow.
    #   STATS           - show the statistics screen
    #   CONTINUE        - statistics done; resume the step that asked for them
    #   COMMIT_ADD      - write the registration, then return to the menu
    #   COMMIT_WITHDRAW - delete the registration, then return to the menu
    STATS = "stats"
    CONTINUE = "continue"
    COMMIT_ADD = "commit_add"
    COMMIT_WITHDRAW = "commit_withdraw"
    CONFIRM = "confirm"
    WITHDRAW_CONF = "withdraw_conf"
    OPTIONMENU = "option_menu"
    GENERAL = "general"
    TIME = "time"
    DIVTEAM = "divteam"
    PRIX = "prix"
    MACHINE = "machine"
    REGPERIOD = "reg_period"
    DISCORD = "discord"
    CONFIRMDELETE = "confirm_delete"
    NULL = "null"


class Mode(StrEnum):
    NEW = "new"
    EDIT = "edit"


class DivTeam(StrEnum):
    DIVISION = "division"
    TEAM = "team"
    NEITHER = "neither"


def time_string_to_datetime(time_string: str, fmt='%Y-%m-%d %H:%M') -> datetime | None:
    """Parse a date-time string with the specified format."""
    try:
        return datetime.strptime(time_string, fmt).replace(tzinfo=timezone.utc)
    except ValueError as e:
        print(f"Invalid input '{time_string}': {e}")
        return None


def discord_timestamp(dt: datetime, format_type: str = "short") -> str | None:
    """Convert a datetime object to a Discord-formatted timestamp string."""
    match format_type:
        case "short":
            format_type = "t"
        case "relative":
            format_type = "R"
        case "full":
            format_type = "F"
        case "long":
            format_type = "f"
    if dt:
        unix_timestamp = round(int(dt.timestamp()))
        return f"<t:{unix_timestamp}:{format_type}>"
    else:
        return None
    

def emphasize_string(string: str, leftpad_num: int = 2):
    """ Adds a left pad and markdown bold to a text string. 
    """
    return f"{' ' * leftpad_num}**{string}**"

def deemphasize_string(string: str):
    """ Removes padding and astersik from emphasized string.
        Returns string unmodified if emphasis does not exist.
    """
    return string.lstrip(" *").rstrip("*")


def set_step_info() -> list[dict]:
    """ Define the structural configuration for the steps.
        Doesn't technically need to be in the class.
    """
    step_info = [
        {"title": "Step 1: Basic Information"},
        {"title": "Step 2: Event Time"},
        {"title": "Step 3: Divisions and Teams"},
        {"title": "Step 4: Lineup"},
        {"title": "Step 5: Vehicles"},
        {"title": "Step 6: Registration Period"},
        {"title": "Step 7: Discord Channels"}
    ]
    return step_info


def highlight_step(step_info: list[dict], current_step: int) -> str:
    """ Returns a string where the current step is highlighted for printing
    """
    leftpad_num: int = 2

    # Unhighlight all steps to re-initialize
    for item in step_info:
        item = deemphasize_string(string=item["title"])
    
    # Highlight current step for printing
    step_info[current_step]["title"] = emphasize_string(
        string=step_info[current_step]["title"],
        leftpad_num=leftpad_num
        )
    
    # Consolidate titles into a string of checklist items
    checklist_text = ""
    for step in step_info:
        checklist_text += f"{step["title"]}\n"

    return checklist_text