# fzdbot

The F-Zero Discord (FZD) community's Discord bot: event registration, result submission and live scoreboards. It reads and writes FZD's data through the FZD API.

---

## Features

* `/register` for events, with divisions, teams and a waitlist
* `/submit` and `/submit_inline` for results, scored by points or by time
* Live scoreboards that keep themselves up to date during an event
* Slash commands with autocomplete, configured through a `.env` file

## Documentation

| Page | What's in it |
|---|---|
| [docs/setup.md](docs/setup.md) | Installing, every setting, running locally and against stage |
| [docs/structure.md](docs/structure.md) | What lives where |
| [docs/internals.md](docs/internals.md) | Startup, commands, the API client, errors, background loops |
| [docs/commands.md](docs/commands.md) | Every command: handler, arguments, who may run it, what it reads and writes |
| [docs/how-to.md](docs/how-to.md) | Adding a command, regenerating the API types, removing a command |
| [AGENTS.md](AGENTS.md) | The design rules, and the details of running against stage |

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

Every setting, its default, and what happens when one is missing are in [docs/setup.md](docs/setup.md#configure).

## Day-to-Day Workflow

* Sync dependencies with `uv sync --dev`.
* Run lint fixes and import sorting with `uv run ruff check . --fix`.
* Run formatting with `uv run ruff format .`.
* Run type checking with `uv run pyright`.

### Run the bot

```bash
uv run fzdbot
```

`uv run fzdbot --env NAME` reads `.env.NAME` instead of `.env`:

```bash
uv run fzdbot --env stage             # against api-stage.fzd.gg
uv run fzdbot --env stage-local-api   # against a local fzd-api over the stage database
```

Never run a second instance on the live bot's token. What goes in those files, and how to start the local
API, is in `AGENTS.md` under "Running locally, against stage or a local API".
