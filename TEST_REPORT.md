# Verification report

## Result

- **50 backend tests passed.**
- Frontend DOM smoke suite passed with no JavaScript runtime errors.
- All Python source files compiled successfully.
- Frontend JavaScript passed `node --check`.
- Core startup loaded Staff, Moderation, Tickets, and Community: **31 registered prefix commands**, including subcommands; aliases are additional.
- Global source scan found no remaining previous-brand text in the delivered source/config/documentation.

Environment: Python 3.12, discord.py 2.7.1, aiohttp 3.14.3, aiosqlite 0.22.1, python-dotenv 1.2.3. The tests use temporary databases and do not contact Discord or act on real members.

## Covered behavior

- Core cog/command registration and persistent controls.
- SQLite settings persistence, per-server isolation, and concurrent version conflicts.
- Per-user/per-server staff grants, denial of unselected permissions, native moderator fallback, administrator access, and revocation.
- Staff role assignment and database save; failure prevents a grant record from being saved.
- Green/gray permission toggles and selector ownership checks.
- Target role hierarchy and refusal of dangerous staff/self-assigned roles.
- Mute/timeout parsing and Discord's 28-day upper limit.
- Universal author/logo/timestamp/default image serialization while retaining custom image content.
- Settings validation, cross-server channel rejection, invalid URLs/colors/IDs, and embed length budgets.
- Private ticket creation; concurrent repeated submissions create only one channel.
- Cleanup after failed ticket creation.
- Close/export/reopen/claim behavior; unauthorized members cannot close another member's ticket.
- Staff ticket access is removed when its grant is revoked.
- Persistent panel registrations survive a bot restart.
- Channel unlocking restores the saved three-state permission values and preserves unrelated overwrites.
- Reaction roles add/remove on the correct message and ignore another channel.
- Login requirement, OAuth state/cookie binding, single-use state, HTTP-only sessions, and logout.
- CSRF and Origin enforcement on dashboard writes.
- Live membership/Manage Server/Administrator checks using mocked Discord responses.
- Cross-server transcript denial and precision-safe Discord snowflake serialization.
- Support-role overwrite failures do not commit misleading dashboard settings.
- Saved custom embeds send their fields with mentions disabled.
- Frontend server selection, welcome preview, HTML escaping, dirty/save state, staff selection/grant saving, combined-panel editing, updated publish-panel labels, reaction-role IDs, custom embed fields, and activity-log rendering.

## Limits

No bot token, OAuth application credentials, or test Discord server were provided. Actual Discord login, delivery, moderation, gateway behavior, and hosted deployment have not been live-verified. The browser environment blocked the local preview address, so rendered screenshots/responsive visual QA were unavailable; frontend verification used jsdom and mocked API responses.

The retained optional legacy feature collection is not covered by these tests and is disabled by default. External AI/music services were not contacted. The provided signed image URLs are kept in configuration; image downloads failed in the build environment. Replace them with durable URLs as described in README.

Python 3.12 emits one upstream `audioop` deprecation warning from discord.py's voice module during tests. It does not fail core startup or the test suite.

## Repeat

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
python -m compileall -q .
node --check fortune/web/app.js
npm ci --prefix tests/ui
npm test --prefix tests/ui
```
