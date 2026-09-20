from datetime import UTC, datetime
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


def time_string_to_datetime(time_string: str, fmt="%Y-%m-%d %H:%M") -> datetime | None:
    """Parse a date-time string with the specified format."""
    try:
        return datetime.strptime(time_string, fmt).replace(tzinfo=UTC)
    except ValueError as e:
        print(f"Invalid input '{time_string}': {e}")
        return None


def discord_timestamp(dt: datetime | None, format_type: str = "short") -> str | None:
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
