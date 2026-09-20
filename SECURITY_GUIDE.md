# FortuneManager security and automod

This release replaces the old antinuke and automod event handlers. It also removes the forced image branding, simplifies help, adds security/automod dashboard pages, and adds verification, tags, reminders, and reaction-role commands. This cleanup release keeps the core security, moderation, event/reward, ticket, staff, and utility modules. Entertainment modules and bundled image/font assets have been removed.

## Upgrade an existing installation

1. Stop the existing bot. Keep a backup of `.env`, the entire `DATA_DIR`, and any existing `db/` or custom `LEGACY_DATA_DIR` directories. Do not replace deployed databases with the ZIP's bundled seed databases.
2. Replace the program files and install `requirements.txt`. Python 3.12 is the tested runtime. Keep the existing token and dashboard OAuth settings.
3. Start **one** process with `python main.py`. Legacy entertainment modules are no longer loaded. Existing `LEGACY_COGS` settings have no effect.
4. As the server owner, run `.antinuke config`, `.antinuke rules`, `.antinuke trusted`, and `.antinuke health`. Review imported trust before permitting ordinary staff activity.
5. For new servers, start with `.antinuke mode observe` and `.antinuke enable`, review the logs during normal administration, then choose `.antinuke mode enforce` once your limits and trust grants match the server. Existing enabled antinuke installations retain enforcement on migration; use observe explicitly if you want a review period.
6. Set `.antinuke logging #security-logs` and `.automod logging #mod-logs`. Create a baseline with `.antinuke backup create`. Enable message filters using `.automod enable`.
7. The deployment owner can run `.synccommands` to publish hybrid slash commands. Set `OWNER_IDS` to the deployment owner's Discord ID. Prefix commands work without a slash sync.

The server owner controls antinuke. Administrators do **not** receive an automatic bypass. The bot ignores its own audit actions to avoid reacting to containment, restoration, and ordinary bot operations. The server owner is also excluded. Accordingly, do not grant untrusted users broad access to destructive bot commands; a separate bot's actions are attributed to that bot, not the human who asked it to act.

### Existing data migration

The first migration reads the persisted `anti.db` and `automod.db` once, after the original database-copy routine. Enabled state, per-action whitelist entries, existing automod actions, ignored roles/channels, and log channels are imported into `fortune.db`. Future changes use the new tables; editing old antinuke tables afterward has no effect. New server IDs start disabled.

Previously enabled antinuke servers keep a strict count of one action for their imported security policy. Review every rule before using the new engine in production. Existing scoped whitelist grants become **100 actions per rule per hour**, with no expiry, and remain visible under `.antinuke trusted`. Replace these with appropriate expiring grants. New trust grants require an expiry from 1 minute to 7 days.

**Old extra-owner immunity is not carried over.** `.extraowner` explains the new controls. Delegate individual actions using scoped trust; it does not grant the ability to disable security. `.nightmode` is now an alias for `.emergency on/off`, which removes/restores manageable administrative role permissions.

The earlier rewards change is unchanged: only qualifying verified invites within the event window, through the applicable cutoff, count. Profile lookup failures report verification pending instead of falsely reporting no eligible rewards.

## Daily commands

All examples use the default `.` prefix; replace it with the single configured prefix after a change. The previous prefix stops working. `.help <command>` shows actual arguments. Unknown or extra arguments are rejected in the new modules.

| Task | Command |
| --- | --- |
| Status, exact limits, health | `.antinuke`, `.antinuke rules`, `.antinuke health` |
| Enable / disable | `.antinuke enable`, `.antinuke disable` |
| Observe or enforce | `.antinuke mode observe`, `.antinuke mode enforce` |
| Set a ban limit | `.antinuke limit ban 3 15 30` |
| Toggle one rule | `.antinuke rule webhook_create on` |
| Choose containment | `.antinuke punishment strip` (`ban` or `kick` also supported) |
| Combined limits | `.antinuke scores 18 60` |
| Temporary maintenance allowance | `.antinuke trust @Mod channel_create 10 15` |
| Same trust command, short form | `.whitelist @Mod channel_create 10 15` |
| Revoke trust | `.antinuke untrust @Mod channel_create` or `.unwhitelist @Mod all` |
| Review incidents | `.antinuke incidents`, `.antinuke incident 12` |
| Acknowledge reviewed incident | `.antinuke resolve 12` |
| Retry failed work after fixing the cause | `.antinuke jobs`, `.antinuke retry 42` |
| Release a contained member | `.antinuke release @Member` |
| Release and explicitly restore their saved roles | `.antinuke release @Member true` |
| Manual role lockdown / recovery | `.emergency on`, `.emergency off` |
| Automatic coordinated-activity lockdown | `.antinuke autolockdown on` |
| Save and list snapshots | `.antinuke backup create`, `.antinuke backup list` |
| Preview missing-object recovery | `.antinuke restore 5` |
| Apply missing-object recovery | `.antinuke restore 5 missing confirm` |
| Preview/reset existing saved structure too | `.antinuke restore 5 full`, then `.antinuke restore 5 full confirm` |
| Dashboard confirmation code | `.antinuke dashboard` |

A limit is inclusive: `ban 3 15 30` triggers on the third ban within 15 seconds, or the thirtieth within one hour. Scoped trust has its own hourly allowance and applies only to the named action. It does not change Discord permissions.

Strip removes manageable roles in one member update, records the removed role IDs, and rechecks remaining guild/channel powers. If dangerous powers remain and the bot can ban, it bans the actor. Role regrants to a contained member queue another containment attempt. Release is owner-only. Kick permits later rejoining, so use it only when that matches your policy.

Lockdown removes dangerous permission bits from roles below the bot. It does not stop the server owner, unmanageable roles, or every chat message. The original permissions are saved; conflicting subsequent edits are reported for manual review instead of silently overwritten.

## Detection and work queues

- Discord audit entry events identify the actor and target directly. Entries are deduplicated by their IDs. Permission grants are distinguished from ordinary role changes.
- A paged audit-log reconciliation path runs alongside gateway ingestion. It scans recent entries, keeps a cursor, and honors Discord.py's request limiting. There is no five-minute protection shutdown after a burst.
- Per-rule short windows and hourly windows catch fast and slower activity. Weighted 60-second member/server totals combine actions across rules. The server-wide check requires multiple actors; automatic lockdown is optional.
- Evidence and jobs persist in SQLite. Three containment workers operate separately from the recovery/log worker. The bot remains one process; this is not an independent security service or protection against loss of the host/network.
- Containment is queued before repair. Delayed events older than two minutes may create observed incidents but do not trigger delayed automatic punishment on their own.
- Transient failures have bounded retries. Permanent permission errors appear as failed jobs/incidents. An owner can correct the cause and retry a specific job.
- Console/database records remain available if a Discord log channel fails. Optional `SECURITY_ALERT_CHANNEL_ID` sends incident alerts to another accessible channel, including in a separate server.

Security decisions are deterministic. No AI provider is used for punishment decisions.

## Snapshots and recovery

Automatic baselines are attempted every six hours when protection is enabled and there are no unresolved incidents. Up to 20 recent baselines are retained, along with snapshots needed by unfinished work. Manual snapshots use the same incident check. Evidence is retained for 48 hours; incident records remain in the database.

Snapshots capture ordinary roles, categories, supported channel types, channel permission overwrites, and selected server settings (name, verification level, default notifications, content filter). Full recovery compares saved objects, restores missing/changed supported objects, and keeps extra channels/roles. Role IDs and channel IDs created during recovery are mapped so permission overwrites can refer to the replacements. The recovery worker checks matching bot audit records when reconciling an interrupted create operation.

Automatic repair after containment can recreate the incident's deleted channel/role from a baseline, remove an unauthorized added bot, remove newly granted dangerous permissions, and undo the triggering ban. Review other damage using the preview and incident list. It does not promise a complete automatic rollback of every earlier action by the attacker.

A snapshot does **not** contain message history, attachments, ordinary role assignments, webhook secrets, vanity ownership, forum threads/posts, deleted emoji/sticker image bytes, role icons/gradients, or every server feature. A recreated channel/role gets a new Discord ID. An unbanned member must rejoin themselves. Managed roles and roles above the bot cannot be restored by this bot. Snapshot recovery cannot make deletion reversible.

## Automod

Manage Server permission is required for configuration. Changing instant-ban words also requires Ban Members. Ordinary rules exempt the server owner, administrators, bots, webhooks, and configured roles/channels. The instant-ban list bypasses administrator and configured role/channel exemptions; the server owner, bots, and webhooks remain exempt. Authorized automod/list-management commands can edit/test their own lists safely. Account-age verification and join-burst protection are separate controls.

| Task | Command |
| --- | --- |
| Strict preset | `.automod hard` |
| Instant-ban words | `.banwords add phrase`, `.banwords remove phrase`, `.banwords list` |
| Same-message repetition limit | `.automod limit repeat_count 4` |
| Review / enable | `.automod config`, `.automod enable` |
| Observe without deleting/punishing | `.automod mode observe` |
| Enable a rule and choose its action | `.automod rule links on delete` |
| Change only the action | `.automod punishment spam timeout` |
| Adjust message limits | `.automod limit spam_count 6`, `.automod limit spam_seconds 5` |
| Adjust mentions / timeout length | `.automod limit mention_count 5`, `.automod limit timeout_seconds 600` |
| Block a literal phrase | `.automod words add blocked phrase` |
| Remove / list phrases | `.automod words remove blocked phrase`, `.automod words list` |
| Allow or block a domain | `.automod domain allowed add example.com`, `.automod domain blocked add bad.example` |
| Exempt a channel or role | `.automod ignore channel #channel`, `.automod ignore role @Role` |
| Remove an exemption | `.automod unignore channel #channel`, `.automod unignore role @Role` |
| Test text without action | `.automod test text to check` |
| Young accounts in join bursts | `.automod raid on 12 10 24` |
| Native Discord spam/mention filters | `.automod native on`, `.automod native off` |

Rules are `spam`, `duplicates`, `repetition`, `mentions`, `everyone`, `invites`, `links`, `caps`, `emoji`, `words`, `domains`, and `attachments`. Actions are `delete`, `warn`, `timeout`, `kick`, and `ban`. Repeated delete/warn violations escalate to timeout after the configured `escalate_after` count in ten minutes. One message matching several filters receives one selected action, and repeated punishments are suppressed briefly while each offending message is still removed. Stronger actions bypass that cooldown: a recent timeout never suppresses a banned-word ban. Spam bursts also remove the recent offending messages that formed the burst.

Content and attachment-only edits are checked. `.automod hard` uses four messages/five seconds, three duplicates/30 seconds, four consecutive word/phrase repetitions, 12 repeated alphanumeric characters, and one-hour timeouts. Adding a banword enables enforce mode; `automod mode observe` and `automod disable` pause bans along with other enforcement. Spam counts messages; editing a message does not count as a new message. Domain matching respects hostname boundaries and subdomains, so `allowed.example.evil.test` does not inherit the allowance for `allowed.example`. Phrase filters use literal word boundaries; they are not an AI/NSFW classifier or a complete phishing detection feed. Attachment filters examine filenames/counts, not file contents.

Native Discord rules are explicitly installed separately by the server owner and remain active when the bot is offline. `.automod disable` does not delete these native rules. Run `.automod native off` to remove this bot's native rules. Discord rule quotas and built-in exemptions still apply.

## Other tools

- `.verification setup #rules @Verified 24` posts a persistent verification button with a 24-hour account age requirement. It is a role/age gate, not a CAPTCHA. Configure channel access yourself. Role safety is checked again at click time; no administrator, moderation, or staff role can be handed out through the button.
- `.tag create rules Read the rules`, `.tag rules`, `.tag get rules`, `.tag list`, `.tag delete rules`. Editing tags requires Manage Messages. Tag content cannot ping everyone.
- `.remindme 30m check the event`, `.reminders list`, `.reminders cancel 3`. Reminders persist across restarts, are delivered in their original channel, and can only be cancelled by their creator. Delivery normally checks every 15 seconds. A server/channel permission loss records failed delivery; a crash exactly between send and commit can duplicate a reminder.
- `.reactionrole add #roles MESSAGE_ID EMOJI @Role`, `.reactionrole list`, `.reactionrole remove MESSAGE_ID EMOJI`. Existing dashboard reaction roles use the same storage. Every assignment rechecks role safety.
- Moderation, staff grants, welcome, tickets/transcripts, invite events/rewards, role management, tags, reminders, and verification remain available. Run `.modulestatus` and browse `.help`.

## Dashboard and appearance

The Antinuke page is server-owner-only. Every save requires a code from `.antinuke dashboard`, delivered to the owner's Discord DMs. Codes last five minutes, work once, and are tied to the server and owner. This adds confirmation to an OAuth web session; it does not replace securing the owner's Discord account. The Automod page accepts Manage Server permission. Both pages validate settings and reject stale versions.

Help lists registered commands in categories and provides exact usage. Help and normal embeds do not inject the supplied artwork, a logo thumbnail, or a banner. User-configured welcome/announcement images and event proof attachments remain supported. No image/entertainment command modules or PNG asset files are shipped.

## What is and is not verified

See TEST_REPORT.md for the offline test results. Live Discord delivery, outage behavior on a deployed host, and comparisons with Wick, Security, Zeon, Krypton, or Carl-bot have not been benchmarked. This package combines many server tools; it is not a claim of complete proprietary feature parity or guaranteed protection.

Discord permissions, role hierarchy, event latency, and request limits still apply. Keep the bot above the roles it must manage, grant View Audit Log, protect its token, and keep durable database backups. Official references: [Discord permissions](https://docs.discord.com/developers/topics/permissions), [audit-log gateway events](https://docs.discord.com/developers/events/gateway-events#guild-audit-log-entry-create), and [rate limits](https://docs.discord.com/developers/topics/rate-limits).

## Default security rules and weights

These are new-server defaults. Imported enabled servers start with strict per-rule counts; use `.antinuke rules` to see your actual settings.

| Rule | Actions / window | Hourly count | Weight per audit entry |
| --- | --- | --- | --- |
| `ban` | 3 / 15s | 30 | 4 |
| `kick` | 5 / 15s | 50 | 3 |
| `prune` | 1 / 60s | 10 | 12 |
| `bot_add` | 1 / 60s | 10 | 8 |
| `channel_create` | 5 / 15s | 50 | 2 |
| `channel_delete` | 1 / 60s | 10 | 8 |
| `channel_update` | 5 / 15s | 50 | 2 |
| `role_create` | 5 / 15s | 50 | 2 |
| `role_delete` | 1 / 60s | 10 | 8 |
| `role_update` | 5 / 15s | 50 | 2 |
| `permission_grant` | 1 / 60s | 10 | 12 |
| `webhook_create` | 2 / 30s | 20 | 5 |
| `webhook_delete` | 3 / 30s | 30 | 3 |
| `webhook_update` | 3 / 30s | 30 | 3 |
| `guild_update` | 3 / 30s | 30 | 4 |
| `overwrite_create` | 3 / 30s | 30 | 4 |
| `overwrite_update` | 3 / 30s | 30 | 4 |
| `overwrite_delete` | 3 / 30s | 30 | 4 |
| `integration_create` | 1 / 60s | 10 | 8 |
| `integration_delete` | 2 / 30s | 20 | 4 |
| `integration_update` | 3 / 30s | 30 | 3 |
| `emoji_delete` | 5 / 30s | 50 | 2 |
| `sticker_delete` | 3 / 30s | 30 | 3 |
| `emoji_create` | 5 / 30s | 50 | 2 |
| `emoji_update` | 5 / 30s | 50 | 2 |
| `sticker_create` | 3 / 30s | 30 | 3 |
| `sticker_update` | 3 / 30s | 30 | 3 |
| `unban` | 5 / 30s | 50 | 2 |
| `member_move` | 5 / 15s | 50 | 3 |
| `member_disconnect` | 3 / 15s | 30 | 4 |
| `automod_rule_create` | 2 / 30s | 20 | 5 |
| `automod_rule_delete` | 1 / 60s | 10 | 10 |
| `automod_rule_update` | 2 / 30s | 20 | 5 |
