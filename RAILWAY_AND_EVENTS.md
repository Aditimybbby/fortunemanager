# Railway deployment and invite events

## Deploy on Railway

1. Extract this ZIP. Put the contents of `FortuneManager/` at your repository root (the directory containing `Dockerfile`, `railway.json`, and `main.py`). Connect that repository to a Railway service. If retaining the outer directory, set the service root directory to `/FortuneManager`.
2. **Attach a Railway Volume at `/data`.** Without a volume, SQLite data will be lost when the service is replaced. Run exactly **one replica**, with no second service using this bot token/database.
3. Set these service variables (keep the token in Railway Variables, never in Git):

   ```dotenv
   DISCORD_TOKEN=your_bot_token
   DATA_DIR=/data
   DEFAULT_PREFIX=.
   DASHBOARD_HOST=0.0.0.0
   ENABLE_DASHBOARD=1
   DASHBOARD_URL=https://your-service-domain.up.railway.app
   ```

4. Railway supplies `PORT`; the bot now reads it. Leave the service start-command override empty: the Dockerfile's entrypoint prepares volume ownership, drops root privileges, and runs `python main.py`. `railway.json` uses `/health` and waits for Discord readiness. Leave `ENABLE_DASHBOARD=1` for that health endpoint.
5. Generate a public Railway domain. If using the dashboard, also set `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET`, and register `https://your-service-domain.up.railway.app/auth/callback` in Discord OAuth2. These OAuth settings are optional for prefix commands.
6. In Discord Developer Portal, enable **Server Members Intent** and **Message Content Intent**. Presence Intent is optional; it is not used to guess whether someone is fake or mass offline.
7. Grant the bot **Manage Server** to read invite counters. Also grant View Channels, Send Messages, Embed Links, Attach Files, Read Message History, and Manage Channels in event categories. Keep the existing permissions needed for moderation/tickets. Manage Channels in invite channels also allows invite-create notifications; without it newly created invites may be unattributable.
8. Deploy, check service logs, and run `.trackingstatus`. Then run `.help` and `.event` as a server administrator.

No token or Railway account was available during development, so deployment and real Discord delivery have not been performed. The archive is prepared for you to deploy.

Storage remains the existing **SQLite** database at `/data/fortune.db`, using WAL and serialized short transactions. All new tables are additive; existing guild settings, staff, tickets, warnings, and dashboard data remain intact. Back up the mounted directory with a SQLite-aware backup or stop the bot first so the database and WAL are consistent. Archived optional legacy cogs retain their original separate storage behavior; this release extends the actively loaded `fortune/` core.

## Tracking commands

| Command | Behavior |
|---|---|
| `.invites [@member]` / `.inv` | Verified lifetime joins, members who ever left, and rejoined members |
| `.messages [@member]` / `.msgs` | Human message total since this version started tracking |
| `.leaderboard invites` | Top 20 by verified lifetime invite joins |
| `.leaderboard messages` | Top 20 by tracked messages |
| `.lb` | Alias for leaderboard; defaults to invites |
| `.trackingstatus` | Manage Server required; invite baseline and attribution counts |

Counters are per server and survive restarts. Bot/webhook messages and DMs do not count. Message edits/deletions do not increase/decrease totals. Text and attachment messages count, including commands; this is an activity counter, not an anti-spam ranking. Invite leaderboard totals are lifetime verified joins, not event payout scores.

Invite tracking starts when the bot connects with permission to read invites. Historical inviter/member relationships cannot be reconstructed. A member receives only one original-inviter credit; leaving and rejoining never creates another credit. Invite use deltas are inferred, not guaranteed attribution supplied by Discord. Concurrent joins, multiple uses observed together, expired/deleted one-use invites, missing permissions, and offline/reconnect intervals may be unknown. Unknown, preexisting, self-attributed, and vanity joins do not earn event rewards. The bot does not invent attribution or import Falcon's private data.

## Event setup

Only the server owner or administrators can set up events and make review decisions.

1. Run `.event` in the announcement channel.
2. Edit the shared branded embed's **title, description, color, image, footer**, and up to five custom fields. The same FortuneManager author/thumbnail branding applies to event, tracking, proof, and review embeds.
3. Choose **Next: rewards** and enter one tier per line:

   ```text
   1 invite = $0.30
   6 invites = $1
   10 invites = Nitro Booster
   30 invites = Nitro Basic
   ```

   `$0.30` and `0.3$` are both supported. Custom non-cash reward names are allowed. Each threshold must be unique.
4. Choose **Use event rules** or **No rules**. Rules mode publishes the supplied rule text, with Markdown escape clutter removed. Its mention of “Falcon” is preserved as requested; actual calculations use FortuneManager's own tracking.
5. Select the event ticket category, review the preview, and publish. The draft expires after 15 minutes; published ticket buttons survive restarts. Only one active event per server is allowed.
6. Run `.event owners @person @person` to choose notification recipients. The server owner is always included. This command changes notifications and access for newly created tickets; it does not grant administrator/review permission or modify existing ticket permissions.
7. Run `.event cmds #cmds` to set the allowed public invite-check channel. A channel literally named `cmds` is selected automatically when found. Otherwise public `.invites` checks during rules events record RESET; checks in the claimant's own event ticket remain allowed. Administrators are exempt from this public-check restriction.
8. Run `.event end` to stop the event. Existing claims remain reviewable; new event tickets cannot be opened after the event ends.

Use the **Back to embed** button during setup to revise the draft before publishing. Published terms are deliberately fixed per event; end it and start another for different reward tiers/rules. Check-channel and notification-owner settings can change while it is active.

## Ticket checks and rewards

Members click **Open event ticket**, or run `.eventticket`. Each person has one claim per event. Event tickets have the existing close/transcript/reopen controls. Existing FortuneManager tickets also work if opened after the event began, in the selected event category, with a known ticket owner.

Inside the ticket, run `.check`, then select **Server promo** or **DM promo**. The first selection is locked for members; an administrator can run `.check` and correct it. Only the ticket owner or an administrator can check it. A ticket is never assigned to a member just because its name contains that person's name.

The bot:

- Includes only verified original joins after event publication and at/before ticket creation. Ticket recovery retains the original cutoff.
- Applies enabled deductions, clamps at zero, and rounds down to an even number in rules mode.
- Shows the ticket owner a reward dropdown containing **all options they qualify for**, with nothing automatically selected.
- Saves their chosen option, calculates that reward, renames the ticket, and pings configured event owners with a breakdown.
- Saves the result as **pending staff review**. Approval is one per event/member; repeat checks do not make new reward claims.

**The member chooses their reward.** The bot never picks the highest tier or substitutes another reward. For example, a member with 10 eligible invites can choose the per-invite cash option, the fixed six-invite cash reward, or Nitro Booster.

Only a cash option with threshold **1** multiplies per eligible invite. Other options award the configured fixed reward once. Rewards do not stack, and there is no remainder bonus.

| Eligible invites after deductions and rounding | Member's choice | Calculated reward |
|---:|---|---|
| 4 | $0.30 per invite | $1.20 |
| 6 | $0.30 per invite | $1.80 |
| 6 | 6 invites = $1 | $1.00 |
| 10 | $0.30 per invite | $3.00 |
| 10 | 10 invites = Nitro Booster | Nitro Booster |
| 30 | 30 invites = Nitro Basic | Nitro Basic |

Only the ticket owner can choose, including when an administrator starts the check. They may rerun `.check` to change their choice while the claim is pending. The saved choice survives restarts. Dropdowns expire after three minutes; rerun `.check` to get another. Every selection and approval checks eligibility again, so an old dropdown cannot claim a reward after deductions make it unavailable. The bot never falls back to a cheaper reward automatically.

Upgrading an existing deployment adds a nullable `selected_reward` column to SQLite without deleting existing claims. Pending claims from the earlier automatic-selection version must have the owner choose a reward before approval. Already reviewed claims retain their history; reopening one also requires an explicit selection if it has none.

## Rule handling

All supplied rules appear in the event announcement. The table below describes actual enforcement; displaying a rule is not a claim that Discord exposes the evidence needed to enforce it.

| Rule | Implementation |
|---|---|
| Even counts | Round down **after** deductions |
| Vanity, unknown, preexisting joins | Never credited |
| Invalid invite | Staff record `invalid`: −1 per invalid invite |
| Server promotion | −4 once per claim |
| Invited member left | −2 once per original invitee, in both modes; not credited again on return |
| Leave & rejoin | Additional −2 once per original invitee for DM promotion; no extra rejoin deduction for server promotion |
| No profile picture | Live Discord check: RESET for DM, −2 per affected member for server promotion |
| No onboarding | Check `completed_onboarding` only when Discord reports guild onboarding enabled; −3 DM / −1 server per member |
| Account under four months | Four calendar months at check time, using Discord account creation timestamp; −3 DM / −1 server per member |
| No biography | Staff finding `no_bio`: RESET DM / −2 server per affected member |
| No status + bio | Staff finding `no_status_bio`: disqualification |
| No proof within five days | At check/review time, proof must have been submitted from this claim's ticket within five days of opening; late proof does not silently clear the finding |
| Checking invites in prohibited chat | `.invites` / `.inv` outside Cmds or the claimant's own event ticket records a RESET finding; deduplicated per participant/event |
| Mass offline, J4J/J2J/J6J, alts/tokens, promo evidence, misconduct, wrong ticket, requesting payout, ping/delete, “No Limit” | Staff records the matching code with evidence; cannot be truthfully inferred from an invite count |
| Leaving after event / scam accusation | Staff records `left_after_event` / `scam_accusation`; blocks rewards and flags ban review. A staff member uses the existing moderation command for any ban |
| Instant payout | Published rule text retained; fulfillment is manual. No payment service, Nitro gift purchase, or automatic financial transfer is configured |

Deduction interpretation: leaves and rejoins are separate findings. A member who leaves and returns costs −4 in DM mode (−2 leave and −2 rejoin) and −2 in server mode. Repeated cycles do not create new invite credit or stack those same two deductions again. Independent age/onboarding/profile deductions can stack. These choices are isolated in `fortune/event_rules.py` for changes if the event operator wants a different policy.

**No-rules events:** no even rounding, promo/leave/profile/age/onboarding deductions, RESET/DQ findings, or five-day proof deadline apply. Verified attribution, event dates, ticket cutoff, one claim per member, and staff approval still apply.

Discord bot APIs do not expose a reliable general-purpose user biography check, private DM promotion history, alternate-account identity, or evidence that offline status means abuse. Such checks remain manual. Missing member-fetch permissions/transient Discord failures explicitly block approval until verification succeeds. Nothing is paid or banned based on an unknown result.

## Staff review

Commands below are used **inside the claimant's ticket** and require administrator/owner permission.

```text
.eventrule list
.eventrule invalid 1 Duplicate invite confirmed in evidence
.eventrule no_bio 2 Two invited members have no biography
.eventrule alt 1 Alternate-account evidence reviewed
.eventunflag 12 Finding entered against the wrong claim
.eventreview approve Checked promo, profiles, screenshots, and all remaining rules
.eventreview reject Promotion evidence did not match this event
.eventreview reset Rule violation confirmed
.eventreview dq Disqualifying evidence confirmed
.eventreview reopen New evidence received; checking again
```

Findings, corrections, and decisions are audited in SQLite. Rule codes include `invalid`, `no_bio`, `no_status_bio`, `mass_offline`, `j4j`, `j2j`, `j6j`, `alt`, `token`, `mass_off`, `asking_payout`, `ping_staff`, `ping_delete`, `wrong_ticket`, `disrespect`, `scam_accusation`, `left_after_event`, `no_limit`, `chat_check`, and `no_proof`.

Approval recalculates current eligibility, requires a selected promo mode and an explicit reward choice by the ticket owner, disallows blocking findings, and requires screenshot proof for rules events. Approval does not send money or Nitro. The owners fulfill the reward manually. An approved claim is not automatically reopened when a later leave occurs; staff can reopen it and record the policy finding.

## Proof command

Send `.proof Your proof title` **with an image attached to the same message**. PNG, JPEG, GIF, and WebP signatures are accepted, up to 8 MiB. Text-only submissions are rejected. The bot uploads a new copy in an embed with the title and submitter identification to:

[Configured proof channel](https://discord.com/channels/1545431325995958314/1545431327850102878)

The default destination is restricted to that same server so the bot cannot forward another server's private attachments there. For event eligibility, submit proof from the event ticket as its owner. The command posts the attachment you select; ensure it is appropriate for whoever can view the proof channel. Other servers can use no-rules events; to run proof-required events there, change `PROOF_GUILD` and `PROOF_CHANNEL` together in `fortune/events.py` before deploying.

## Verification and reference material

Run the automated suite from the project directory:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use temporary SQLite files and mocked Discord interactions. See `UPDATE_TEST_REPORT.md` for the result and the live checks still needed. The original `TEST_REPORT.md` records the previous version's checks.

Primary references: [Railway volumes](https://docs.railway.com/volumes), [Railway configuration](https://docs.railway.com/config-as-code/reference), [Discord gateway intents](https://docs.discord.com/developers/events/gateway), [Discord guild/member API](https://docs.discord.com/developers/resources/guild).
