"""Owner-controlled security commands and Discord event adapters."""

import json
import time
import discord
from discord.ext import commands
from .branding import embed
from .security_engine import SecurityEngine
from .security_policy import RULES, rule_name


class Antinuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.engine = SecurityEngine(bot)

    async def cog_load(self):
        await self.engine.start()

    async def cog_unload(self):
        await self.engine.close()

    async def cog_check(self, ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage("Use this command in a server.")
        if ctx.author.id != ctx.guild.owner_id:
            raise commands.CheckFailure(
                "Only the server owner can manage antinuke settings."
            )
        return True

    async def change(self, ctx, edit):
        p, v = await self.engine.data.policy(ctx.guild.id)
        edit(p)
        await self.engine.data.save(ctx.guild.id, p, v, ctx.author.id)

    async def reply(self, ctx, title, body):
        await ctx.send(embed=embed(title, body))

    @commands.hybrid_group(
        name="antinuke", aliases=["anti", "security"], invoke_without_command=True
    )
    async def antinuke(self, ctx):
        """Show protection status. Server owner only. Use help antinuke for all settings."""
        p, v = await self.engine.data.policy(ctx.guild.id)
        await self.reply(
            ctx,
            "Antinuke",
            f'**Status:** {"Enabled" if p["enabled"] else "Disabled"}\n'
            f'**Mode:** {p["mode"]}\n**Action:** {p["punishment"]}\n**Policy:** v{v}\n\n'
            f"`{ctx.clean_prefix}antinuke enable`\n`{ctx.clean_prefix}antinuke rules`\n"
            f"`{ctx.clean_prefix}antinuke health`\n`{ctx.clean_prefix}help antinuke`",
        )

    @antinuke.command(name="enable")
    async def enable(self, ctx):
        """Enable protection. Requires View Audit Log, Manage Roles, and Ban Members."""
        missing = [
            p
            for p in ("view_audit_log", "manage_roles", "ban_members")
            if not getattr(ctx.guild.me.guild_permissions, p)
        ]
        if missing:
            raise commands.BotMissingPermissions(missing)
        await self.change(ctx, lambda p: p.update(enabled=True))
        await self.reply(
            ctx,
            "Antinuke enabled",
            "Run `antinuke rules` to review limits and `antinuke health` to check role placement.",
        )

    @antinuke.command(name="disable")
    async def disable(self, ctx):
        """Disable automatic detection and containment; retain settings and evidence."""
        await self.change(ctx, lambda p: p.update(enabled=False))
        await self.reply(
            ctx,
            "Antinuke disabled",
            "Settings, snapshots, and incident records have been kept.",
        )

    @antinuke.command(name="config", aliases=["settings"])
    async def config(self, ctx):
        """Show the current action, mode, scoring limits, log channel, and trust count."""
        p, v = await self.engine.data.policy(ctx.guild.id)
        await self.reply(
            ctx,
            "Antinuke settings",
            f'Enabled: **{p["enabled"]}** · Mode: **{p["mode"]}**\n'
            f'Action: **{p["punishment"]}** · Auto lockdown: **{p["auto_lockdown"]}**\n'
            f'Combined limits: **{p["actor_score"]}** per member / **{p["guild_score"]}** per server in 60s\n'
            f'Log channel: {"<#"+str(p["log_channel_id"])+">" if p["log_channel_id"] else "Not set"}\n'
            f'Trusted members: **{len(p["trust"])}** · Policy version: **{v}**',
        )

    @antinuke.command(name="rules")
    async def rules(self, ctx):
        """List exact rule names and limits. A limit of 3 triggers on action 3."""
        p, _ = await self.engine.data.policy(ctx.guild.id)
        lines = [
            f'`{name}` · {r["count"]}/{r["seconds"]}s · {r["hourly"]}/hour · {"on" if r["enabled"] else "off"}'
            for name, r in p["rules"].items()
        ]
        for offset in range(0, len(lines), 12):
            await self.reply(
                ctx,
                "Antinuke rules",
                "\n".join(lines[offset : offset + 12])
                + "\n\nLimits trigger on the listed count. Scoped trust has its own hourly allowance.",
            )

    @antinuke.command(name="limit")
    async def limit(
        self, ctx, rule: str, count: int, seconds: int = 15, hourly: int = 30
    ):
        """Set a rule limit. Example: antinuke limit ban 3 15 30. Ranges: 1–100 actions, 1–300 seconds, 1–1000/hour."""
        name = rule_name(rule)
        await self.change(
            ctx,
            lambda p: p["rules"][name].update(
                count=count, seconds=seconds, hourly=hourly
            ),
        )
        await self.reply(
            ctx,
            "Limit updated",
            f"`{name}` triggers at **{count} actions / {seconds}s** or **{hourly} / hour**.",
        )

    @antinuke.command(name="rule")
    async def rule(self, ctx, rule: str, enabled: bool):
        """Enable or disable one rule. Example: antinuke rule channel_delete on."""
        name = rule_name(rule)
        await self.change(ctx, lambda p: p["rules"][name].update(enabled=enabled))
        await self.reply(
            ctx, "Rule updated", f'`{name}` is {"enabled" if enabled else "disabled"}.'
        )

    @antinuke.command(name="mode")
    async def mode(self, ctx, mode: str):
        """Choose observe (record only) or enforce (apply containment)."""
        await self.change(ctx, lambda p: p.update(mode=mode.lower()))
        await self.reply(
            ctx, "Mode updated", f"Antinuke is in **{mode.lower()}** mode."
        )

    @antinuke.command(name="punishment", aliases=["action"])
    async def punishment(self, ctx, action: str):
        """Choose strip, ban, or kick. Strip removes roles and bans if dangerous powers remain."""
        await self.change(ctx, lambda p: p.update(punishment=action.lower()))
        await self.reply(
            ctx, "Action updated", f"Containment action: **{action.lower()}**."
        )

    @antinuke.command(name="logging", aliases=["log"])
    async def logging(self, ctx, channel: discord.TextChannel):
        """Set the security incident log channel. The bot needs Send Messages and Embed Links."""
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            raise ValueError("I need Send Messages and Embed Links in that channel.")
        await self.change(ctx, lambda p: p.update(log_channel_id=channel.id))
        await self.reply(
            ctx,
            "Security logging",
            f"Incident updates will be sent to {channel.mention}.",
        )

    @antinuke.command(name="scores")
    async def scores(self, ctx, member_limit: int, server_limit: int):
        """Set combined risk limits for the 60-second window; each value must be 5–500."""
        await self.change(
            ctx, lambda p: p.update(actor_score=member_limit, guild_score=server_limit)
        )
        await self.reply(
            ctx,
            "Combined limits updated",
            f"Member: **{member_limit}** · Server: **{server_limit}**. Rule weights are listed in SECURITY_GUIDE.md.",
        )

    @antinuke.command(name="autolockdown")
    async def autolockdown(self, ctx, enabled: bool):
        """Toggle removal of manageable staff permissions when coordinated activity reaches the server limit."""
        await self.change(ctx, lambda p: p.update(auto_lockdown=enabled))
        await self.reply(
            ctx,
            "Automatic lockdown",
            f'{"Enabled" if enabled else "Disabled"}. Use `antinuke lockdown off` to restore saved role permissions after review.',
        )

    async def grant(self, ctx, member, rule, budget, minutes):
        name = rule_name(rule)
        if not 1 <= minutes <= 10080:
            raise ValueError("Trust duration must be 1–10080 minutes (7 days).")

        def update(p):
            p["trust"].setdefault(str(member.id), {})[name] = dict(
                budget=budget, expires=time.time() + minutes * 60
            )

        await self.change(ctx, update)
        await self.reply(
            ctx,
            "Trust added",
            f"{member.mention} may perform **{budget} `{name}` actions per hour**, for **{minutes} minutes**. Other rules still apply.",
        )

    @antinuke.command(name="trust")
    async def trust(
        self,
        ctx,
        member: discord.Member,
        rule: str,
        budget: int = 20,
        minutes: int = 60,
    ):
        """Allow one rule with an hourly allowance and expiry. Example: antinuke trust @Mod channel_create 10 15."""
        await self.grant(ctx, member, rule, budget, minutes)

    @commands.hybrid_command(name="whitelist", aliases=["wl"])
    async def whitelist(
        self,
        ctx,
        member: discord.Member,
        rule: str,
        budget: int = 20,
        minutes: int = 60,
    ):
        """Add scoped antinuke trust. Usage: whitelist @member rule [hourly allowance] [minutes]."""
        await self.grant(ctx, member, rule, budget, minutes)

    async def revoke(self, ctx, member, rule):
        name = None if rule == "all" else rule_name(rule)

        def update(p):
            if name is None:
                p["trust"].pop(str(member.id), None)
            else:
                p["trust"].get(str(member.id), {}).pop(name, None)

        await self.change(ctx, update)
        await self.reply(
            ctx, "Trust removed", f"Removed **{rule}** trust for {member.mention}."
        )

    @antinuke.command(name="untrust")
    async def untrust(self, ctx, member: discord.Member, rule: str = "all"):
        """Remove one trust grant or all grants for a member."""
        await self.revoke(ctx, member, rule)

    @commands.hybrid_command(name="unwhitelist", aliases=["unwl"])
    async def unwhitelist(self, ctx, member: discord.Member, rule: str = "all"):
        """Remove scoped antinuke trust for a member; defaults to all rules."""
        await self.revoke(ctx, member, rule)

    @antinuke.command(name="trusted")
    async def trusted(self, ctx):
        """List trust scopes, hourly allowances, and expiry times."""
        p, _ = await self.engine.data.policy(ctx.guild.id)
        lines = []
        for uid, grants in p["trust"].items():
            for name, g in grants.items():
                expiry = (
                    f'<t:{int(g["expires"])}:R>'
                    if g["expires"]
                    else "no expiry (imported)"
                )
                lines.append(f'<@{uid}> · `{name}` · {g["budget"]}/hour · {expiry}')
        for off in range(0, max(1, len(lines)), 15):
            await self.reply(
                ctx,
                "Scoped trust",
                "\n".join(lines[off : off + 15]) or "No trust grants.",
            )

    @commands.hybrid_command(name="whitelisted", aliases=["wlist"])
    async def whitelisted(self, ctx):
        """List the current scoped antinuke trust grants."""
        await self.trusted.callback(self, ctx)

    @commands.hybrid_command(name="whitelistreset")
    async def whitelistreset(self, ctx, confirmation: str):
        """Remove every trust grant. Usage: whitelistreset confirm."""
        if confirmation != "confirm":
            raise ValueError("Use `whitelistreset confirm` to remove all trust grants.")
        await self.change(ctx, lambda p: p.update(trust={}))
        await self.reply(ctx, "Trust reset", "All scoped trust grants were removed.")

    @commands.hybrid_command(name="extraowner")
    async def extraowner(self, ctx):
        """Explain owner controls and show scoped trust. Extra-owner immunity is no longer used."""
        await self.reply(
            ctx,
            "Security access",
            "The server owner controls security settings. Use `whitelist @member rule budget minutes` to delegate a specific action. Existing extra-owner records do not bypass antinuke.",
        )
        await self.trusted.callback(self, ctx)

    @antinuke.command(name="health")
    async def health(self, ctx):
        """Check bot permissions, role placement, worker status, and unfinished work."""
        h = self.engine.health(ctx.guild)
        jobs = await self.bot.store.rows(
            "SELECT status,COUNT(*) AS n FROM security_jobs WHERE guild_id=? GROUP BY status",
            (ctx.guild.id,),
        )
        await self.reply(
            ctx,
            "Security health",
            "Missing permissions: **"
            + (", ".join(h["missing_permissions"]) or "None")
            + "**\n"
            "Roles above the bot: **"
            + (", ".join(h["unmanageable_roles"])[:800] or "None")
            + "**\n"
            f'Workers running: **{h["workers_running"]}**\nJobs: '
            + (", ".join(f'{r["n"]} {r["status"]}' for r in jobs) or "None")
            + "\n\nThe server owner and roles above the bot cannot be contained.",
        )

    @antinuke.command(name="incidents")
    async def incidents(self, ctx):
        """Show the latest ten recorded security incidents."""
        rows = await self.bot.store.rows(
            "SELECT * FROM security_incidents WHERE guild_id=? ORDER BY id DESC LIMIT 10",
            (ctx.guild.id,),
        )
        await self.reply(
            ctx,
            "Security incidents",
            "\n".join(
                f'**#{r["id"]}** · <@{r["actor_id"]}> · `{r["rule"]}` · {r["status"]}'
                + (" · resolved" if r["resolved"] else "")
                for r in rows
            )
            or "No incidents recorded.",
        )

    @antinuke.command(name="incident")
    async def incident(self, ctx, case_id: int):
        """Show an incident's evidence, decision, and containment result."""
        row = await self.bot.store.one(
            "SELECT * FROM security_incidents WHERE guild_id=? AND id=?",
            (ctx.guild.id, case_id),
        )
        if not row:
            raise ValueError("Incident not found in this server.")
        await self.reply(
            ctx,
            f"Security case #{case_id}",
            f'Actor: <@{row["actor_id"]}>\nAudit entry: `{row["event_id"]}`\n'
            f'Rule: `{row["rule"]}`\nStatus: **{row["status"]}**\nReason: {row["reason"]}\nResult: {row["result"] or "Pending"}',
        )

    @antinuke.command(name="resolve")
    async def resolve(self, ctx, case_id: int):
        """Mark a reviewed incident resolved. Does not restore roles or unban its actor."""
        row = await self.bot.store.one(
            "SELECT * FROM security_incidents WHERE guild_id=? AND id=?",
            (ctx.guild.id, case_id),
        )
        if not row:
            raise ValueError("Incident not found in this server.")
        if row["status"] == "queued":
            raise ValueError(
                "Wait for containment to finish before resolving this incident."
            )
        await self.bot.store.execute(
            "UPDATE security_incidents SET resolved=1 WHERE guild_id=? AND id=?",
            (ctx.guild.id, case_id),
        )
        await self.bot.store.audit(
            ctx.guild.id, ctx.author.id, "security.resolve", case_id
        )
        await self.reply(
            ctx, "Incident resolved", f"Case **#{case_id}** was marked reviewed."
        )

    @antinuke.command(name="retry")
    async def retry(self, ctx, job_id: int):
        """Retry one failed containment or recovery job after correcting its error."""
        row = await self.bot.store.one(
            "SELECT * FROM security_jobs WHERE guild_id=? AND id=?",
            (ctx.guild.id, job_id),
        )
        if not row or row["status"] != "failed":
            raise ValueError("No failed job with that ID in this server.")
        await self.bot.store.execute(
            "UPDATE security_jobs SET status='pending',attempts=0,available=0 WHERE id=?",
            (job_id,),
        )
        await self.bot.store.audit(
            ctx.guild.id, ctx.author.id, "security.retry", job_id
        )
        await self.reply(ctx, "Job queued", f"Job **#{job_id}** will retry.")

    @antinuke.command(name="jobs")
    async def jobs(self, ctx):
        """Show recent recovery and containment jobs, including errors."""
        rows = await self.bot.store.rows(
            "SELECT * FROM security_jobs WHERE guild_id=? ORDER BY id DESC LIMIT 10",
            (ctx.guild.id,),
        )
        await self.reply(
            ctx,
            "Security jobs",
            "\n".join(
                f'**#{r["id"]}** · {r["kind"]} · {r["status"]}\n{r["error"][:200]}'
                for r in rows
            )
            or "No jobs recorded.",
        )

    @antinuke.group(name="backup", invoke_without_command=True)
    async def backup(self, ctx):
        """List saved server snapshots. Use antinuke backup create to save a baseline."""
        rows = await self.bot.store.rows(
            "SELECT id,at,label FROM security_snapshots WHERE guild_id=? ORDER BY id DESC LIMIT 10",
            (ctx.guild.id,),
        )
        await self.reply(
            ctx,
            "Server snapshots",
            "\n".join(
                f'**#{r["id"]}** · <t:{int(r["at"])}:f> · {r["label"]}' for r in rows
            )
            or "No snapshots. Use `antinuke backup create`.",
        )

    @backup.command(name="list")
    async def backup_list(self, ctx):
        """List saved server structure snapshots."""
        await self.backup.callback(self, ctx)

    @backup.command(name="create")
    async def backup_create(self, ctx):
        """Save roles, channel structure, overwrites, and supported server settings. Never includes messages."""
        ident = await self.engine.recovery.snapshot(ctx.guild)
        await self.reply(
            ctx,
            "Snapshot saved",
            f"Snapshot **#{ident}** saved. Preview changes with `antinuke restore {ident}`.",
        )

    @antinuke.command(name="restore")
    async def restore(
        self,
        ctx,
        snapshot_id: int,
        mode: str = "missing",
        confirmation: str = "preview",
    ):
        """Preview recovery; use antinuke restore ID missing confirm to apply. Full mode also resets saved settings. No message history is restored."""
        if mode not in ("missing", "full"):
            raise ValueError("Choose missing or full.")
        if confirmation not in ("preview", "confirm"):
            raise ValueError("Choose preview or confirm.")
        if confirmation == "confirm":
            count = await self.engine.recovery.enqueue(ctx.guild, snapshot_id, mode)
            await self.bot.store.audit(
                ctx.guild.id, ctx.author.id, "security.restore", f"{snapshot_id} {mode}"
            )
            return await self.reply(
                ctx,
                "Recovery queued",
                f"Queued **{count}** object changes. Use `antinuke jobs` to follow progress. Full mode also queues server settings.",
            )
        snap, plan = await self.engine.recovery.plan(ctx.guild, snapshot_id)
        chosen = [x for x in plan if mode == "full" or x[2] == "missing"]
        await self.reply(
            ctx,
            f"Recovery preview · #{snapshot_id}",
            "\n".join(
                f"{state} {kind}: {discord.utils.escape_markdown(name)}"
                for kind, oid, state, name in chosen[:20]
            )
            + f"\n\n**{len(chosen)} object changes**. Extra channels and roles are kept.\n"
            f"Apply: `{ctx.clean_prefix}antinuke restore {snapshot_id} {mode} confirm`\nDeleted channels receive new IDs; message history cannot be recovered.",
        )

    @antinuke.command(name="lockdown")
    async def lockdown(self, ctx, enabled: bool):
        """On: remove dangerous permissions from manageable roles. Off: restore the saved permissions after review."""
        count = await self.engine.recovery.lockdown(ctx.guild, enabled)
        await self.bot.store.audit(
            ctx.guild.id, ctx.author.id, "security.lockdown", enabled
        )
        await self.reply(
            ctx,
            "Lockdown queued",
            f"Queued changes for **{count}** roles. Managed roles and roles above the bot cannot be changed. This does not lock every chat channel.",
        )

    @commands.hybrid_command(name="emergency", aliases=["nightmode"])
    async def emergency(self, ctx, enabled: bool):
        """Toggle a security lockdown. Usage: emergency on or emergency off. Server owner only."""
        await self.lockdown.callback(self, ctx, enabled)

    @antinuke.command(name="release")
    async def release(self, ctx, member: discord.Member, restore_roles: bool = False):
        """End role-regrant protection for a contained member. Add true to restore their saved manageable roles."""
        row = await self.bot.store.one(
            "SELECT * FROM security_contained WHERE guild_id=? AND user_id=?",
            (ctx.guild.id, member.id),
        )
        if not row:
            raise ValueError("That member is not recorded as contained.")
        if restore_roles:
            roles = [
                r
                for rid in json.loads(row["roles"])
                if (r := ctx.guild.get_role(rid))
                and not r.managed
                and r < ctx.guild.me.top_role
            ]
            # Clear the regrant guard before the explicitly authorized restoration.
            await self.bot.store.execute(
                "DELETE FROM security_contained WHERE guild_id=? AND user_id=?",
                (ctx.guild.id, member.id),
            )
            try:
                if roles:
                    await member.add_roles(
                        *roles,
                        reason=f"Security release by server owner {ctx.author.id}",
                        atomic=True,
                    )
            except Exception:
                await self.bot.store.execute(
                    "INSERT OR REPLACE INTO security_contained VALUES(?,?,?,?)",
                    (ctx.guild.id, member.id, row["roles"], row["at"]),
                )
                raise
        else:
            await self.bot.store.execute(
                "DELETE FROM security_contained WHERE guild_id=? AND user_id=?",
                (ctx.guild.id, member.id),
            )
        await self.bot.store.audit(
            ctx.guild.id,
            ctx.author.id,
            "security.release",
            f"{member.id}; restore={restore_roles}",
        )
        await self.reply(
            ctx,
            "Member released",
            f"{member.mention} is no longer marked as contained."
            + (
                " Saved manageable roles were restored."
                if restore_roles
                else " No roles were restored."
            ),
        )

    @antinuke.command(name="dashboard")
    async def dashboard(self, ctx):
        """Send a single-use, five-minute code to your DMs for saving dashboard security settings."""
        code = await self.engine.data.issue_code(ctx.guild.id, ctx.author.id)
        try:
            await ctx.author.send(
                f"Security settings for {ctx.guild.name}\nConfirmation code: `{code}`\nExpires in 5 minutes; usable once."
            )
        except discord.Forbidden:
            raise ValueError(
                "Enable DMs from this server, then run this command again."
            )
        await self.reply(
            ctx,
            "Confirmation code sent",
            "Check your DMs. Enter the code when saving security settings on the dashboard.",
        )

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        await self.engine.ingest(entry)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self.engine.fallback(channel.guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self.engine.fallback(role.guild)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await self.engine.fallback(guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.engine.fallback(member.guild)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return
        row = await self.bot.store.one(
            "SELECT * FROM security_contained WHERE guild_id=? AND user_id=?",
            (after.guild.id, after.id),
        )
        if row and any(r not in before.roles for r in after.roles):
            await self.engine.data.job(
                after.guild.id, "contain", {"actor_id": after.id}, priority=0
            )
