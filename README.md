# FortuneManager

A Discord server-management bot with a Discord-login dashboard, granular staff grants, moderation, private tickets, welcome messages, custom embeds, and reaction roles.

**Default prefix: `.`. Python 3.12 recommended.** The bot and dashboard run together with `python main.py`. No Node build, Redis, MongoDB, Lavalink, or paid API is required for the rebuilt core.

## New: Railway, invite events, and tracking

Read **[RAILWAY_AND_EVENTS.md](RAILWAY_AND_EVENTS.md)** for Railway deployment, `.event` setup, reward examples, invite/message tracking, `.check`, `.proof`, and the automatic-versus-manual rule table. SQLite data is preserved on a mounted Railway volume. Existing configured prefixes continue to work; `.` is also always accepted.

## Start here

1. Extract this folder. Install Python 3.12.
2. Create a virtual environment and install the core dependencies:

   ```sh
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux / macOS:
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`. Set `DISCORD_TOKEN` to **your own bot token**. The older `TOKEN` environment variable is also accepted. Do not share this file.
4. In the [Discord Developer Portal](https://discord.com/developers/applications), enable **Server Members Intent** and **Message Content Intent** under Bot. The core does not need Presence Intent. Rename your application/bot to **FortuneManager** there if its Discord profile still has the previous name; source-code branding does not change your Discord application profile.
5. Invite the bot with these permissions: View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Manage Server (for invite tracking), Add Reactions, Kick Members, Ban Members, Moderate Members, Manage Messages, Manage Nicknames, Manage Channels, and Manage Roles. Administrator is not required.
6. Put the bot's role above the staff role and any members/roles it needs to manage.
7. Run:

   ```sh
   python main.py
   ```

8. In a server, run `.help`. For immediate staff setup:

   ```text
   .staffrole @Staff
   .staff @wumpus
   ```

   Select permissions: green means selected. Press **Done**. The bot saves the per-server grants in SQLite and assigns the configured role.

A missing token produces an explicit startup message. Do not paste your token into source files.

## Dashboard setup

The dashboard includes server selection, welcome previews, a standalone embed builder, staff management, ticket configuration and combined panels, reaction-role mappings, transcript downloads, and an audit log.

For local use, set:

```dotenv
DISCORD_CLIENT_ID=your_application_id
DISCORD_CLIENT_SECRET=your_oauth2_client_secret
DASHBOARD_URL=http://localhost:8080
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8080
```

In Developer Portal → OAuth2, register this **exact** redirect:

```text
http://localhost:8080/auth/callback
```

Open `http://localhost:8080` on the machine running the bot and select **Continue with Discord**. This uses the `identify` and `guilds` OAuth scopes. Your client secret and access tokens remain server-side.

For a public dashboard, use a persistent Python/VPS/container host and a TLS reverse proxy. Set `DASHBOARD_URL=https://your-domain.example` and register `https://your-domain.example/auth/callback`. Set `DASHBOARD_HOST=0.0.0.0` only when your hosting/proxy requires it. The public URL must use HTTPS. The OAuth client secret is different from the bot token.

The bot maintains a persistent Discord connection; do not deploy this as a static site or an ordinary short-lived serverless function. No domain has been deployed or connected by this package.

A server owner, administrator, or member with **Manage Server** can configure that server. Only the owner or an administrator can assign staff or change the staff role. Membership and permissions are checked again against Discord for every server API request. Settings include version checks to prevent one browser tab silently overwriting another.

## Commands

Use your configured prefix in place of `-` in the older examples below; `.` is always accepted. These are **prefix commands**, not slash commands.

| Command | Purpose / required staff grant |
| --- | --- |
| `-staff @member` | Administrator opens the permission selector |
| `-staff list` | Administrator lists assigned staff |
| `-staff remove @member` | Administrator removes saved grants and assigned staff role |
| `-staffrole @role` | Administrator chooses the staff role |
| `-kick @member [reason]` | Kick |
| `-ban @member [reason]` / `-unban USER_ID [reason]` | Ban / unban |
| `-mute @member 10m [reason]` | Mute using Discord's timeout; default 10 minutes |
| `-timeout @member 2h [reason]` | Timeout |
| `-unmute @member [reason]` / `-untimeout @member` | Clear timeout; uses the Mute / unmute grant |
| `-warn @member reason` / `-warnings @member` | Warn and view recent warnings |
| `-purge 20` / `-clear 20` | Delete up to 100 messages |
| `-lock [#channel]` / `-unlock [#channel]` | Lock / unlock |
| `-deletechannel [#channel]` / `-delchannel [#channel]` | Delete channels; asks for confirmation |
| `-slowmode 10 [#channel]` | Set slowmode; use `0` to disable |
| `-nickname @member [new nickname]` / `-nick` | Change or reset nickname |
| `-ticket setup CATEGORY_ID [@Support]` | Administrator enables tickets with a default panel |
| `-ticket panel [#channel] [panel_id]` | Manage Server: publish a saved panel; default ID is `support` |
| `-ticket add @member` / `-ticket remove @member` | Ticket manager adds/removes a participant |
| `-dashboard` | Dashboard link |
| `-prefix !` | Manage Server: change the command prefix |
| `-greettest` | Manage Server: preview the saved greeting in the current channel |
| `-help [command]` / `-ping` | Help / connectivity |

Category channels do not have normal text-channel mentions. Use the category ID or its quoted name for `ticket setup`.

### Staff rules

- Grants are stored per guild and user. A grant in one server never applies in another.
- The configured staff role should have **no native administrative or moderation permissions**. It identifies staff; the bot enforces the individual grants.
- Choose Kick, Ban, Mute, Timeout, Warn, Delete messages, Lock, Delete channels, Slowmode, Ticket access, Manage tickets, and Change nicknames independently.
- Assigned staff use their saved grant list for the rebuilt commands. Non-assigned native moderators can use commands matching their Discord permissions. Server owners/administrators retain full access.
- Discord's own role hierarchy and the bot's permissions still apply. Staff cannot moderate members at or above their highest role. Administrator members cannot be timed out.
- Staff grants control **bot commands**. They cannot remove native Discord permissions a member already has through other roles.
- A staff role cannot also be a self-assigned reaction role, automatic join role, or broad ticket support role.
- Changing the configured staff role does not automatically move every staff member. Save each member again to move their assignment from the old role.
- Removing bot grants does not remove unrelated roles or their native permissions.

## Tickets

Quick setup:

```text
-ticket setup CATEGORY_ID @Support
-ticket panel #open-a-ticket
```

For a combined panel, open Dashboard → Ticket system:

1. Enable tickets; choose the default category, optional support roles, transcript log, and open-ticket limit.
2. Add a panel. Choose **Dropdown menu** or **Buttons**.
3. Add categories such as General support, Billing, Reports, and Partnerships. Each can use its own Discord category override. Up to 25 choices per panel and 10 saved panels per server.
4. Save changes, then select the panel and destination and publish.

A member chooses a category and submits a subject and description. The bot creates a private channel with explicit access for the owner, bot, configured support roles, and staff with ticket grants. It does not inherit arbitrary category role visibility.

Controls inside a ticket:

- **Claim:** ticket managers/support staff assign the conversation to themselves. Another manager cannot silently steal an existing claim.
- **Close:** the owner or a ticket manager confirms closure. A transcript is saved and the owner/added participants become read-only.
- **Transcript:** the owner, ticket staff with access, or a manager exports the conversation.
- **Reopen:** managers reopen a closed ticket, subject to the member's open-ticket limit.
- **Delete:** managers confirm deletion of a closed channel; the saved transcript remains.

`Ticket access` grants channel visibility and conversation access. `Manage tickets` also permits claim/reopen/delete and participant changes. Support roles can manage all tickets. Administrators always retain Discord's native access. Ticket controls and published panels are registered again on startup.

Publishing creates a **new message**; existing panels keep their published snapshot. To retire an old panel, delete its Discord message. Disabling tickets stops new tickets from all panels. Changing support roles updates active/closed ticket-channel overwrites before settings are committed; a failed update is reported instead of silently saving the change.

Transcripts are plain UTF-8 text including message content, embed text, authors, timestamps, and attachment links. Export is capped at the first 10,000 messages or approximately 7 MB, with a visible truncation marker. Attachments are linked, not downloaded; their remote availability is not guaranteed. Transcripts are saved under `DATA_DIR/transcripts/<guild_id>/` and can be downloaded by server managers from the dashboard after export/closure.

## Welcome messages, embeds, and reaction roles

**Welcome messages:** select a channel, enable the greeting, edit the text/embed, and optionally choose join roles. Variables are `{user}`, `{username}`, `{server}`, `{member_count}`, and `{user_id}`. Only the joining user can be pinged by a welcome message. Join roles work independently of the greeting toggle.

**Message embeds:** save a reusable announcement draft with a destination, plain message, title, description, color, optional image, and up to 25 fields. Use **Send embed to Discord** after saving. Every send creates a new message, with mentions disabled.

**Reaction roles:** add emoji/role/channel mappings. Leave the message ID blank and use **Publish reaction panel** to create a new panel (up to 20 reactions per message), or enter existing message IDs and use **Add reactions to saved messages**. Reactions grant a role; removing the reaction removes it. The bot needs Add Reactions, Read Message History, and Manage Roles. Managed, dangerous, and staff roles are rejected. Removing a mapping stops automation but does not bulk-remove roles from existing members. Reactions missed while the bot is offline are not replayed automatically.

## Branding

All serialized bot embeds receive:

- Author: **.gg/fortuneleaf** with the supplied logo and support link.
- A timestamp and the supplied logo as thumbnail.
- The supplied banner as the default image; meaningful custom images, such as a welcome image or avatar result, remain usable.
- FortuneManager as the default footer.

The supplied image URLs are set as defaults. They are signed Discord attachment links and their supplied `ex` values expire on **2026-09-16 at 23:28:06 UTC**. Set `BRAND_LOGO_URL` and `BRAND_BANNER_URL` to durable public HTTPS image URLs for ongoing operation. Do not assume a copied attachment URL is permanent. The original artwork could not be downloaded in the build environment, so the images are referenced by URL and are not bundled.

## Data, deployment, and backups

The rebuilt core uses `data/fortune/fortune.db`, unless you set `DATA_DIR` to an absolute persistent directory. Old uploaded databases under `db/` are preserved for the retained modules; the core does not import old owner IDs, staff grants, prefix settings, or moderation history automatically. Configure the new dashboard once for each server.

Run **one bot process per data directory/token**. In-process locks coordinate Discord operations; SQLite stores settings, grants, tickets, panel registrations, warnings, lock snapshots, and audit records. Persist the entire data directory and back it up with the process stopped, or use SQLite's backup API. Sessions are intentionally in memory: dashboard users sign in again after a restart.

Docker is optional:

```sh
docker compose up -d --build
```

The Compose example exposes the dashboard only on the host's loopback port 8080, keeps data in a named volume, and expects a TLS reverse proxy for public access. Its image installs core dependencies only.

## Retained original features

The uploaded feature modules and assets remain in `cogs/`, `games/`, `data/`, `utils/`, and `db/`, with FortuneManager branding. They are **optional and disabled by default**. The new moderation, greetings, staff, tickets, help, and dashboard are the active implementation.

To experiment with retained features:

```sh
python -m pip install -r requirements-legacy.txt
```

Then explicitly choose cog class names in `.env`, for example:

```dotenv
LEGACY_COGS=afk,Timer,Slots,Blackjack
```

Available original class names are listed in `fortune/legacy_modules.json`. Each selected module loads separately; a failure is logged without disabling the rebuilt core. Core commands take precedence over conflicting old commands. Superseded moderation/greeting modules and the original owner/global-control modules are not loaded by this bridge.

Legacy AI/music integrations still require your own valid service credentials/endpoints. Their third-party services and entire legacy command collection have **not** been live-tested or claimed as repaired. They are not configured through the new dashboard. Review an optional feature before enabling it; core staff grants govern the rebuilt command set, not every retained module. Existing old custom emoji IDs may require access to their original emoji servers.

This separation removes the original all-or-nothing loader, circular startup imports, hardcoded default owners, invalid dependencies, import-time event-loop startup, forced two-shard setup, and active embedded service credentials from the core startup path. The old source and databases are retained rather than discarded.

## Verification

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

Frontend DOM tests are optional and require Node 20 or newer:

```sh
npm ci --prefix tests/ui
npm test --prefix tests/ui
```

See `TEST_REPORT.md` for the exact checks and limits. These tests use local SQLite databases, mocked Discord objects, and mocked HTTP/OAuth responses. A successful test run is not a claim of a live Discord deployment.

Suggested live acceptance check after setup: save a staff member with only Kick, verify Ban is denied, remove the grant, publish a ticket panel, open/claim/close/reopen a ticket, restart the bot and reuse the old panel, test a welcome, send an embed, and add/remove a reaction role.

## Troubleshooting

- **Prefix commands do not respond:** check Message Content Intent, the configured prefix, bot channel visibility/send permissions, and startup logs. Restart after changing `.env`.
- **`-staff` reports a role problem:** configure a normal staff role below the bot, without native moderation permissions; make sure your own top role can manage the target.
- **Timeout/ban/kick denied:** check both the bot's native permissions and role order. Discord does not let an administrator be timed out.
- **Dashboard redirect fails:** its registered OAuth redirect must exactly match `DASHBOARD_URL + /auth/callback`, including protocol and port.
- **Dashboard shows no server:** your account needs Manage Server or Administrator, and the bot needs to be invited to that server.
- **A button shows an error:** check the console and channel permissions. Existing ticket controls recover on restart; a temporary staff selector expires after five minutes and should be reopened.
- **Artwork stops loading:** replace the signed attachment links with durable image URLs.
- **Optional legacy module fails:** its failure is shown in the console. Disable that cog or configure its additional dependency/provider; it does not block core startup.

## Implementation references

- [discord.py persistent views and API](https://discordpy.readthedocs.io/en/stable/api.html)
- [Discord OAuth2 authorization-code flow](https://docs.discord.com/developers/topics/oauth2)
- [Discord API reference and attachment CDN behavior](https://docs.discord.com/developers/reference)
