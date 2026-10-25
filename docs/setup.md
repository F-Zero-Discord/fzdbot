# Setup

How to get fzdbot running on your own machine.

## What you need

- **Python 3.12 or later**, and [uv](https://docs.astral.sh/uv/).
- **A Discord application of your own**, invited to a test server with the
  `applications.commands` scope and the message-content intent turned on. Never run
  locally with the live bot's token: two processes on one token both answer every
  command (see [Running against stage](#running-against-stage)).
- **An FZD API to talk to**: the stage API, or a copy of `fzd-api` running locally.
  You need a key for it; keys are minted by the API's maintainers.
- **MySQL credentials** for the same environment's database. `/fzd_events_schedule`
  and `/fzd_start_event` still read and write it directly, and the bot opens its
  connection pool at startup.

## Install

```bash
uv sync --dev
```

This creates `.venv/` and installs everything, including the development tools.

## Configure

Settings come from `.env` in the repository root, or from `.env.NAME` with
`uv run fzdbot --env NAME`. The named file is read **instead of** `.env`, not on top
of it. Start from `.env.example`. Every `.env*` file except `.env.example` is
ignored by git.

`src/fzdbot/settings.py` is the source of truth for what follows.

| Setting | Required | Default | What it does |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | | The bot's token, from your application's Bot page. |
| `SERVER_ID` | yes | | The server the commands are registered to. |
| `FZD_API_BASE_URL` | yes | | Where the FZD API is. |
| `FZD_API_KEY` | yes | | This bot's key for that API. One per environment. |
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | yes | | The database account and schema for the two direct-database commands. Point `DB_NAME` at the same environment as the API. |
| `DB_HOST` | no | `localhost` | |
| `DB_PORT` | no | `3306` | |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` or `NOTSET`. |
| `ERROR_ALERT_CHANNEL_ID` | no | FZD's alert channel | Where unexpected errors are reported. Set it to a channel in your test server; **empty turns alerts off**. |
| `RULES_CHANNEL_ID`, `HELP_CHANNEL_ID` | no | FZD's channels | The channels `/register` points players to. Empty shows a plain name instead of a link. |
| `GGP8_EVENT_ROLES` | no | `{}` | JSON map from a scheduled event id to a role id: the role the event-role sync gives that event's registrants. Empty turns the sync off. |
| `GGP8_DIVISION_ROLES` | no | `{}` | The same, from a division id to a role id, on top of the event's role. |
| `EVENT_ROLE_SYNC_SECONDS` | no | `300` | How often the event-role sync runs. |
| `SCOREBOARD_DISPLAY_PODIUM` | no | `true` | Medals for the top three on scoreboards. |
| `SCOREBOARD_LINES_PER_BLOCK` | no | `8` | Lines per block in a scoreboard embed. |
| `SCOREBOARD_REFRESH_SECONDS` | no | `10` | How often live boards are re-read and, if their standings changed, edited. |
| `DEBUG_SCOREBOARD` | no | `false` | Adds each player's submission count and per-slot results to their line. |
| `SUBMISSION_CONFIRMATION_EPHEMERAL` | no | `false` | `true` shows the confirmation of a submitted, changed or removed result only to the player; `false` posts it in the channel. |

What happens when a value is missing or empty:

- A required setting that's missing stops the bot at startup with a validation
  error.
- An empty value for a number or true/false setting also stops it at startup.
- An empty required text value passes, and fails later instead (for example, at
  database login).
- Setting either role map asks Discord for the **members intent**, which is
  privileged. Turn it on in your application's developer portal, or the bot won't
  start. The bot's own role must also sit above the roles it hands out.

## Run

```bash
uv run fzdbot                 # reads .env
uv run fzdbot --env stage     # reads .env.stage
```

On startup the bot loads its cogs and syncs its commands to `SERVER_ID`, replacing
whatever your application had registered there. The log line `Synced N commands to
guild …` confirms it.

`/register` and `/submit` draw each prix slot with its emoji, looked up by name in
the server the command runs in. A test server without copies of FZD's prix emoji
shows the slots without icons.

## Running against stage

Use `.env.stage`, with your test application's token and server, the stage
database settings, and the stage API's URL and key:

```bash
uv run fzdbot --env stage
```

The stage API serves whatever was last published to it. If it doesn't have the API
change you need, run `fzd-api` locally over the stage database and point a second
file, `.env.stage-local-api`, at `http://127.0.0.1:8000`. [AGENTS.md](../AGENTS.md)
("Running locally, against stage or a local API") has every line of both files.

Live boards are registered with the API, not the bot, so a second bot on the same
environment sees the first one's boards and can't edit them. Take turns.

## Before you open a pull request

```bash
uv run ruff check . --fix && uv run ruff format . && uv run pyright && uv run pytest -q
```

The tests cover what runs without Discord or the API: the scoreboard renderer, the
result parsers, the API client's request shapes, the event-role sync, and the
live-board tick against a fake API and channel.
