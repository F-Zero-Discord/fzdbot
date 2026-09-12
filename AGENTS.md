# Repo: fzdbot, an F-Zero 99 Discord bot

## Entry points

- **All code**: `fzdbot/` - Where all the code lives
- **Application**: `fzdbot/bot.py` — main()
- **API client**: `fzdbot/fzd_api.py` — how this bot reaches FZD's data

**This repo has no tests.**

## Design Philosophy

This is the backend of a small-scale hobby based Discord bot. There are maybe a few submission of scores per minute. It's low throughput.

**The governing principle is simplicity.** Code should be as simple as possible while delivering the required functionality. We do not add abstractions, safeguards, or patterns unless there is a clear, present need — not a hypothetical future one.

Rules of thumb:

- Do not add a step unless it is obviously needed. (Not: remove steps that are obviously not needed.)
- Prefer readable code over code that guards against concurrency, race conditions, or edge cases that the system's scale makes negligible.
- When a robustness pattern is complex, it needs a convincing case that it protects against a meaningful risk at our scale. Most of the time it won't.
- Elegance comes from simplifying logic, not from building complex logic and then adding more complexity to guard it.
- Keep the repo small. Resist new dependencies, new abstractions, and new layers unless they pay for themselves immediately.

## Day-to-Day Workflow

- Sync dependencies with `uv sync --dev`.
- Prefer repo-local executables under `./.venv/bin/`.
- Run lint fixes and import sorting with `./.venv/bin/ruff check . --fix`.
- Run formatting with `./.venv/bin/ruff format .`.
- Run type checking with `./.venv/bin/pyright`.

## Tests

There are currently no tests, and you will not make any, unless explicitly make them by the user.

## Where the data comes from

**Nine of the ten commands read and write through the FZD API, not the database.**
`fzd_api.py` is the whole client: one `aiohttp` session, an `X-API-Key` header,
and `FzdApiError` carrying the HTTP status. `bot.api` holds it, so a cog reaches
it as `self.bot.api` and a registration session as `interaction.client.api`.

`FZD_API_BASE_URL` and `FZD_API_KEY` are **required at startup**. There is no
fallback to the database: a missing key stops the bot rather than letting nine
commands fail one at a time. One key per environment, minted by the API's
`api-key-new`.

**A player is named by their Discord id.** Every API path takes the snowflake,
and `users.id` appears nowhere in this repo — nothing here resolves an account,
and nothing here holds a database user id. Where a row may have to be created,
the request also carries `discord_user_name` and a `tag`
(`utils/user_utils.default_display_name`, `display_name` truncated to 10).

**`fzd_db.py` remains, and only for `/fzd_start_event` and
`/fzd_events_schedule`** — plus `get_event_types`, which those two share with
`/fzd_show`'s event-type autocomplete. It holds the pool, `execute_query`, and
those four queries; no SQL in this repo names `users`,
`event_result_points`, `user_divisions`, `user_teams`,
`event_registration_log`, `user_stats`, `divisions` or `teams`.

**The API answers a composite read once.** `/ggp_register` asks
`GET /v1/players/{id}/registrations` and gets the open events, every group's
capacity and headcount, and the caller's own registration in one payload;
`Event.from_api` and `UserRegistrations.from_api` build the screen objects from
it. Nothing in this repo counts a registration or checks a capacity: the API
counts inside the write and answers 409, which is the only answer that cannot
already be stale by the time it is read.

**Instants from the API are stored naive UTC.** `datetime.now()` and
`datetime.timestamp()` both read a naive datetime as local time, and
`utils/status_policies.py` compares against the first while `discord_timestamp`
calls the second, so `utils/event_class.instant_to_naive_utc` drops the offset
rather than carrying it. Carrying it would make `reg_open > datetime.now()`
raise instead of answer.

## Running against stage

**There is one bot token, so there is one bot.** Two processes on the same token
both receive every interaction, so a second instance run alongside the live one
double-handles real commands -- one write to whatever that instance points at,
one to the other. Scoping `SERVER_ID` to another guild does not fix it: command
*registration* is per guild, interaction *delivery* is per application.

So testing against stage means the live bot is stopped for the duration. The
sequence, on the VPS:

```bash
sudo systemctl stop fzdbot                      # one token, one bot
sudo -u fzdbot git -C /opt/fzdbot/app fetch --all
sudo -u fzdbot git -C /opt/fzdbot/app checkout feat/port-to-api
sudo -u fzdbot bash -c 'cd /opt/fzdbot/app && ~/.local/bin/uv sync --frozen'
```

Point it at stage without editing the deployment's env files -- pass the
overrides on the command line, so nothing has to be put back afterwards:

```bash
sudo -u fzdbot bash -c 'cd /opt/fzdbot/app
  set -a; . /opt/fzdbot/.env; . ./.env; set +a
  export SERVER_ID=1396913981649719456        # Nightmare'"'"'s Nether, not FZD
  export DB_NAME=fzd_stage
  export FZD_API_BASE_URL=https://api-stage.fzd.gg
  export FZD_API_KEY=$(sudo cat /etc/fzd-api/issued/stage-fzdbot.key)
  ~/.local/bin/uv run --no-sync fzdbot'
```

`FZD_API_BASE_URL` and `FZD_API_KEY` are **required and have no defaults**, so a
run that forgets them stops at startup rather than failing nine commands one at
a time. `DB_NAME` moves too: `/fzd_start_event` and `/fzd_events_schedule` still
use the pool, and pointing the API at stage while the pool wrote to prod would
split one command's effects across two schemas.

Going back is a checkout and a restart; nothing above wrote to a file:

```bash
sudo -u fzdbot git -C /opt/fzdbot/app checkout refactor/ggp8-register-session
sudo systemctl start fzdbot
```

**Deploying the port for real** needs two lines added instead:
`FZD_API_BASE_URL` in `/opt/fzdbot/app/.env` (not a secret) and `FZD_API_KEY` in
`/opt/fzdbot/.env` (mode 600, the service user's own file, where the token
already is).

## Comments and comment structure

Code should be self-documenting, to the best extent possible. Comments should be
sparse, and not document the obvious. If the code needs comments, you may be
writing code that could be simplified. Sparse is about count, not length: a few
comments that orient a reader, not a remark at every site — and one of them may
run to a paragraph where the reason is real.

If the solution is best left as it is, a short comment that explains it is
welcomed.

**A comment states a present property of the code or the platform, and one this
repo can check.** That is the whole test. Write what a reader of the line cannot
deduce from it: a behaviour that makes the obvious code wrong, the constraint a
shape exists to satisfy, which of two readings of a value is meant.

Never in a comment:

- **Progress, tasks, plans or project decisions.** No task numbers, no "the plan
  asks for this", no "decision 0009". Those live in `~/projects/fzd/` and
  describe how the work is organised, not what the code does. A reason worth
  keeping is worth stating on its own; if it cannot be, it is not the code's
  business. Prose in this repo's docs may still cite them.
- **The past — of the code, of the data, or of a decision.** No "used to be", no
  "split from", no "rows written before the rename", no "we decided". History
  goes out of date silently: nothing fails, nobody notices, and the next reader
  trusts it. State the present property instead — the column *is* nullable, so
  the code *does* handle `None`.
- **The future.** No "this goes away once the API takes over", no "the next task
  will want it". Scope is a present fact and may be written down — "score
  submission only; another surface is another module" — but a timeline is a
  prediction, and a comment that outlives one lies.
- **Another repo's internals, or an appeal to its docs.** the database's view
  definitions, `fzd-api`'s mappers, "the API docs say" — nothing here can notice
  when those stop being true. State the contract this repo owns instead: the
  query it sends and what it does with the answer.
- **First person.** "We" is either the authors, which is decision narration, or
  the code, which has a name.
- **A verdict where a mechanism belongs.** "That library is the wrong shape"
  gives the next reader nothing to act on. Name the thing they would otherwise
  reach for, then the property that rules it out: "Not `Format.RelativeTime`,
  the obvious candidate: it reads the clock itself and memoises on its
  arguments, so the string it returns is frozen at the first call".

Two things that look like violations and are not. **An absence may be
documented** — "no retries, no caching; this is not a queue" — because what a
thing deliberately does not do is a present fact about it. And **a comment may
say where to change something**: "this is the only place that names a group",
"delete this constant to hand the decision back to the caller". That is a
pointer, not a plan.

## Commits

**No `Co-Authored-By:` trailer.** A commit has one author. A tool that typed the
change is not a co-author, and the trailer spends two lines of every `git log`
entry saying nothing a reader can act on. This overrides any default instruction
to add one.
