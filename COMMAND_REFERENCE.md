# FortuneManager command reference

Generated from the cleaned release. Examples use the default `.`; after `.prefix !`, use `!` instead. Only the configured prefix works. Required arguments use `<...>` and optional arguments use `[...]`.

Normal bot responses delete after 20 seconds by default; use `.autodelete` to inspect or change the delay.

## Security

| Command | Description |
| --- | --- |
| `.antinuke` | Show protection status. Server owner only. Use help antinuke for all settings. Aliases: `anti`, `security`. |
| `.antinuke autolockdown <enabled>` | Toggle removal of manageable staff permissions when coordinated activity reaches the server limit. |
| `.antinuke backup` | List saved server snapshots. Use antinuke backup create to save a baseline. |
| `.antinuke backup create` | Save roles, channel structure, overwrites, and supported server settings. Never includes messages. |
| `.antinuke backup list` | List saved server structure snapshots. |
| `.antinuke config` | Show the current action, mode, scoring limits, log channel, and trust count. Aliases: `settings`. |
| `.antinuke dashboard` | Send a single-use, five-minute code to your DMs for saving dashboard security settings. |
| `.antinuke disable` | Disable automatic detection and containment; retain settings and evidence. |
| `.antinuke enable` | Enable protection. Requires View Audit Log, Manage Roles, and Ban Members. |
| `.antinuke health` | Check bot permissions, role placement, worker status, and unfinished work. |
| `.antinuke incident <case_id>` | Show an incident's evidence, decision, and containment result. |
| `.antinuke incidents` | Show the latest ten recorded security incidents. |
| `.antinuke jobs` | Show recent recovery and containment jobs, including errors. |
| `.antinuke limit <rule> <count> [seconds=15] [hourly=30]` | Set a rule limit. Example: antinuke limit ban 3 15 30. Ranges: 1–100 actions, 1–300 seconds, 1–1000/hour. |
| `.antinuke lockdown <enabled>` | On: remove dangerous permissions from manageable roles. Off: restore the saved permissions after review. |
| `.antinuke logging <channel>` | Set the security incident log channel. The bot needs Send Messages and Embed Links. Aliases: `log`. |
| `.antinuke mode <mode>` | Choose observe (record only) or enforce (apply containment). |
| `.antinuke punishment <action>` | Choose strip, ban, or kick. Strip removes roles and bans if dangerous powers remain. Aliases: `action`. |
| `.antinuke release <member> [restore_roles=False]` | End role-regrant protection for a contained member. Add true to restore their saved manageable roles. |
| `.antinuke resolve <case_id>` | Mark a reviewed incident resolved. Does not restore roles or unban its actor. |
| `.antinuke restore <snapshot_id> [mode=missing] [confirmation=preview]` | Preview recovery; use antinuke restore ID missing confirm to apply. Full mode also resets saved settings. No message history is restored. |
| `.antinuke retry <job_id>` | Retry one failed containment or recovery job after correcting its error. |
| `.antinuke rule <rule> <enabled>` | Enable or disable one rule. Example: antinuke rule channel_delete on. |
| `.antinuke rules` | List exact rule names and limits. A limit of 3 triggers on action 3. |
| `.antinuke scores <member_limit> <server_limit>` | Set combined risk limits for the 60-second window; each value must be 5–500. |
| `.antinuke trust <member> <rule> [budget=20] [minutes=60]` | Allow one rule with an hourly allowance and expiry. Example: antinuke trust @Mod channel_create 10 15. |
| `.antinuke trusted` | List trust scopes, hourly allowances, and expiry times. |
| `.antinuke untrust <member> [rule=all]` | Remove one trust grant or all grants for a member. |
| `.emergency <enabled>` | Toggle a security lockdown. Usage: emergency on or emergency off. Server owner only. Aliases: `nightmode`. |
| `.extraowner` | Explain owner controls and show scoped trust. Extra-owner immunity is no longer used. |
| `.unwhitelist <member> [rule=all]` | Remove scoped antinuke trust for a member; defaults to all rules. Aliases: `unwl`. |
| `.whitelist <member> <rule> [budget=20] [minutes=60]` | Add scoped antinuke trust. Usage: whitelist @member rule [hourly allowance] [minutes]. Aliases: `wl`. |
| `.whitelisted` | List the current scoped antinuke trust grants. Aliases: `wlist`. |
| `.whitelistreset <confirmation>` | Remove every trust grant. Usage: whitelistreset confirm. |

## Automoderation

| Command | Description |
| --- | --- |
| `.automod` | Show automod status and rules. Manage Server permission required. Aliases: `am`. |
| `.automod config` | Show enabled rules, their actions, exemptions, and detection thresholds. |
| `.automod disable` | Disable the bot's automod pipeline; retain settings. Native Discord rules remain until removed. |
| `.automod domain <list_type> [operation=list] [domain]` | Manage allowed/blocked link domains. Example: automod domain blocked add bad.example. |
| `.automod enable` | Enable the configured rules. Existing choices are preserved. |
| `.automod hard` | Enable strict anti-spam: 4 messages/5s, 3 duplicates/30s, repeated text, and 1h timeouts. Aliases: `strict`. |
| `.automod ignore` | Show automod exemptions; use ignore channel or ignore role to add one. |
| `.automod ignore channel <channel>` | Exempt a text channel and its threads from message automod. |
| `.automod ignore reset` | Remove all configured channel and role exemptions. |
| `.automod ignore role <role>` | Exempt members with this role from message automod. |
| `.automod ignore show` | List all message automod exemptions. |
| `.automod limit <setting> <value>` | Set spam_count, spam_seconds, duplicate_count, repeat_count, repeat_characters, mention_count, caps_percent, caps_min, emoji_count, attachment_count, timeout_seconds, or escalate_after. |
| `.automod logging <channel>` | Set the channel for automod actions and failures. Aliases: `log`. |
| `.automod mode <mode>` | Choose observe (log only) or enforce (delete and apply the configured action). |
| `.automod native <enabled>` | Install/remove FortuneManager's native Discord mention and spam rules. Native rules also work while the bot is offline. |
| `.automod punishment <rule> <action>` | Set delete, warn, timeout, kick, or ban for one rule without changing whether it is enabled. Aliases: `action`. |
| `.automod raid <enabled> [count=12] [seconds=10] [min_age_hours=24]` | Timeout young accounts in a join burst. Example: automod raid on 12 10 24. Existing members are not targeted. |
| `.automod rule <rule> <enabled> [action=delete]` | Set one rule. Rules: spam, duplicates, repetition, mentions, everyone, invites, links, caps, emoji, words, domains, attachments. |
| `.automod test <text>` | Preview content filters without punishing anyone. Does not simulate message rate or native rules. |
| `.automod unignore` | Remove an exemption using unignore channel or unignore role. |
| `.automod unignore channel <channel>` | Remove a channel exemption. |
| `.automod unignore role <role>` | Remove a role exemption. |
| `.automod words [operation=list] [word]` | Manage literal blocked words/phrases: automod words add phrase, remove phrase, or list. |
| `.banwords` | Manage instant-ban words/phrases: banwords add, remove, or list. Matching uses whole words. Aliases: `banword`. |
| `.banwords add <word>` | Add a literal word or phrase; enable enforce mode. Matches are deleted and the sender is banned. |
| `.banwords list` | List instant-ban words and the current enforcement status. |
| `.banwords remove <word>` | Remove an instant-ban word or phrase. Aliases: `delete`. |

## Moderation

| Command | Description |
| --- | --- |
| `.ban <member> [reason=No reason supplied]` | Use this command or its help page for details. |
| `.deletechannel [channel]` | Use this command or its help page for details. Aliases: `delchannel`. |
| `.kick <member> [reason=No reason supplied]` | Use this command or its help page for details. |
| `.lock [channel]` | Use this command or its help page for details. |
| `.mute <member> [duration=10m] [reason=No reason supplied]` | Mute using Discord timeout, default 10 minutes. |
| `.nickname <member> [name]` | Use this command or its help page for details. Aliases: `nick`. |
| `.purge <amount>` | Use this command or its help page for details. Aliases: `clear`. |
| `.role` | Add or remove a member's role: role add @member @role; role remove @member @role. |
| `.role add <member> <role>` | Assign a role below both your highest role and the bot's role. |
| `.role remove <member> <role>` | Remove a role below both your highest role and the bot's role. |
| `.slowmode <seconds> [channel]` | Use this command or its help page for details. |
| `.timeout <member> [duration=10m] [reason=No reason supplied]` | Use this command or its help page for details. |
| `.unban <user_id> [reason=No reason supplied]` | Use this command or its help page for details. |
| `.unlock [channel]` | Use this command or its help page for details. |
| `.unmute <member> [reason=No reason supplied]` | Use this command or its help page for details. Aliases: `untimeout`. |
| `.warn <member> <reason>` | Use this command or its help page for details. |
| `.warnings <member>` | Use this command or its help page for details. |

## Staff

| Command | Description |
| --- | --- |
| `.staff <member>` | Select a member's permissions with buttons, then save. |
| `.staff list` | Use this command or its help page for details. |
| `.staff remove <member>` | Use this command or its help page for details. |
| `.staffrole <role>` | Choose the role assigned after staff permissions are saved. |

## Tickets

| Command | Description |
| --- | --- |
| `.ticket` | Use this command or its help page for details. |
| `.ticket add <member>` | Use this command or its help page for details. |
| `.ticket panel [channel] [panel_id=support]` | Use this command or its help page for details. |
| `.ticket remove <member>` | Use this command or its help page for details. |
| `.ticket setup <category> [support]` | Use this command or its help page for details. |

## Invite Tracking

| Command | Description |
| --- | --- |
| `.invites [member]` | Show verified, retained, left, and rejoined invites since tracking started. Aliases: `inv`. |
| `.leaderboard [kind=invites]` | leaderboard invites \| messages — top 20 server members. Aliases: `lb`. |
| `.messages [member]` | Show a member's message total since tracking started. Aliases: `msgs`. |
| `.trackingstatus` | Show tracking health; joins while offline or ambiguous are never fabricated. |

## Events & Rewards

| Command | Description |
| --- | --- |
| `.check` | Check event invites, select server/DM promotion, then let the ticket owner choose a reward. |
| `.event` | Build an event: edit embed → rewards → rules → category → publish. event end closes it. |
| `.event cmds <channel>` | event cmds #channel — allowed public invite-check channel for this event. |
| `.event end` | End invite accrual; existing claims remain reviewable. |
| `.event owners [members]...` | event owners @owner1 @owner2 — people notified by checks (server owner is always included). |
| `.eventreview <decision> <reason>` | eventreview approve\|reject\|reset\|dq\|reopen <reason>; approval requires evidence review. |
| `.eventrule [code=list] [quantity=1] [reason]` | Inside a claim ticket: eventrule <code> <quantity> <evidence/reason>. eventrule list lists codes. |
| `.eventticket` | Open your private claim ticket for the current event. |
| `.eventunflag <finding_id> <reason>` | Remove an incorrect finding in this ticket, retaining its audit record. |
| `.proof <title>` | proof <title> + an attached PNG/JPEG/GIF/WebP image. Sends a new copy to the proof channel. |

## Server Setup & Utility

| Command | Description |
| --- | --- |
| `.autodelete [seconds]` | Set reply cleanup in seconds (1–3600; 0 disables). Default: 20 seconds. |
| `.dashboard` | Use this command or its help page for details. |
| `.greettest` | Use this command or its help page for details. |
| `.modulestatus` | Show the loaded moderation, event, and utility modules. |
| `.ping` | Use this command or its help page for details. |
| `.prefix [value]` | Show or replace this server's only prefix. Example: prefix ! |
| `.reactionrole` | Show reaction role mappings. Use reactionrole add #channel message_id emoji @role. Aliases: `rr`. |
| `.reactionrole add <channel> <message_id> <emoji> <role>` | Bind an emoji on an existing message to a safe role. Members can remove the reaction to remove the role. |
| `.reactionrole list` | List reaction role mappings. |
| `.reactionrole remove <message_id> <emoji>` | Stop one reaction role binding. Existing member roles are kept. |
| `.reminders` | List your pending reminders in this server. |
| `.reminders cancel <reminder_id>` | Cancel one of your pending reminders. |
| `.reminders list` | List your pending reminders in this server. |
| `.remindme <when> <text>` | Schedule your reminder in this channel. Example: remindme 30m check the event. Limit: 30 pending reminders. Aliases: `remind`. |
| `.tag [name]` | Send a saved server response. Example: tag rules. Use tag list to browse. |
| `.tag create <name> <content>` | Create or update a saved response. Requires Manage Messages. No automatic mentions. Aliases: `add`, `edit`. |
| `.tag delete <name>` | Delete a saved server response. Requires Manage Messages. Aliases: `remove`. |
| `.tag get <name>` | Send a saved response by name. |
| `.tag list` | List saved responses in this server. |
| `.verification` | Configure a persistent verification button and minimum account age. This is a role gate, not a CAPTCHA. |
| `.verification disable` | Disable all FortuneManager verification buttons in this server. |
| `.verification setup <channel> <role> [min_age_hours=24]` | Publish a verification button. The role must have no moderation or admin powers. Channel access stays under your control. |
| `.verification status` | Show verification status, role, and minimum account age. |

## Other

| Command | Description |
| --- | --- |
| `.help [command]` | Show all command categories, or help for a command/category. Aliases: `h`. |
