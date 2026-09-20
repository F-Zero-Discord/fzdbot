# Helpers for deriving database fields from a discord user object.
import discord

TAG_MAX_LENGTH = 10  # users.tag is varchar(10)


def default_display_name(discord_user: discord.abc.User) -> str:
    """ The display name to store in users.tag when the user did not supply one.

        Member.nick is None whenever a member has not set a server nickname, and
        a plain User (a DM, or an uncached member) has no .nick at all, so
        reading .nick directly is either a TypeError or an AttributeError.
        .display_name exists on both and falls back nick -> global_name -> name,
        so it is always a non-empty string.
    """
    return discord_user.display_name[0:TAG_MAX_LENGTH]
