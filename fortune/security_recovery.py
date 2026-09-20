"""Versioned server structure snapshots and incremental, resumable recovery."""

import json
import time
import discord
from .security_policy import DANGEROUS


def role_data(role):
    return dict(
        id=role.id,
        name=role.name,
        permissions=role.permissions.value,
        color=role.colour.value,
        hoist=role.hoist,
        mentionable=role.mentionable,
        position=role.position,
        managed=role.managed,
        default=role.is_default(),
    )


def channel_data(channel):
    data = dict(
        id=channel.id,
        name=channel.name,
        type=channel.type.value,
        position=channel.position,
        category_id=channel.category_id,
        overwrites=[],
    )
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        data["overwrites"].append(
            dict(
                id=target.id,
                role=isinstance(target, discord.Role),
                allow=allow.value,
                deny=deny.value,
            )
        )
    if data["type"] == 4:
        return data
    for name in (
        "topic",
        "slowmode_delay",
        "nsfw",
        "bitrate",
        "user_limit",
        "rtc_region",
        "default_auto_archive_duration",
        "default_thread_slowmode_delay",
    ):
        if hasattr(channel, name):
            data[name] = getattr(channel, name)
    if isinstance(channel, discord.ForumChannel):
        data["tags"] = [
            dict(
                name=t.name,
                moderated=t.moderated,
                emoji=str(t.emoji) if t.emoji else None,
            )
            for t in channel.available_tags
        ]
    return data


class Recovery:
    def __init__(self, bot, data):
        self.bot, self.data, self.store = bot, data, data.store

    async def snapshot(self, guild, label="Manual snapshot"):
        active = await self.store.one(
            "SELECT id FROM security_incidents WHERE guild_id=? AND resolved=0 LIMIT 1",
            (guild.id,),
        )
        if active:
            raise ValueError(
                "Resolve open security incidents before saving a new baseline."
            )
        data = dict(
            name=guild.name,
            verification_level=guild.verification_level.value,
            default_notifications=guild.default_notifications.value,
            explicit_content_filter=guild.explicit_content_filter.value,
            roles=[role_data(r) for r in guild.roles],
            channels=[channel_data(c) for c in guild.channels],
        )
        ident = await self.store.execute(
            "INSERT INTO security_snapshots(guild_id,at,data,label) VALUES(?,?,?,?)",
            (guild.id, time.time(), json.dumps(data), label[:100]),
        )
        # Retain every snapshot referenced by unfinished work, and the newest 20.
        await self.store.execute(
            "DELETE FROM security_snapshots WHERE guild_id=? AND id NOT IN "
            "(SELECT id FROM security_snapshots WHERE guild_id=? ORDER BY id DESC LIMIT 20) "
            "AND id NOT IN (SELECT json_extract(payload,'$.snapshot_id') FROM security_jobs WHERE status != 'done' AND json_extract(payload,'$.snapshot_id') IS NOT NULL)",
            (guild.id, guild.id),
        )
        return ident

    async def get(self, guild_id, snapshot_id=None):
        sql = "SELECT * FROM security_snapshots WHERE guild_id=?"
        args = [guild_id]
        if snapshot_id is not None:
            sql += " AND id=?"
            args.append(snapshot_id)
        row = await self.store.one(sql + " ORDER BY id DESC LIMIT 1", args)
        if not row:
            raise ValueError(
                "No matching snapshot. Run `antinuke backup create` first."
            )
        row["data"] = json.loads(row["data"])
        return row

    async def mappings(self, snapshot_id):
        return {
            r["old_id"]: r["new_id"]
            for r in await self.store.rows(
                "SELECT * FROM security_objects WHERE snapshot_id=?", (snapshot_id,)
            )
        }

    async def plan(self, guild, snapshot_id=None):
        snap = await self.get(guild.id, snapshot_id)
        mapped = await self.mappings(snap["id"])
        result = []
        for role in snap["data"]["roles"]:
            if role["managed"]:
                continue
            current = guild.get_role(mapped.get(role["id"], role["id"]))
            if current is None:
                result.append(("role", role["id"], "missing", role["name"]))
            elif any(
                role[k] != role_data(current)[k]
                for k in (
                    "permissions",
                    "color",
                    "name",
                    "hoist",
                    "mentionable",
                    "position",
                )
            ):
                result.append(("role", role["id"], "changed", role["name"]))
        for channel in snap["data"]["channels"]:
            current = guild.get_channel(mapped.get(channel["id"], channel["id"]))
            if current is None:
                result.append(("channel", channel["id"], "missing", channel["name"]))
            else:
                current_data = channel_data(current)
                wanted = dict(channel)
                wanted["id"] = current.id
                wanted["category_id"] = mapped.get(
                    wanted["category_id"], wanted["category_id"]
                )
                wanted["overwrites"] = [
                    {**o, "id": mapped.get(o["id"], o["id"])}
                    for o in wanted["overwrites"]
                ]
                if current_data != wanted:
                    result.append(
                        ("channel", channel["id"], "changed", channel["name"])
                    )
        return snap, result

    async def enqueue(self, guild, snapshot_id, mode="missing"):
        snap, plan = await self.plan(guild, snapshot_id)
        if mode not in ("missing", "full"):
            raise ValueError("Recovery mode must be missing or full.")
        # Category creation precedes child channels; roles precede overwrites.
        cats = {c["id"] for c in snap["data"]["channels"] if c["type"] == 4}
        plan.sort(
            key=lambda item: (
                0 if item[0] == "role" else 1 if item[1] in cats else 2,
                item[1],
            )
        )
        count = 0
        for kind, oid, state, _ in plan:
            if mode == "missing" and state != "missing":
                continue
            await self.data.job(
                guild.id,
                "restore",
                dict(snapshot_id=snap["id"], kind=kind, old_id=oid, mode=mode),
                priority=30 if kind == "role" else 40 if oid in cats else 50,
            )
            count += 1
        if mode == "full":
            await self.data.job(
                guild.id, "restore_settings", dict(snapshot_id=snap["id"]), priority=60
            )
        return count

    async def restore(self, guild, payload):
        snap = await self.get(guild.id, payload["snapshot_id"])
        mapped = await self.mappings(snap["id"])
        oid = payload["old_id"]
        kind = payload["kind"]
        source = next((x for x in snap["data"][kind + "s"] if x["id"] == oid), None)
        if not source:
            raise ValueError("Object is not present in the snapshot.")
        current = (guild.get_role if kind == "role" else guild.get_channel)(
            mapped.get(oid, oid)
        )
        reason = f'FortuneManager recovery {snap["id"]}/{oid}'
        if current is None:
            # Reconcile the API-success / DB-commit crash window before creating.
            action = (
                discord.AuditLogAction.role_create
                if kind == "role"
                else discord.AuditLogAction.channel_create
            )
            async for entry in guild.audit_logs(limit=100, action=action):
                if entry.reason == reason and entry.user_id == guild.me.id:
                    current = (guild.get_role if kind == "role" else guild.get_channel)(
                        entry.target.id
                    )
                    if current:
                        break
        if current is not None and payload.get("mode", "missing") == "missing":
            await self.store.execute(
                "INSERT OR REPLACE INTO security_objects VALUES(?,?,?)",
                (snap["id"], oid, current.id),
            )
            return "Already present"
        if kind == "role":
            if source["managed"]:
                return "Skipped managed role"
            if source["default"]:
                await guild.default_role.edit(
                    permissions=discord.Permissions(source["permissions"]),
                    reason=reason,
                )
                return "Restored default role permissions"
            fields = {k: source[k] for k in ("name", "hoist", "mentionable")}
            fields.update(
                permissions=discord.Permissions(source["permissions"]),
                colour=discord.Colour(source["color"]),
            )
            if current:
                if current >= guild.me.top_role:
                    raise ValueError("Role is above the bot.")
                await current.edit(**fields, reason=reason)
            else:
                current = await guild.create_role(**fields, reason=reason)
            # Commit the new identity before another API call can fail.
            await self.store.execute(
                "INSERT OR REPLACE INTO security_objects VALUES(?,?,?)",
                (snap["id"], oid, current.id),
            )
            await current.edit(
                position=min(source["position"], guild.me.top_role.position - 1),
                reason=reason,
            )
            return f"Restored role {current.id}"
        overwrites = {}
        for overwrite in source["overwrites"]:
            tid = mapped.get(overwrite["id"], overwrite["id"])
            target = guild.get_role(tid) if overwrite["role"] else guild.get_member(tid)
            if target is None:
                if overwrite["role"]:
                    # Dropping a deny overwrite can expose a private channel.
                    raise ValueError(
                        f'Restore role {overwrite["id"]} before this channel.'
                    )
                target = discord.Object(tid, type=discord.Member)
            overwrites[target] = discord.PermissionOverwrite.from_pair(
                discord.Permissions(overwrite["allow"]),
                discord.Permissions(overwrite["deny"]),
            )
        parent = source["category_id"]
        category = guild.get_channel(mapped.get(parent, parent)) if parent else None
        if parent and category is None:
            raise ValueError("Restore the parent category first.")
        fields = dict(
            name=source["name"], position=source["position"], overwrites=overwrites
        )
        if source["type"] != 4:
            fields["category"] = category
        for key in (
            "topic",
            "slowmode_delay",
            "nsfw",
            "bitrate",
            "user_limit",
            "rtc_region",
            "default_auto_archive_duration",
            "default_thread_slowmode_delay",
        ):
            if key in source:
                fields[key] = source[key]
        if "bitrate" in fields:
            fields["bitrate"] = min(fields["bitrate"], guild.bitrate_limit)
        if "tags" in source:
            fields["available_tags"] = [
                discord.ForumTag(
                    name=t["name"], moderated=t["moderated"], emoji=t["emoji"]
                )
                for t in source["tags"]
            ]
        if current:
            await current.edit(**fields, reason=reason)
        else:
            create = {
                0: guild.create_text_channel,
                5: guild.create_text_channel,
                2: guild.create_voice_channel,
                4: guild.create_category,
                13: guild.create_stage_channel,
                15: guild.create_forum,
                16: guild.create_forum,
            }.get(source["type"])
            if not create:
                raise ValueError(
                    f'Channel type {source["type"]} is not supported for restoration.'
                )
            if source["type"] == 5:
                fields["news"] = True
            if source["type"] == 16:
                fields["media"] = True
            current = await create(**fields, reason=reason)
        await self.store.execute(
            "INSERT OR REPLACE INTO security_objects VALUES(?,?,?)",
            (snap["id"], oid, current.id),
        )
        return f"Restored channel {current.id}"

    async def settings(self, guild, payload):
        data = (await self.get(guild.id, payload["snapshot_id"]))["data"]
        await guild.edit(
            name=data["name"],
            verification_level=discord.VerificationLevel(data["verification_level"]),
            default_notifications=discord.NotificationLevel(
                data["default_notifications"]
            ),
            explicit_content_filter=discord.ContentFilter(
                data["explicit_content_filter"]
            ),
            reason="FortuneManager recovery",
        )
        return "Restored server settings"

    async def lockdown(self, guild, enable):
        results = []
        if enable:
            for role in guild.roles:
                if (
                    role.managed
                    or role >= guild.me.top_role
                    or not role.permissions.value & DANGEROUS
                ):
                    continue
                locked = role.permissions.value & ~DANGEROUS
                await self.store.execute(
                    "INSERT OR IGNORE INTO security_lockdown VALUES(?,?,?,?)",
                    (guild.id, role.id, role.permissions.value, locked),
                )
                await self.data.job(
                    guild.id,
                    "lock_role",
                    dict(role_id=role.id, permissions=locked),
                    priority=1,
                )
                results.append(role.id)
        else:
            await self.store.execute(
                "UPDATE security_jobs SET status='cancelled' WHERE guild_id=? AND kind='lock_role' AND status='pending'",
                (guild.id,),
            )
            for row in await self.store.rows(
                "SELECT * FROM security_lockdown WHERE guild_id=?", (guild.id,)
            ):
                role = guild.get_role(row["role_id"])
                if role and role.permissions.value == row["permissions"]:
                    await self.store.execute(
                        "DELETE FROM security_lockdown WHERE guild_id=? AND role_id=?",
                        (guild.id, row["role_id"]),
                    )
                    continue
                if role and role.permissions.value != row["locked"]:
                    raise ValueError(
                        f"Role {role.name} changed after lockdown. Review its permissions manually."
                    )
                if role:
                    await self.data.job(
                        guild.id,
                        "unlock_role",
                        dict(role_id=role.id, permissions=row["permissions"]),
                        priority=20,
                    )
                    results.append(role.id)
                else:
                    await self.store.execute(
                        "DELETE FROM security_lockdown WHERE guild_id=? AND role_id=?",
                        (guild.id, row["role_id"]),
                    )
        return len(results)
