# FortuneManager

A streamlined Discord bot for antinuke, moderation, invite events, tickets, and server utilities. Default prefix: `.`. Python 3.12.

## What changed

- Kept Antinuke, Automod, ban/warn/kick/timeout, role management, staff, tickets, invite tracking, events/rewards, proof submissions, verification, tags, reminders, and dashboard configuration.
- Removed the old entertainment/music/game/image commands, passive chat responders, duplicate legacy command loaders, and all bundled images/fonts. No PNG assets or image-generation dependencies remain. Event proof attachments and deliberately configured announcement images are still supported.
- Commands run only when a registered command follows the active prefix at the **start** of a message. Unknown commands, bare prefixes, mentions, and prefixes inside ordinary conversation receive no command reply. Moderation still checks ordinary messages.
- A prefix change **replaces** the previous prefix. There is no permanent `.` fallback or mention prefix.
- Ordinary command responses auto-delete after **20 seconds** by default. This includes help/error responses; interactive menus last until their timeout plus five seconds. Published ticket/event/verification panels, proof records, announcements, reminders, and audit logs remain available.

## Install and run

```sh
python -m pip install -r requirements.txt
cp .env.example .env
python main.py
```

Set `DISCORD_TOKEN` in `.env`. Enable **Server Members Intent** and **Message Content Intent** in the Discord Developer Portal. The bot needs View Channel, Send Messages, Embed Links, Read Message History, Manage Messages, Ban Members, and Moderate Members for strict message moderation. Grant other capabilities required by the features you use: View Audit Log for antinuke; Manage Channels/Roles for tickets, verification, and recovery. Place the bot role above members it must moderate.

To run without the web dashboard, set `ENABLE_DASHBOARD=0`. Otherwise configure the dashboard OAuth application and callback in `.env.example`. `OWNER_IDS` grants deployment-level access to `.synccommands`; it does not replace the server owner's antinuke controls.

## Quick commands

Replace `.` with your current prefix after changing it.

| Command | Result |
| --- | --- |
| `.help` | Browse the retained commands |
| `.automod hard` | Enable strict message moderation and enforce mode |
| `.automod config` | Review enabled rules and thresholds |
| `.automod logging #mod-logs` | Record moderation actions and failures |
| `.banwords add bad phrase` | Add one literal word/phrase, enable moderation/enforce mode; matching messages are deleted and the author is banned |
| `.banwords remove bad phrase` | Remove that instant-ban entry |
| `.banwords list` | Show the list and whether enforcement is active |
| `.automod words add bad phrase` | Existing configurable word filter; default action is deletion |
| `.prefix !` | Replace `.` with `!`; next command is `!help` |
| `.prefix` | Show the current prefix |
| `.autodelete 30` | Delete normal bot replies after 30 seconds |
| `.autodelete 0` | Disable automatic reply deletion |
| `.ban @member reason`, `.warn @member reason` | Manual moderation |
| `.role add @member @role`, `.role remove @member @role` | Manage roles with permission and hierarchy checks |
| `.event`, `.check`, `.proof Title` | Existing event builder, reward check, and proof flow |

`banwords add/remove` requires **Manage Server and Ban Members**. Other automod configuration requires Manage Server. New servers start with automod disabled until `.automod enable`, `.automod hard`, or `.banwords add` is used.

## Strict moderation behavior

`.automod hard` enables these defaults:

- Four messages in five seconds, or three matching messages in 30 seconds: delete the detected burst and timeout the author for one hour.
- Four consecutive copies of a word/phrase **inside one message**, or a 12-character alphanumeric run: delete and timeout for one hour. Case, whitespace, and common zero-width differences are normalized.
- Mass mentions/everyone mentions and emoji spam: delete and timeout. Invite, blocked-domain, and risky-attachment rules are enabled. Existing word lists, domain lists, logging, and configured exemptions are preserved.

Use `.automod limit repeat_count 5`, `.automod limit repeat_characters 15`, or `.automod limit timeout_seconds 600` to tune the limits. `.automod rule repetition on timeout` controls the same-message repetition rule separately.

`banwords` matches literal whole words/phrases, ignoring case, full-width character variants, common zero-width characters, and repeated whitespace. A word does not match unrelated larger words. These are literal filters, not semantic detection or exhaustive anti-evasion matching.

Instant-ban words apply despite ordinary role/channel exemptions, including to administrators below the bot role. The server owner, bots, and webhooks are exempt. Authorized automod/list management commands are exempt so moderators can edit/test the lists. Ordinary spam rules retain the existing administrator and configured role/channel exemptions. `.automod mode observe` logs without deleting/punishing; `.automod disable` pauses the pipeline, including banwords.

All enforced violations are deleted even if Discord refuses the punishment. A failed deletion does not prevent an attempted ban; failures are recorded. Message and attachment edits are checked. A prior timeout never suppresses a stronger banned-word ban. Discord role hierarchy and permissions still limit what the bot can do.

## Upgrade an existing deployment

Stop the old process. Keep your existing `.env`, `DATA_DIR`, and any custom `LEGACY_DATA_DIR` or deployed `db/` files. Replace program files from this ZIP, reinstall `requirements.txt`, and restart one process. Do not overwrite deployed databases with seed files from the ZIP.

Existing `fortune.db` settings, warnings, tickets, invite counts, events, and security policies remain intact. Old saved automod configurations gain the new repetition rule and an empty instant-ban list without losing existing choices. The first migration can still import legacy Antinuke/Automod settings from persisted `anti.db`/`automod.db`. `LEGACY_COGS` no longer loads additional modules, even if an old environment sets it to `all`.

If you previously published slash commands, the deployment owner can run `.synccommands` to refresh the command list. Prefix commands do not require syncing. See `RAILWAY_AND_EVENTS.md` for Railway volumes and the existing event workflow; `SECURITY_GUIDE.md` describes antinuke settings.

## Verification

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
npm ci --prefix tests/ui
npm test --prefix tests/ui
```

Tests use temporary SQLite databases and mocked Discord I/O. See `TEST_REPORT.md`. No real member is banned by these tests. A live Discord deployment has not been exercised for this update.
