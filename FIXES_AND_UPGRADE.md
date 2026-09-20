# Cleanup and strict moderation update

See README.md for installation, commands, and upgrade steps.

## Removed

The archived `cogs/`, `games/`, `utils/`, `prodia/`, and Top.gg service, legacy module loader/registry, entertainment commands, music/Lavalink integrations, AI/image commands, image/font assets, and their dependencies. Useful commands now come from nine core modules. Existing security, moderation, events, tickets, staff, and tracking stay in the same core SQLite database.

## Fixed

1. `resolve_prefix` returns exactly the saved prefix; it never appends `.` or bot mentions.
2. Message routing checks a real registered command at the start of the message. Unknown commands and plain-prefix messages remain silent.
3. One moderation pipeline processes new messages and edits. Enforced violations are deleted before punishment; spam cleanup includes earlier messages in that author's matching burst.
4. Punishment suppression compares action strength, so a timeout cannot suppress an immediate banned-word ban.
5. Repeated Gateway delivery reuses the moderation decision and cannot accidentally route a blocked command. Attachment-only edits are rechecked.
6. Ordinary command replies use a configurable 20-second deletion timer. Interactive menus receive time to complete; persistent records and panels are retained.

## Added

- `.automod hard` / `.automod strict`: stronger message limits and same-message repetition detection.
- `.banwords add <word or phrase>`, `.banwords remove <word or phrase>`, `.banwords list`: an explicit instant-ban list.
- `.autodelete [seconds]`: set or inspect reply cleanup, with zero to disable it.
- The Automod dashboard exposes repetition thresholds and the instant-ban list. Server settings expose reply cleanup.

The new fields are merged into existing configurations. No live server changes are made until this package is deployed and commands/settings enable the behavior.
