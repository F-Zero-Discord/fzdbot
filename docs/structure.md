# Structure

```text
src/fzdbot/
  main.py                    entry point: settings, intents, startup, error handling
  settings.py                every setting, typed (pydantic-settings)
  fzd_api.py                 the FZD API client: one method per route the bot calls
  api_types.py               the API's request and response shapes, generated
  fzd_db.py                  the MySQL pool and the queries of the two direct-database commands
  error_alerts.py            error reports to the alert channel
  scoreboards.py             a scoreboard as Discord markdown, from the API's reads
  formatters.py              Discord timestamps and the events schedule embed
  lineup_emoji.py            a prix slot's emoji, looked up by name in the server
  event_roles.py             who should hold an event's or division's role
  cogs/                      one module per group of commands
    event_register.py          /register, and the session behind its screens
    ggp_submissions.py         /submit, /submit_inline, /show_submissions
    submissions.py             /delete_submission, plus the slot and machine pickers,
                               parsers and messages the result commands share
    show_scoreboard.py         /fzd_show, /setup_scoreboard, /stop_board, the live-board loop
    votes.py                   /set_vote
    events_users_handling.py   /fzd_set_name, /fzd_events_schedule, /fzd_start_event
    event_roles.py             the event-role sync loop (no commands)
    ggp8_rivals.py             GGP8's rival commands
  views/                     the /register screens (Discord components)
    common.py                  the base view and button every screen uses
    register_views.py          rules, event menu, division or team, confirm, withdraw, exit
    stats_menu_views.py        the skill questions
  utils/
    event_class.py             events, divisions and teams, built from API payloads
    status_policies.py         the label and button each event gets on the /register menu
    view_utils.py              the steps of the /register flow, and timestamps
    user_utils.py              the default display name for a new player
scripts/
  api_types.py               regenerates src/fzdbot/api_types.py
tests/                       renderer, parsers, client requests, role sync, live-board tick
docs/                        this documentation
```

`commands.md` lists every command with its handler and line.
