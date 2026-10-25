# How-to

## Add a command

1. **Pick the cog** it belongs with, or add a module under `cogs/`. A new module
   needs a `setup(bot)` that adds the cog with `guild=discord.Object(id=SERVER_ID)`,
   and a `load_extension` line in `FZDBot.setup_hook` (`main.py`).
2. **Define it:**

   ```python
   @app_commands.command(name="my_command", description="What it does, in a few words")
   @app_commands.describe(slot="Which slot")
   async def my_command(self, interaction: discord.Interaction, slot: str) -> None:
       ...
   ```

   Describe every argument. The description is what members see in Discord, and the
   docs read it too.
3. **Offer choices** by binding an autocomplete handler in the cog's `cog_load`:
   `self.my_command.autocomplete("slot")(self.slot_autocomplete)`. Return ids as the
   values, and refuse a value that isn't one.
4. **Read and write through the API.** Add a method to `fzd_api.py` that returns a
   type from `api_types.py`. If the route is new, regenerate the types (below).
   Show a refusal with `error.refusal()`; don't add a check of your own that copies
   a rule the API enforces.
5. **Run the checks:**
   `uv run ruff check . --fix && uv run ruff format . && uv run pyright && uv run pytest -q`.
6. **Set who may use it** in Discord: Server Settings → Integrations → fzdbot. Until
   then, everyone who can see the command can run it.
7. **Document it.** In `fzd-docs`, after the change is on the branch the docs read:
   - run `make generate`;
   - add the command to `tools/commands/fzdbot.toml` with its `tier` and channels;
   - run `make repo-docs` to refresh [commands.md](commands.md);
   - update any how-to it changes.

## Regenerate `api_types.py`

When the API adds or changes a route this bot uses:

```bash
uv run scripts/api_types.py                # from a ../fzd-api checkout, on the branch you work against
uv run scripts/api_types.py --url URL      # or from a running API's /openapi.json
```

The checkout is the default because a deployed API can be behind the branch you're
working against. Never edit the file by hand; its header names the API commit it
came from.

## Run against stage

See [Setup](setup.md#running-against-stage).

## Deploy

fzdbot runs on the FZD server as a systemd service, deployed from a branch: `dev`
today. The procedure is in the private developer docs; ask the dev team for
access.

## Remove a command

Delete it from its cog, and its `fzd_api.py` method if nothing else uses it.
`make generate` in `fzd-docs` then warns that `tools/commands/fzdbot.toml` still
has an entry for it. Remove the entry, and run `make repo-docs`.
