# This file contains format functions for displaying results from past events into discord,
# mainly used in the "/show" command as of right now
from datetime import timezone
from fzdbot.settings import get_settings
# <:CupAPACChamp:1239639234500104235>
# <:CupMachineChamp:1239640891526742036>
# <:CupCrackChamp:1239639247582138440>


def format_discord_timestamp(dt, inline=False) -> str:
    """Flexible timestamp builder.
    If the event is not in the next few hours, it will use
    a different format automatically.
    Set inline to True if the timestamp appears in a
    started sentence.
    """
    t_format = "f"
    if inline:
        particle = "on "
    else:
        particle = ""
    text = "{0}<t:{1}:{2}>"
    return text.format(particle, int(dt.timestamp()), t_format)


def format_scoreboard_display_text(allscores) -> list[str]:
    """Takes as input the list of dictionaries with players and scores from sql,
    calculates rank (assuming the input is ordered already by max score)
    and outputs a list of text lines for each player,
    where each line contains rank, player name, and scores
    (with some other formatting/flair for a nice-looking scoreboard display)
    """

    qualemoji = "<:LuckyRank:1213541741450231878>"
    scoreboard = []
    isBelowPodium = False
    display_podium = get_settings().scoreboard_display_podium

    if "is_qualified" in allscores[0] and any(d.get("is_qualified") == 1 for d in allscores):
        scoreboard.append(f"*{qualemoji} = already qualified for Team World*\n")

    # Display team scores at the top
    team_names = {d["team"] for d in allscores}
    team_names.discard(None)  # Remove any non-team entries
    if team_names:
        team_scores = []
        for team in team_names:
            team_scores.append((sum([s["score"] for s in allscores if s["team"] == team]), team))

        sorted_team_results = sorted(team_scores, reverse=True)

        rank = 0
        last_score = None
        for iscore, (score, team) in enumerate(sorted_team_results):
            if score != last_score:
                rank = iscore + 1

            rankdisplay = " " + str(rank) + "\\."

            if display_podium:
                if rank == 1:
                    rankdisplay = "<:1st:1201576405339754546> "  # emoji=":trophy: "
                elif rank == 2:
                    rankdisplay = "<:2nd:1201576409638903858> "  # ":second_place: "
                elif rank == 3:
                    rankdisplay = "<:3rd:1201576412444905653> "  # ":third_place: "

            if not isBelowPodium and rank > 3:
                isBelowPodium = True

            # If we don't escape the dot ("\\.") discord might see the rank as markdown text
            # And weird behavior could happen as a result
            scoreboard.append(f"**{rankdisplay} {team} - {score}**")
            last_score = score
        scoreboard.append("\n INDIVIDUAL RESULTS:")

    rank = 0
    last_score = None

    # Display individual player scores
    for iscore, entry in enumerate(allscores):
        player = entry["player"]
        score = int(entry["score"])
        if score != last_score:
            rank = iscore + 1

        rankdisplay = str(rank) + "\\."

        if display_podium:
            if rank == 1:
                rankdisplay = "<:1st:1201576405339754546> "  # emoji=":trophy: "
            elif rank == 2:
                rankdisplay = "<:2nd:1201576409638903858> "  # ":second_place: "
            elif rank == 3:
                rankdisplay = "<:3rd:1201576412444905653> "  # ":third_place: "

        if team_names:
            rankdisplay = f"{entry['team']}: "
            if "emote" in entry:
                rankdisplay = f"{entry['emote']} "

        qualdisplay = ""
        if "is_qualified" in entry and entry["is_qualified"] == 1:
            qualdisplay = f"{qualemoji} "

        if display_podium:
            if not team_names and not isBelowPodium and rank > 3:
                scoreboard.append("======================")
                isBelowPodium = True

        # If we don't escape the dot ("\\.") discord might see the rank as markdown text
        # And weird behavior could happen as a result
        scoreboard.append(f"{rankdisplay}{qualdisplay} **{player}** - {score}")
        last_score = score

    return scoreboard


def format_scoreboard_for_discord_embed(
    lines: list[str], max_num_lines: int = 100, max_field_length: int = 1024
) -> list[str]:
    """
    Given a list of lines, split them into blocks that fit into Discord embed fields.
    max_num_lines:  optional max number of lines to display on the scoreboard
    max_field_length: Each field has a max length of 1024 in discord (keep as is unless discord changes it)
    """

    curstr = ""
    formatted_fields = []
    linecount: int = 0
    display_podium = get_settings().scoreboard_display_podium
    maxlines: int = max_num_lines
    if display_podium:
        maxlines += 1  # Accounts for added line of "=" in formatting of podium

    for line in lines:
        if len(curstr) + len(line) + 1 > max_field_length or linecount >= maxlines:
            formatted_fields.append(curstr)
            curstr = ""
            linecount = 0
            maxlines = max_num_lines

        curstr += line + "\n"
        linecount += 1

    # Don’t forget the last block
    if curstr:
        formatted_fields.append(curstr)

    return formatted_fields


def format_events_schedule(events):
    """
    Given a list of events for a week, create list of strings for each event to put in an embed.
    """
    # Extract start and end utc dates, make sure to lavel datetime object timezone needed for
    # formatting to discord timestamp
    utc_start = [e["utc_start"].replace(tzinfo=timezone.utc) for e in events if "utc_start" in e]
    utc_end = [e["utc_end"].replace(tzinfo=timezone.utc) for e in events if "utc_end" in e]

    events_start_discord_timestamps = [format_discord_timestamp(s) for s in utc_start]
    events_end_discord_timestamps = [format_discord_timestamp(e) for e in utc_end]

    events_names = [e["event"] for e in events if "event" in e]

    formatted_fields = []
    curstr = ""
    for event, start, end in zip(events_names, events_start_discord_timestamps, events_end_discord_timestamps):
        curstr += event + ": " + start + " \n "
    formatted_fields.append(curstr)
    return formatted_fields
