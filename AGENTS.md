# Repo: fzdbot, an F-Zero 99 Discord bot

## Entry points

- **Code**: `src/fzdbot/` — `main.py` is the application, `fzd_api.py` the API client,
  `cogs/` the commands.
- **Tests**: `tests/`

## Design Philosophy

This is a small-scale hobby Discord bot. There are maybe a few score submissions
per minute. It's low throughput.

Per decision 0009: **contributor experience outranks robustness.** Four
volunteers touch FZD's code, and a change that generally works and ships beats
one that always works and never lands. When a choice trades "harder to
contribute to" against "harder to break", choose the one that can be
contributed to.

**Contributor experience is measured at read time.** The contributor this
protects opens the repo cold, months after the last change, to fix one thing
before an event. Everything here is judged by what they can tell at a glance:
what a value holds, what a function takes, where a rule lives. Effort up front
on a structure that gives them that is not the robustness 0009 declines; it is
what 0009 is for. What is declined is defensive code — guards, retries,
validation — against failures this scale makes negligible. Structure makes
reading cheaper; robustness makes failing rarer. The first outranks the second.

So "simple" means simple to understand, not simple to write:

- **Knowledge goes in code, not prose.** What a value holds, what a function
  takes and what a boundary sends are declared in types and signatures, where
  pyright checks them — not narrated in a docstring or in this file, where
  nothing does. A docstring that has to explain what an argument holds is a
  missing type. This file says why; the code says what.
- **Every shape the API answers is declared in this repo.** `fzd_api.py`
  returns `TypedDict`s from `api_types.py`, one per response schema, under the
  API's own schema names and wire field names, so a name greps across both
  repos. How that file is produced is stated at its top. A `TypedDict` is a
  claim pyright checks at every read site, not a parser: no runtime
  validation, no renaming layer. A `dict[str, Any]` past `fzd_api.py` is a
  missing declaration, not simplicity.
- **A fallback is a claim about the data, and the claim has to be
  checkable from where the reader stands.** `x or y`, `.get(k)`, a default,
  a truncation: each says a case exists. Before writing one, read the column
  and prod's rows. If the case does not exist, the fix is upstream — the API
  guarantees the value and the type says `str` — and the guard is deleted,
  not typed. If it exists because the API shapes one thing two ways, that is
  upstream too. If the platform or the data really allows it, keep the guard
  and put the reason on the constant or the branch. Idiom is not the
  question: `a or b` is fine when a reader can see what `b` covers. Read a
  `TypedDict` with `[]`, not `.get`: pyright accepts `.get` with a key the
  shape does not declare and types it `Any | None`, so a `.get` chain hides
  from the checker exactly the drift the shape exists to catch.
- **Dense code is a defect, not a style.** A line a reader has to unpack —
  subscripts chained into untyped values, a search for what a field already
  holds, a comprehension doing three things — is rewritten, not commented.
  A comment is for what the code cannot say (see "Comments").
- **Let it fail where the failure is legible.** Before adding a guard, say what
  happens without it. Where that is an exception whose message names its own
  cause — Discord refusing an eleventh embed, the API answering 409 — the
  exception is the better version: it costs one afternoon, once, and only if it
  happens, while the guard costs every reader who meets it. Write the plain
  line. A guard arrives when someone has *seen* the failure, not when a limit is
  known to exist: the same evidence that retires one is what admits it. The
  exceptions are the two this section names.

When to add, and when to remove:

- **Structure has to pay for something specific.** A layer, a helper or a rule
  exists because it makes one named hard thing mechanical — knowing what a
  value holds without opening another repo is one of them — or because a
  decision record asks for it. Anything in this file or in the code that is
  not paying for one of those should be deleted, not kept out of respect.
- **An exception is the bar for the next one.** Where this repo already carries
  a guard — a retry, a lock, a fallback — it is the whole of that exemption.
  A second one is measured against the first, and it has to be at least as
  well-founded.
- **Name the price.** A pattern that costs every ordinary change something is
  fine only if the text next to it says what that is and what it buys.
- **Retire on evidence, not calendar.** Remove a guard or a branch when it is
  observed to be unused, not when it feels old — and do not keep it because it
  might be needed someday.

Two things are outside this decision, and only two: unrecoverable loss of data,
and reliability during a major event such as a GGP. Everything else is
"breaks on Friday, fixed on Saturday". Where simplicity and correctness pull
apart, say so in the change rather than quietly optimising for robustness out
of habit.

## Before finishing

`uv run ruff check . --fix && uv run ruff format . && uv run pyright && uv run pytest -q`.
Ruff runs repo-wide; keep any unrelated fixes it makes.

## Tests

`tests/` covers what runs without Discord or the API: the renderer, the parsers,
the client's request shapes, and the live-board tick against a fake API and a
fake channel. Add a test only where a task asks for one; nothing here spins up
a bot.

## Where the data comes from

**Every command but two reads and writes through the FZD API, not the
database.** `fzd_api.py` is the whole client: one `aiohttp` session, an
`X-API-Key` header, and `FzdApiError` carrying the HTTP status. `bot.api` holds
it, so a cog reaches it as `self.bot.api` and a registration session as
`interaction.client.api`. What a given command reads and writes is its cog and
the client's signatures; only what no single file can state is below.

`FZD_API_BASE_URL` and `FZD_API_KEY` are **required at startup**. There is no
fallback to the database: a missing key stops the bot rather than letting every
command fail one at a time. One key per environment, minted by the API's
`api-key-new`.

**The API owns every rule, and this repo keeps no copy of one.** Which events
are open, whether a slot takes a score or a time, the bounds, whether a machine
must be named, who may be named a rival, whether a registration still fits —
each is decided inside the write, and the refusal is the sentence the user
reads. Nothing here counts a registration, checks a capacity or ranks a result:
a count read before a write is already stale by the time it is read, and the
409 is not. A check added here is a second copy of a rule, and the copy is the
one that goes out of date.

**A cache is something to report, not to build on.** Commands read the API per
interaction and the bot holds no state between them. If you find a cache in this
repo or on a branch being harvested — a module or class-level dict of options
or event config, a TTL, a list loaded once in `cog_load` — tell the user where
it is and what reads it, and ask whether it should be removed. Do not extend
it, tune it or validate input against it, and do not add one. Two things are
not that: `get_settings`'s `lru_cache` in `settings.py` is configuration read
once, not data; and the live-board loop's `_rendered` and `_failing` in
`cogs/show_scoreboard.py` remember only what the last tick edited and what
failed, so an unchanged board costs no edit and a stuck one alerts once. Both
are rebuilt from the API on the next tick and nothing reads them but the loop.

**A player is named by their Discord id.** Every API path takes the snowflake,
and `users.id` appears nowhere in this repo — nothing here resolves an account,
and nothing here holds a database user id. Where a row may have to be created,
the request also carries `discord_user_name` and a `tag`
(`utils/user_utils.default_display_name`, `display_name` truncated to 10).

**`fzd_db.py` remains, and only for `/fzd_start_event` and
`/fzd_events_schedule`**, both in `cogs/events_users_handling.py`. It holds the
pool, `execute_query`, and their three queries. No SQL in this repo names
`users`, `event_result_points`, `user_divisions`, `user_teams`,
`event_registration_log`, `user_stats`, `divisions` or `teams`.

**The event role sync needs two grants that cannot be made in this repo.** The
**members intent**, privileged and enabled in the application's developer
portal: `main.py` asks for it only where `GGP8_EVENT_ROLES` or
`GGP8_DIVISION_ROLES` is not empty,
because asking for an intent the portal has not granted stops the bot at
startup, so the map and the portal are configured together or the deployment
comes up dead. And **fzdbot's own role above the roles it hands out**, without
which every add is refused — a member holding a role above the bot's is refused
individually, staff who register are exactly that member, and the pass carries
on.

## Running locally, against stage or a local API

**There is one bot token per Discord application, so one bot per application.**
Two processes on the same token both receive every interaction, so a second
instance run alongside the live one double-handles real commands -- one write to
whatever that instance points at, one to the other. Scoping `SERVER_ID` to
another guild does not fix it: command *registration* is per guild, interaction
*delivery* is per application.

So a local run uses **a Discord application of its own**, never the token in the
deployment's `.env`: any application you own, invited to a test guild with the
`applications.commands` scope and the message-content and members intents on.
Starting fzdbot syncs its command tree to `SERVER_ID`, replacing whatever that
application had registered there.

**The live-board registry belongs to the API, not to a bot.**
`GET /v1/scoreboards` answers every board registered against that environment,
so a second bot pointed at stage reads the first one's rows and gets a 403 on
each: a message is editable only by the application that sent it. That is one
warning per board per tick and one error alert, and nothing else — the row
stays, and the bot that posted the message goes on drawing it.
`DELETE /v1/scoreboards/{message_id}` clears a row whose message is nobody's
board any more. Two bots each holding a live board on one environment is not
arrangeable; take turns.

**`fzdbot --env NAME` reads `.env.NAME` in place of `.env`**, not on top of it:
a setting the named file leaves out fails startup rather than being taken from
another file, and a name with no file is refused. Without the flag the bot reads
`.env`, which is what the deployment has. Every `.env*` but `.env.example` is
ignored by git. The names in use:

| File | Run |
|---|---|
| `.env.stage` | this bot, locally, against `api-stage.fzd.gg` |
| `.env.stage-local-api` | this bot, locally, against a local `fzd-api` over the stage database |
| `.env.prod` | the live bot's settings. **On a development machine keep them here, not in `.env`**, so a bare `fzdbot` stops at startup instead of logging in as the live bot |

`.env.stage`, mode 600:

```bash
DISCORD_TOKEN=...                 # the test application's, not the live bot's
SERVER_ID=...                     # the test guild
ERROR_ALERT_CHANNEL_ID=...        # a channel in the test guild; empty disables alerts,
                                  # absent falls back to FZD's own channel
DB_HOST=...                       # the stage database server
DB_PORT=3306
DB_USER=...
DB_PASSWORD=...
DB_NAME=fzd_stage
FZD_API_BASE_URL=https://api-stage.fzd.gg
FZD_API_KEY=...                   # ssh fzd 'sudo cat /etc/fzd-api/issued/stage-fzdbot.key'
GGP8_EVENT_ROLES={"738": ...}     # stage's event ids to roles made in the test guild;
                                  # omit it to leave the role sync off
EVENT_ROLE_SYNC_SECONDS=60        # 300 in the deployment; 60 while watching it work
```

A run with either role map set asks for the members intent, so the *test*
application needs it granted in its portal or the bot stops at startup, and the
test bot's own role has to sit above the roles the map names or every add is
refused with a 403.

`FZD_API_BASE_URL` and `FZD_API_KEY` are **required and have no defaults**, so a
run that forgets them stops at startup rather than failing the API commands one at a
time. `DB_NAME` moves with them: `/fzd_start_event` and `/fzd_events_schedule`
still use the pool, and pointing the API at stage while the pool wrote elsewhere
would split one command's effects across two schemas.

### Against api-stage

```bash
uv run fzdbot --env stage
```

Stage serves whatever branch was last published to it (`./scripts/publish stage
<branch>` in `fzd-api`, a `git pull` on the VPS), so it cannot serve uncommitted
API work. A 404 on a route this client calls means stage is behind; check with
`curl -H "X-API-Key: $FZD_API_KEY" $FZD_API_BASE_URL/v1/events/active`.

### Against a local fzd-api

For API work that stage does not serve yet, run the `../fzd-api` checkout on
`:8000` over the stage database and point the bot at it. `.env.stage-local-api`
is `.env.stage` with `FZD_API_BASE_URL=http://127.0.0.1:8000` and a key minted
for the local API. Mint it once, in
`fzd-api`: `uv run api-key-new fzdbot-local` prints the key and the
`FZD_API_CLIENT_KEYS` JSON entry for it; keep both (`~/.config/fzd/local-api-key.txt`).

```bash
uv run serve --env stage-db            # terminal 1, in ../fzd-api
uv run fzdbot --env stage-local-api    # terminal 2, in this repo
```

`.env.stage-db` is `fzd-api`'s file and is described in its `AGENTS.md` under
Environments: its `.env` with the database settings of `.env.stage` here,
`FZD_API_DB_SSL=off`, port 8000, stage's GGP8 and rival event ids
(`[735,736,737,738,739]` and `[736,737,738,739]`) and the minted key's
`FZD_API_CLIENT_KEYS` entry.

`curl localhost:8000/health/database` must name `fzd_stage` before the bot
starts. A bot key passes every staff gate, so stage data can be seeded through
the API itself -- `PUT /v1/events/{id}/schedule`, and result `PUT`s with `?now=`
inside the event's window -- rather than by SQL.

### On the VPS, with the live token

Only when the live application itself has to be the one tested. The live bot is
stopped for the duration:

```bash
sudo systemctl stop fzdbot                      # one token, one bot
sudo -u fzdbot git -C /opt/fzdbot/app fetch --all
sudo -u fzdbot git -C /opt/fzdbot/app checkout <branch under test>
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

Going back is a checkout and a restart; nothing above wrote to a file:

```bash
sudo -u fzdbot git -C /opt/fzdbot/app checkout <the deployed branch>
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
- **What a signature could say.** A docstring that lists what the arguments
  hold, or which keys a dict carries, is a missing type. Declare the shape and
  keep the docstring for the rule the function applies, if there is one.
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
