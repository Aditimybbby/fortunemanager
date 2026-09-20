"""Shared audit ingestion and independent containment/recovery workers."""

import asyncio
from collections import defaultdict
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
import discord
from .branding import embed
from .security_policy import Evidence, Detector, DANGEROUS, normalize
from .security_data import SecurityData
from .security_recovery import Recovery

log = logging.getLogger(__name__)


class SecurityEngine:
    def __init__(self, bot):
        self.bot = bot
        self.data = SecurityData(bot.store)
        self.store = bot.store
        self.detector = Detector()
        self.recovery = Recovery(bot, self.data)
        self.tasks = []
        self.wake = asyncio.Event()
        self.actor_locks = defaultdict(asyncio.Lock)
        self.fallback_locks = defaultdict(asyncio.Lock)
        self.last_poll = {}
        self.last_warning = {}
        self.last_audit = {}
        self.processed = 0
        self.failures = 0

    async def start(self):
        await self.data.init()
        if self.bot.load_legacy:
            await self.data.import_legacy()
        for row in await self.store.rows(
            "SELECT * FROM security_events WHERE at>? AND processed=1 ORDER BY at",
            (time.time() - 3600,),
        ):
            self.detector.remember(self.evidence(row))
        self.tasks = [
            asyncio.create_task(self.dispatch_loop(), name="security-evidence"),
            asyncio.create_task(self.job_loop(False), name="security-recovery"),
            asyncio.create_task(self.maintenance_loop(), name="security-health"),
        ]
        self.tasks.extend(
            asyncio.create_task(self.job_loop(True), name=f"security-containment-{i}")
            for i in range(3)
        )

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    @staticmethod
    def evidence(row):
        return Evidence(
            row["id"],
            row["guild_id"],
            row["actor_id"],
            row["target_id"],
            row["rule"],
            row["at"],
            json.loads(row["detail"]),
        )

    async def ingest(self, entry):
        self.last_audit[entry.guild.id] = time.time()
        policy, _ = await self.data.policy(entry.guild.id)
        if not policy["enabled"]:
            return False
        if getattr(entry, "user_id", None) in (entry.guild.owner_id, self.bot.user.id):
            return False
        event = normalize(entry)
        if event is None or event.actor_id in (entry.guild.owner_id, self.bot.user.id):
            return False
        if event.timestamp > time.time() + 10 or event.timestamp < time.time() - 3600:
            return False
        inserted = await self.data.record(event)
        if inserted:
            self.wake.set()
        return bool(inserted)

    async def process_pending(self):
        for row in await self.store.rows(
            "SELECT * FROM security_events WHERE processed=0 ORDER BY at,id LIMIT 100"
        ):
            event = self.evidence(row)
            policy, _ = await self.data.policy(event.guild_id)
            guild = self.bot.get_guild(event.guild_id)
            if (
                guild
                and policy["enabled"]
                and event.actor_id not in (guild.owner_id, guild.me.id)
            ):
                reason = self.detector.decide(
                    event, policy, now=min(time.time(), event.timestamp + 0.1)
                )
                if reason:
                    # Historical evidence contributes to limits; old actions alone
                    # never cause a delayed ban after an outage.
                    stale = event.timestamp < time.time() - 120
                    incident = await self.data.incident(
                        event,
                        reason + (" (received after outage)" if stale else ""),
                        observe=policy["mode"] == "observe" or stale,
                    )
                    if incident:
                        await self.queue_report(
                            guild,
                            "Security incident",
                            f"Case #{incident} · <@{event.actor_id}>\n{reason}\n"
                            + (
                                "Recorded only."
                                if policy["mode"] == "observe" or stale
                                else "Containment queued."
                            ),
                        )
                        if (
                            reason.startswith("Coordinated")
                            and policy["auto_lockdown"]
                            and not stale
                            and policy["mode"] == "enforce"
                        ):
                            await self.recovery.lockdown(guild, True)
            await self.store.execute(
                "UPDATE security_events SET processed=1 WHERE id=?", (event.id,)
            )
            self.processed += 1

    async def dispatch_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                self.wake.clear()
                await self.process_pending()
                if await self.store.one(
                    "SELECT id FROM security_events WHERE processed=0 LIMIT 1"
                ):
                    continue
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failures += 1
                log.exception("Security evidence worker failed; will retry")
                await asyncio.sleep(1)

    async def report(self, guild, title, description):
        log.warning("%s guild=%s %s", title, guild.id, description.replace("\n", " | "))
        policy, _ = await self.data.policy(guild.id)
        channels = [policy["log_channel_id"]]
        external = os.getenv("SECURITY_ALERT_CHANNEL_ID", "")
        if external.isdigit():
            channels.append(int(external))
        for cid in set(c for c in channels if c):
            channel = self.bot.get_channel(cid)
            if channel:
                try:
                    await asyncio.wait_for(
                        channel.send(
                            embed=embed(title, description[:3500], color=0xED4245),
                            allowed_mentions=discord.AllowedMentions.none(),
                        ),
                        timeout=8,
                    )
                except (discord.HTTPException, asyncio.TimeoutError):
                    log.warning("Security log channel unavailable: %s", cid)

    async def queue_report(self, guild, title, description):
        # Log delivery must never stall evidence processing or containment.
        log.warning("%s guild=%s %s", title, guild.id, description.replace("\n", " | "))
        await self.data.job(
            guild.id, "notify", dict(title=title, description=description), priority=80
        )

    async def fallback(self, guild):
        """Bounded paged reconciliation; gateway events remain the fast path."""
        now = time.time()
        if (
            now - self.last_poll.get(guild.id, 0) < 5
            or self.fallback_locks[guild.id].locked()
        ):
            return
        p, _ = await self.data.policy(guild.id)
        if not p["enabled"] or not guild.me.guild_permissions.view_audit_log:
            return
        async with self.fallback_locks[guild.id]:
            self.last_poll[guild.id] = now
            try:
                cursor = await self.store.one(
                    "SELECT value FROM security_meta WHERE key=?",
                    (f"audit_cursor:{guild.id}",),
                )
                floor = discord.utils.time_snowflake(
                    datetime.now(timezone.utc) - timedelta(seconds=90)
                )
                after = max(floor, int(cursor["value"]) if cursor else floor)
                last = after
                async for entry in guild.audit_logs(
                    limit=200, after=discord.Object(after), oldest_first=True
                ):
                    await self.ingest(entry)
                    last = max(last, entry.id)
                await self.store.execute(
                    "INSERT OR REPLACE INTO security_meta VALUES(?,?)",
                    (f"audit_cursor:{guild.id}", str(last)),
                )
            except discord.HTTPException:
                log.exception("Audit reconciliation failed for %s", guild.id)

    async def contain(self, guild, payload):
        uid = payload["actor_id"]
        ident = payload.get("incident_id")
        if uid in (guild.owner_id, guild.me.id):
            return "Owner and bot are never containment targets"
        policy, _ = await self.data.policy(guild.id)
        if not policy["enabled"] or policy["mode"] != "enforce":
            if ident:
                await self.store.execute(
                    "UPDATE security_incidents SET status='cancelled',result='Protection was disabled or changed to observe' WHERE id=?",
                    (ident,),
                )
            return "Policy no longer permits enforcement"
        async with self.actor_locks[(guild.id, uid)]:
            try:
                member = await guild.fetch_member(uid)
            except discord.NotFound:
                member = None
            if member is not None and member.top_role >= guild.me.top_role:
                raise ValueError(
                    "Cannot contain this member: their highest role is above or equal to mine."
                )
            action = policy["punishment"]
            result = ""
            reason = f'FortuneManager security case #{ident or "regrant"}'
            if member is None:
                # A departed actor can still be banned if the policy explicitly says ban.
                if action == "ban":
                    await guild.ban(
                        discord.Object(uid), reason=reason, delete_message_seconds=0
                    )
                result = "Actor left the server" + (
                    "; ban recorded" if action == "ban" else ""
                )
            elif action == "ban":
                await guild.ban(member, reason=reason, delete_message_seconds=0)
                result = "Banned actor"
            elif action == "kick":
                await guild.kick(member, reason=reason)
                result = "Kicked actor"
            else:
                # Remove all manageable roles: channel overwrites can grant powers
                # even when a role has no dangerous guild-level permission bits.
                roles = [
                    r
                    for r in member.roles
                    if not r.is_default() and not r.managed and r < guild.me.top_role
                ]
                await self.store.execute(
                    "INSERT INTO security_contained VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id) DO NOTHING",
                    (guild.id, uid, json.dumps([r.id for r in roles]), time.time()),
                )
                if roles:
                    await member.edit(
                        roles=[r for r in member.roles if r.managed], reason=reason
                    )
                refreshed = await guild.fetch_member(uid)
                dangerous = bool(refreshed.guild_permissions.value & DANGEROUS)
                if not dangerous:
                    dangerous = any(
                        c.permissions_for(refreshed).value & DANGEROUS
                        for c in guild.channels
                    )
                if dangerous:
                    if guild.me.guild_permissions.ban_members:
                        await guild.ban(
                            refreshed,
                            reason=reason + "; remaining privileges",
                            delete_message_seconds=0,
                        )
                        result = "Banned actor; permissions remained after role removal"
                    else:
                        raise ValueError(
                            "Roles removed, but dangerous permissions remain; Ban Members permission is needed."
                        )
                else:
                    result = f"Removed {len(roles)} roles"
            if ident:
                await self.store.execute(
                    "UPDATE security_incidents SET status='contained',result=? WHERE id=?",
                    (result, ident),
                )
            event = await self.store.one(
                "SELECT * FROM security_events WHERE id=?",
                (payload.get("event_id", 0),),
            )
            if event:
                await self.queue_repair(guild, self.evidence(event))
            await self.queue_report(
                guild,
                "Containment complete",
                f'Case #{ident or "regrant"} · <@{uid}>\n{result}',
            )
            return result

    async def queue_repair(self, guild, event):
        if event.rule == "bot_add" and event.target_id:
            await self.data.job(
                guild.id,
                "remove_bot",
                {"target_id": event.target_id, "automatic": True},
                priority=1,
            )
        if event.rule == "permission_grant" and event.target_id:
            await self.data.job(
                guild.id,
                "revert_grant",
                {"target_id": event.target_id, "automatic": True, **event.detail},
                priority=1,
            )
        if event.rule == "ban" and event.target_id:
            await self.data.job(
                guild.id,
                "unban",
                {"target_id": event.target_id, "automatic": True},
                priority=25,
            )
        if event.rule in ("channel_delete", "role_delete"):
            try:
                snap = await self.recovery.get(guild.id)
            except ValueError:
                return
            kind = event.rule.split("_")[0]
            if any(x["id"] == event.target_id for x in snap["data"][kind + "s"]):
                await self.data.job(
                    guild.id,
                    "restore",
                    dict(
                        snapshot_id=snap["id"],
                        kind=kind,
                        old_id=event.target_id,
                        mode="missing",
                        automatic=True,
                    ),
                )

    async def run_job(self, job):
        guild = self.bot.get_guild(job["guild_id"])
        if guild is None:
            raise ValueError("Bot is no longer in this server.")
        p = json.loads(job["payload"])
        kind = job["kind"]
        if p.get("automatic"):
            policy, _ = await self.data.policy(guild.id)
            if not policy["enabled"] or policy["mode"] != "enforce":
                return "Cancelled: automatic enforcement is off"
        if kind == "notify":
            await self.report(guild, p["title"], p["description"])
            return "Log delivery attempted"
        if kind == "contain":
            return await self.contain(guild, p)
        if kind == "restore":
            return await self.recovery.restore(guild, p)
        if kind == "restore_settings":
            return await self.recovery.settings(guild, p)
        if kind in ("lock_role", "unlock_role"):
            role = guild.get_role(p["role_id"])
            if role:
                if role.managed or role >= guild.me.top_role:
                    raise ValueError("This role cannot be managed.")
                await role.edit(
                    permissions=discord.Permissions(p["permissions"]),
                    reason="FortuneManager security lockdown",
                )
            if kind == "unlock_role":
                await self.store.execute(
                    "DELETE FROM security_lockdown WHERE guild_id=? AND role_id=?",
                    (guild.id, p["role_id"]),
                )
            return "Role permissions updated"
        target = guild.get_member(p.get("target_id", 0))
        if kind == "remove_bot":
            if target and target.bot and target.id not in (guild.owner_id, guild.me.id):
                if target.top_role >= guild.me.top_role:
                    raise ValueError("Added bot is above my role.")
                await guild.kick(
                    target, reason="FortuneManager: unauthorized bot addition"
                )
        elif kind == "unban":
            try:
                await guild.unban(
                    discord.Object(p["target_id"]),
                    reason="FortuneManager: undo incident ban",
                )
            except discord.NotFound:
                pass
        elif kind == "revert_grant":
            if p["action"] == "member_role_update":
                if target and target.id not in (guild.owner_id, guild.me.id):
                    roles = [
                        r
                        for rid in p.get("role_ids", [])
                        if (r := guild.get_role(rid))
                        and not r.managed
                        and r < guild.me.top_role
                    ]
                    if roles:
                        await target.remove_roles(
                            *roles,
                            reason="FortuneManager: undo unauthorized role grant",
                        )
            else:
                role = guild.get_role(p["target_id"])
                if role and not role.managed and role < guild.me.top_role:
                    value = p.get("before_permissions")
                    if value is not None:
                        added = (p.get("after_permissions") or 0) & ~value
                        await role.edit(
                            permissions=discord.Permissions(
                                role.permissions.value & ~added
                            ),
                            reason="FortuneManager: undo unauthorized permission grant",
                        )
        else:
            raise ValueError(f"Unknown security job {kind}")
        return "Completed"

    async def execute_job(self, job):
        try:
            result = await self.run_job(job)
            await self.store.execute(
                "UPDATE security_jobs SET status='done',error=? WHERE id=?",
                (str(result)[:1000], job["id"]),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            transient = (
                isinstance(error, (asyncio.TimeoutError, OSError))
                or isinstance(error, discord.HTTPException)
                and (error.status == 429 or error.status >= 500)
            )
            retry = transient and job["attempts"] < 4
            delay = min(60, 2 ** (job["attempts"] + 1))
            # discord.py handles bucket/global Retry-After before raising. A
            # propagated rate-limit response still gets a bounded delayed retry.
            if isinstance(error, discord.HTTPException) and error.status == 429:
                try:
                    delay = max(
                        delay, float(error.response.headers.get("Retry-After", delay))
                    )
                except (AttributeError, TypeError, ValueError):
                    pass
            await self.store.execute(
                "UPDATE security_jobs SET status=?,available=?,error=? WHERE id=?",
                (
                    "pending" if retry else "failed",
                    time.time() + delay,
                    f"{type(error).__name__}: {error}"[:1000],
                    job["id"],
                ),
            )
            if not retry:
                self.failures += 1
                p = json.loads(job["payload"])
                if p.get("incident_id"):
                    await self.store.execute(
                        "UPDATE security_incidents SET status='failed',result=? WHERE id=?",
                        (str(error)[:1000], p["incident_id"]),
                    )
                guild = self.bot.get_guild(job["guild_id"])
                if guild and job["kind"] != "notify":
                    await self.queue_report(
                        guild,
                        "Security action failed",
                        f'Job #{job["id"]} · {job["kind"]}\n{str(error)[:1500]}',
                    )

    async def job_loop(self, containment):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                job = await self.data.take_job(containment)
                if job:
                    await self.execute_job(job)
                else:
                    await asyncio.sleep(0.25 if containment else 1)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Security job worker failed; will retry")
                await asyncio.sleep(1)

    def health(self, guild):
        required = (
            "view_audit_log",
            "manage_roles",
            "ban_members",
            "kick_members",
            "manage_channels",
        )
        missing = [
            p.replace("_", " ")
            for p in required
            if not getattr(guild.me.guild_permissions, p)
        ]
        above = [
            r.name
            for r in guild.roles
            if r >= guild.me.top_role
            and r.id != guild.me.top_role.id
            and r.permissions.value & DANGEROUS
        ]
        return {
            "missing_permissions": missing,
            "unmanageable_roles": above,
            "last_audit": self.last_audit.get(guild.id),
            "worker_failures": self.failures,
            "workers_running": bool(self.tasks)
            and all(not t.done() for t in self.tasks),
        }

    async def maintenance_loop(self):
        await self.bot.wait_until_ready()
        cleaned = 0
        while not self.bot.is_closed():
            for guild in list(self.bot.guilds):
                try:
                    p, _ = await self.data.policy(guild.id)
                    if not p["enabled"]:
                        continue
                    await self.fallback(guild)
                    health = self.health(guild)
                    if (
                        health["missing_permissions"] or health["unmanageable_roles"]
                    ) and time.time() - self.last_warning.get(guild.id, 0) > 900:
                        self.last_warning[guild.id] = time.time()
                        await self.queue_report(
                            guild,
                            "Protection needs attention",
                            "Missing permissions: "
                            + (", ".join(health["missing_permissions"]) or "None")
                            + "\nRoles above the bot: "
                            + (", ".join(health["unmanageable_roles"]) or "None"),
                        )
                    latest = await self.store.one(
                        "SELECT at FROM security_snapshots WHERE guild_id=? ORDER BY id DESC LIMIT 1",
                        (guild.id,),
                    )
                    if not latest or latest["at"] < time.time() - 21600:
                        try:
                            await self.recovery.snapshot(guild, "Automatic baseline")
                        except ValueError:
                            pass
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Security maintenance failed for %s", guild.id)
            if time.time() - cleaned > 3600:
                cleaned = time.time()
                await self.store.execute(
                    "DELETE FROM security_events WHERE at<? AND processed=1",
                    (cleaned - 172800,),
                )
                await self.store.execute(
                    "DELETE FROM security_jobs WHERE status='done' AND id NOT IN (SELECT id FROM security_jobs ORDER BY id DESC LIMIT 10000)"
                )
            await asyncio.sleep(20)
