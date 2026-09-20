# Cleanup release verification

## Results

- Python 3.12; discord.py 2.7.1; aiohttp 3.14.3.
- `python -m pytest -q`: **274 passed** (5.94 seconds).
- `npm test --prefix tests/ui`: **passed**; no dashboard JavaScript runtime errors.
- Offline startup: **9 core cogs**, **126 registered prefix commands/subcommands**, **14 hybrid slash-command roots**. The public command reference includes 125 non-hidden commands/subcommands.
- Removed **280 PNGs** and six other bundled image/font assets. Entertainment cogs and their dependency stack are absent.

## Covered behavior

- Only registered commands at the beginning of a message execute. Unknown commands, bare prefixes, mention prefixes, ordinary punctuation, and embedded prefixes stay silent.
- Consecutive prefix changes remove old prefixes; per-server isolation and saved configuration are preserved.
- Strict anti-spam detects rapid messages, repeated messages, repeated words/phrases within one message, long character runs, and message edits.
- Instant-ban words are normalized, match literal boundaries, delete the message, and ban the author. Whole-word false positives, Unicode/whitespace variants, permissions, hierarchy, ordinary exemptions, observe/disabled modes, management-command exemptions, and add/remove/list persistence are covered.
- A stronger ban overrides a recent timeout's cooldown. Concurrent/repeated message delivery does not duplicate the action or execute a blocked command.
- Spam cleanup deletes the author's offending burst without deleting another member's messages. Deletion and ban failures are recorded separately. Attachment-only edits are rechecked.
- Reply cleanup applies to send/reply, is configurable, respects an explicit delay, and keeps interactive/persistent controls usable.
- Core antinuke containment/recovery, staff permissions, moderation, events/reward accounting, ticket workflows, proof attachments, dashboard authentication, configuration validation, and optimistic locking still pass their existing regressions.
- Role management checks native permissions, grantable capabilities, and hierarchy. Dashboard instant-ban edits require Ban Members.
- Dashboard smoke test covers the new repetition/banword/cleanup controls along with existing settings, tickets, staff, and security configuration.

## Limits

Discord API calls are mocked, with real command parsing, SQLite storage, policy evaluation, and local dashboard handlers. This update has not logged into a live Discord server or been deployed to Railway. Live channel permissions, role hierarchy, Gateway delivery, Discord rate limits, and timer execution during host restarts remain deployment-dependent.

The only Python warning was discord.py's upstream deprecation warning for Python's `audioop` module. The bot does not load music or voice commands.
