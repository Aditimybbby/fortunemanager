# FortuneManager — Olympus help and reward checks repair

## What was wrong

1. `fortune/legacy.py` returned immediately when `LEGACY_COGS` was empty. The delivered `.env.example` set it to an empty value. Olympus source files were present but their commands and listeners did not load.
2. `fortune/community.py` replaced the original interactive help with a fixed list of new commands. `CommandNotFound` errors were ignored, making disabled commands such as `.antinuke` appear unresponsive.
3. The Docker build installed only the smaller core requirements. Simply enabling old modules could fail on missing dependencies. Several original cogs initialized their SQLite tables in unawaited tasks, so registration did not guarantee that commands were ready. The original Moderation cog also collided with the new Moderation cog name.
4. `.invites` displayed all-time verified joins, while `.check` used only verified joins after event publication and before ticket creation/event end. These totals can legitimately differ. Server-promotion deductions, even-number rounding, profile/age/onboarding rules, manual findings, and proof deadlines can further reduce or block rewards.
5. Even a no-rules event fetched member profiles and blocked rewards on a temporary Discord lookup failure. Also, timestamp filtering compared ISO strings instead of instants, and a member's server-specific profile picture was ignored.

The uploaded ZIPs do not contain the running bot's current event/tracking database. Therefore, the exact exclusion for any particular affected member cannot be established from these archives. The repaired check reports the evidence needed to diagnose it.

## What changed

- Olympus server features now load by default. `LEGACY_COGS=all`, an empty value, and an unset value all load the supported server modules. `LEGACY_COGS=none` deliberately selects the smaller core. Explicit comma-separated module names still work.
- The full help uses the Olympus category-menu approach: Home, a dropdown, page buttons, command descriptions, usage, aliases, and subcommands. It includes original server categories and the new staff, ticket, tracking, and event commands. `.h` works too.
- Antinuke loads its protection listeners and whitelist/extraowner commands together; automod loads its enforcement listeners. Loading a module does not enable protection for a new guild. Existing configured module states are preserved.
- Missing commands now produce a response. `.modulestatus` reports failed module names; detailed errors remain in startup logs. A failure in one module does not hide other working modules.
- `requirements.txt` includes the original server-module dependencies. Database initialization is awaited, original/current Moderation cogs coexist, core command names retain precedence, and old database/task resources are closed on shutdown.
- Legacy SQLite files are initialized once into `DATA_DIR/legacy`, making them writable and persistent on Railway/Docker. Existing persisted files are never replaced on restart. The existing `fortune.db` schema/data remain in place.
- `.check` counts only joins earned during the event and at/before ticket creation, as requested. It compares timestamps in UTC, accounts for server profile pictures, skips profile checks for no-rules events, and shows temporary lookup failures as **Verification pending**.
- Reports show all-time verified joins, excluded pre-event and post-cutoff joins, event/ticket timestamps, deductions, even-number rounding, and blocking findings. The report reuses its evaluation, avoiding duplicate member lookups. `.invites` shows the current-event subtotal as well.
- Reward choices and staff approval remain required; the bot does not automatically select, approve, or pay a reward. Event rules and deductions have not been loosened.

## Upgrade an existing bot

1. Stop the bot. Keep a backup of your current `.env`, your `DATA_DIR` (including `fortune.db` and any SQLite sidecar files), and your existing `db/` directory.
2. Update the program files/assets from this package. **Keep your live `.env`, `DATA_DIR`, and existing `db/*.db` files. Do not overwrite live databases with the bundled source databases.**
3. Use Python 3.12 and install the updated dependencies:

   ```sh
   python -m pip install -r requirements.txt
   ```

4. In the existing environment settings, set:

   ```dotenv
   LEGACY_COGS=all
   ```

   Preserve your current `DISCORD_TOKEN`, `DATA_DIR`, and dashboard settings. Do not replace your `.env` with the example.
5. Restart with `python main.py`. On Railway/Docker, rebuild/redeploy the image so the new dependencies are installed. Keep the mounted `/data` volume and `DATA_DIR=/data`.
6. Run `.modulestatus`, `.help`, and `.help antinuke`. The full-package offline startup registered **77 Olympus modules and 336 commands**, with no module-load failures. Different explicit module selections will have different counts.
7. Run `.antinuke` as the server owner (or a configured extra owner) to see its options. Enabling it retains the original Administrator and role-position requirements.
8. In an existing event ticket, run `.check` again. Keep the existing ticket cutoff; invites gained after that ticket opened remain excluded. If it reports a rule finding or a deduction, use that explanation to review the claim. Fix an incorrect manual finding with `.eventunflag ID reason` as an administrator. For a temporary verification failure, retry later.

On first startup, the existing project `db/*.db` files are copied using SQLite backup into `DATA_DIR/legacy`; after that, the persistent copies are authoritative. For a prior custom legacy location, set `LEGACY_DATA_DIR` to its persistent absolute directory. Keep both `fortune.db` and the legacy directory in backups.

## Scope and verification

Executed locally with Python 3.12 and Discord actions mocked:

```text
python -m pytest -q
117 passed
```

The tests cover existing staff grants, dashboard, tickets, event/reward behavior, and six additional integration/regression cases. The integration test starts all supported original modules, dispatches `.help`, `.h antinuke`, `.help Security`, `.antinuke`, `.antinuke enable`, `.antinuke disable`, and `.automod` through Discord.py's command parser/checks; checks category navigation and embed limits; verifies the original AntiBan listener; denies an unauthorized antinuke attempt; and checks that persisted database changes survive initialization.

Reward regressions cover event-only counts, offsets and exact ticket boundaries, exclusion explanations, no-rules eligibility during profile-service failure, retryable failures in rules events, server avatars, and preservation of the server-promo deduction.

No connection to your live Discord bot was made. No real messages, role changes, moderation actions, or rewards were sent. Provider-backed music/AI/game image calls and the entire collection of individual original commands have not been live-tested. Music still needs a working Lavalink/provider configuration; third-party AI integrations need their own configured services. Original no-prefix and deployment-wide owner/global-control cogs, old duplicate help/error handlers, and join advertising remain excluded; those are separate from the restored server features. Original greeting/autorole settings and dashboard settings are independent, so configure each feature through one interface to avoid duplicates.

The earlier `TEST_REPORT.md` and `UPDATE_TEST_REPORT.md` are retained as historical reports; this file describes the current build.
