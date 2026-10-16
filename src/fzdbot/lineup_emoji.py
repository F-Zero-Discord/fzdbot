"""The custom emoji a prix slot is drawn with. Looked up by name in the guild
the command runs in, never by id: each guild the bot serves carries its own
copy of the emoji under the same name, with an id of its own, so the stage
bot's guild and FZD's render the same markup from one table.
"""

import discord

from fzdbot.api_types import SlotResponse

# `lineup_short_name` as the API spells it, to the guild emoji's name. A
# lineup not listed, a 99 race, has no emoji. This is the only place that
# names one.
EMOJI_NAMES: dict[str, str] = {
    "Knight": "GPKnight",
    "mKnight": "GPMirrorKnight",
    "Queen": "GPQueen",
    "mQueen": "GPMirrorQueen",
    "King": "GPKing",
    "mKing": "GPMirrorKing",
    "Ace": "GPAce",
    "mAce": "GPMirrorAce",
    "???": "GPSecretKnight",
    "MP": "MPMini",
    "cMP": "MPClassicMini",
}


def lineup_emoji(guild: discord.Guild, slot: SlotResponse) -> discord.Emoji | None:
    """The guild's emoji for the slot's lineup; `None` for a lineup with none,
    and for a guild that carries no copy under that name, which draws the slot
    without an icon rather than refusing the form.
    """
    name = EMOJI_NAMES.get(slot["lineup_short_name"])
    if name is None:
        return None
    return discord.utils.get(guild.emojis, name=name)
