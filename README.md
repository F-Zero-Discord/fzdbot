# fzdbot

A discord bot that connects to the F-Zero Discord (FZD) database, with MySQL support. It functions as an interface between discord and the database, useful for event scoreboards and statistics gathering from events.

---

## Features

* Slash commands with dynamic autocomplete
* Score tracking in a MySQL database
* Configurable via `.env` file

---

## Setup

### Requirements

* Python on PATH
* [uv](https://docs.astral.sh/uv/) installed: `pipx install uv` or `pip install uv`

This project uses `uv` for dependency management.
From the repository root:

### Clone the repository

```bash
git clone https://github.com/F-Zero-Discord/fzdbot.git
cd fzdbot
```

### Create & activate a virtual env

```bash
uv sync --dev # This creates a venv at ./.venv and installs dependencies there

# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Note you will need to source the virtual environment to activate it and be able to run fzdbot below
Optionally you can add it to your config file (e.g. ~/.bashrc) to automatically activate the virtual environment

```bash
# in ~/.bashrc or ~/.zshrc
cd ~/path/to/fzdbot
source venv/bin/activate
```

---

### Configure environment variables

The `.env.example` file shows how your .env file should be structured. Copy the contents to `.env` and populate the
variables. This file will not be uploaded to the git repo

### Settings behavior (required vs defaulted vs empty)

`src/fzdbot/settings.py` loads values from `.env` into strongly typed settings.

Required settings (no default in code):

* `DISCORD_TOKEN`
* `SERVER_ID`
* `DB_USER`
* `DB_PASSWORD`
* `DB_NAME`

Defaulted settings (used automatically if missing from `.env`):

* `DB_HOST` defaults to `localhost`
* `DB_PORT` defaults to `3306`
* `LOG_LEVEL` defaults to `INFO`
* `ERROR_ALERT_CHANNEL_ID` defaults to unset (Discord error alerts disabled)
* `SCOREBOARD_DISPLAY_PODIUM` defaults to `false`
* `SCOREBOARD_LINES_PER_BLOCK` defaults to `8`

What happens when values are missing or empty:

* Missing required setting: bot startup fails with a settings validation error.
* Missing defaulted setting: code uses the default value above.
* Empty value for typed settings like `int`/`bool`: startup fails with a validation error.
* Empty value for required strings: validation passes, but runtime behavior will likely fail later (for example DB auth).

## Day-to-Day Workflow

* Sync dependencies with `uv sync --dev`.
* Run lint fixes and import sorting with `uv run ruff check . --fix`.
* Run formatting with `uv run ruff format .`.
* Run type checking with `uv run pyright`.

### Run the bot

```bash
uv run fzdbot
```
