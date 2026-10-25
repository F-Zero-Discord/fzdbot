# Internals

How fzdbot works inside. The design rules behind it ("simple to understand beats
robust", what a comment may say, why the bot keeps no copy of the API's rules) are
in [AGENTS.md](../AGENTS.md); this page describes the machinery.

## Startup

`main.py`:

1. Parses `--env NAME` and reads the settings. A missing required setting stops
   here.
2. Asks Discord for the message-content, guilds and messages intents, and for the
   members intent only when a role map is set. Asking for a privileged intent the
   portal hasn't granted stops the bot.
3. `FZDBot.setup_hook` creates the API client and the MySQL pool, loads the eight
   cogs in order, and syncs the command tree to `SERVER_ID`. A failure while
   loading is logged, reported to the alert channel, and stops the bot. A failure
   while syncing is reported, and the bot carries on.

## Commands

Every command is a discord.py application command on a cog:

- `@app_commands.command(name=…, description=…)` defines it, and
  `@app_commands.describe(…)` gives its arguments their help text.
- Autocomplete is bound in the cog's `cog_load`:
  `self.<method>.autocomplete("<argument>")(handler)`. Each handler reads the API
  as the user types; nothing is cached.
- A choice carries ids, not names. A slot choice is `"<event id>:<slot id>"`
  (`SLOT_VALUE` in `cogs/submissions.py`). A command checks the value has that
  shape and refuses typed text: "Pick a slot from the list."
- Every cog is added with `guild=SERVER_ID`, so commands exist in one server only.

**There are no permission checks in the code.** Who may run each command is set
in Discord, under Server Settings → Integrations, by role. The API accepts the
bot's key as staff, so nothing downstream checks either.

## Talking to the API

`fzd_api.py` is the whole client:

- One `aiohttp` session, created on first use, with a 10-second timeout.
- Every request sends `X-API-Key`. Methods take the player's Discord id, never a
  database id.
- Every response is typed with a `TypedDict` from `api_types.py`, which is
  generated from the API's OpenAPI document (see [How-to](how-to.md)). A type is a
  claim pyright checks, not a parser: nothing validates a response at runtime.
- Write routes take `?now=` as an ISO 8601 UTC instant, because the API reads its
  clock from the request.
- Any status of 400 or more raises `FzdApiError`, with `status` and the API's
  `detail`. A timeout or a network failure raises it with no status.

`FzdApiError.refusal()` is what a command shows the user. For a 404, 409 or 422 it's
the API's own sentence about the rule that refused the request, word for word. For
anything else, it's the client's own description, such as "The FZD API did not
answer in time. Nothing was changed."

## Errors and alerts

- A command catches `FzdApiError` and shows `refusal()`, privately.
- Anything else reaches `FZDBot.on_app_command_error`, which logs the traceback,
  calls `send_error_alert`, and tells the user "ERROR! Something went wrong,
  contact FZD staff for help!"
- `error_alerts.py` posts to `ERROR_ALERT_CHANNEL_ID`: where it happened, the error,
  the command, the user and the channel, truncated to fit one Discord message. The
  traceback stays in the log.
- Logging goes to stdout at `LOG_LEVEL`, as `time name level message`.

## State

The bot keeps nothing between interactions. Each command reads the API afresh.
There are three exceptions, and none of them is data:

- `get_settings()` reads the settings once.
- A `/register` session (`RegSession`) lives as long as one of its screens is on
  screen. It holds the newest interaction token from each click, which lets a
  session outlast the 15 minutes Discord gives the original command.
- The live-board loop remembers what it last drew on each board and which boards
  are failing, so an unchanged board costs no edit and a stuck one alerts once.

## Background loops

**Live boards** (`cogs/show_scoreboard.py`):

- Every `SCOREBOARD_REFRESH_SECONDS`, the loop reads the board registry
  (`GET /v1/scoreboards`). It renders each board whose event has started, and edits
  the message only where the render changed.
- At the event's end, it draws the board once more as final and deletes its
  registry row.
- With nothing under way, it sleeps until the next event starts, for an hour at
  most. `/setup_scoreboard` restarts it.
- The registry lives in the API, so a restart resumes every board on the next
  tick.

**Event roles** (`cogs/event_roles.py`):

- Every `EVENT_ROLE_SYNC_SECONDS`, the sync gives each configured event's (and
  division's) role to its registered players with a place, and takes it from
  anyone else.
- An event with no registrations at all is skipped, so a failed read can't strip
  every role.
- Members with a role above the bot's are refused one at a time. The pass carries
  on, and logs them.

## The direct database path

`fzd_db.py` holds the MySQL pool and the queries behind `/fzd_events_schedule`
(`vw_list_scheduled_events`) and `/fzd_start_event` (an overlap check, and an
insert into `events_scheduled`). Everything else goes through the API.
