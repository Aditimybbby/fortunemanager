"""Durable security configuration, evidence, incidents, and retryable work."""

import copy
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from .security_policy import default_policy, validate_policy, RULES, ALIASES
from .legacy_storage import legacy_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS security_config(guild_id INTEGER PRIMARY KEY,data TEXT NOT NULL,version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS security_events(id INTEGER PRIMARY KEY,guild_id INTEGER,actor_id INTEGER,target_id INTEGER,rule TEXT,at REAL,detail TEXT,processed INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS security_pending ON security_events(processed,at);
CREATE INDEX IF NOT EXISTS security_history ON security_events(guild_id,at);
CREATE TABLE IF NOT EXISTS security_incidents(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,actor_id INTEGER,event_id INTEGER,rule TEXT,reason TEXT,status TEXT,at REAL,result TEXT DEFAULT '',resolved INTEGER DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS security_incident_event ON security_incidents(guild_id,actor_id,event_id);
CREATE TABLE IF NOT EXISTS security_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,kind TEXT,payload TEXT,priority INTEGER,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,available REAL DEFAULT 0,error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS security_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,at REAL,data TEXT,label TEXT);
CREATE TABLE IF NOT EXISTS security_objects(snapshot_id INTEGER,old_id INTEGER,new_id INTEGER,PRIMARY KEY(snapshot_id,old_id));
CREATE TABLE IF NOT EXISTS security_contained(guild_id INTEGER,user_id INTEGER,roles TEXT,at REAL,PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS security_lockdown(guild_id INTEGER,role_id INTEGER,permissions INTEGER,locked INTEGER,PRIMARY KEY(guild_id,role_id));
CREATE TABLE IF NOT EXISTS security_confirm(guild_id INTEGER PRIMARY KEY,user_id INTEGER,digest TEXT,expires REAL);
CREATE TABLE IF NOT EXISTS automod_config(guild_id INTEGER PRIMARY KEY,data TEXT NOT NULL,version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS security_meta(key TEXT PRIMARY KEY,value TEXT);
"""


class SecurityData:
    def __init__(self, store):
        self.store = store
        self.cache = {}

    async def init(self):
        await self.store._run(lambda c: c.executescript(SCHEMA))
        # An interrupted request is retryable. All worker operations are idempotent
        # where Discord permits; recovery reconciles objects before retrying.
        await self.store.execute(
            "UPDATE security_jobs SET status='pending' WHERE status='running'"
        )

    async def policy(self, guild_id):
        if guild_id not in self.cache:
            row = await self.store.one(
                "SELECT * FROM security_config WHERE guild_id=?", (guild_id,)
            )
            self.cache[guild_id] = (
                validate_policy(json.loads(row["data"])) if row else default_policy(),
                row["version"] if row else 0,
            )
        policy, version = self.cache[guild_id]
        return copy.deepcopy(policy), version

    async def save(self, guild_id, data, version, actor_id, confirmation=None):
        clean = validate_policy(data)

        def update(c):
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT version FROM security_config WHERE guild_id=?", (guild_id,)
            ).fetchone()
            if (row["version"] if row else 0) != version:
                raise ValueError("Security settings changed. Reload and try again.")
            if confirmation is not None:
                row = c.execute(
                    "SELECT * FROM security_confirm WHERE guild_id=?", (guild_id,)
                ).fetchone()
                digest = hashlib.sha256(str(confirmation).encode()).hexdigest()
                if (
                    not row
                    or row["user_id"] != actor_id
                    or row["expires"] < time.time()
                    or not secrets.compare_digest(row["digest"], digest)
                ):
                    raise ValueError(
                        "Run `antinuke dashboard` in Discord for a new confirmation code."
                    )
                c.execute("DELETE FROM security_confirm WHERE guild_id=?", (guild_id,))
            c.execute(
                "INSERT INTO security_config VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data,version=excluded.version",
                (guild_id, json.dumps(clean), version + 1),
            )
            c.execute(
                "INSERT INTO audit(guild_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,datetime('now'))",
                (
                    guild_id,
                    actor_id,
                    "security.config",
                    f'Policy version {version+1}; {clean["mode"]}; enabled={clean["enabled"]}',
                ),
            )

        await self.store._run(update)
        self.cache[guild_id] = (copy.deepcopy(clean), version + 1)
        return version + 1

    async def issue_code(self, guild_id, user_id):
        code = secrets.token_urlsafe(18)
        await self.store.execute(
            "INSERT OR REPLACE INTO security_confirm VALUES(?,?,?,?)",
            (
                guild_id,
                user_id,
                hashlib.sha256(code.encode()).hexdigest(),
                time.time() + 300,
            ),
        )
        return code

    async def record(self, event):
        return await self.store._run(
            lambda c: c.execute(
                "INSERT OR IGNORE INTO security_events(id,guild_id,actor_id,target_id,rule,at,detail) VALUES(?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.guild_id,
                    event.actor_id,
                    event.target_id,
                    event.rule,
                    event.timestamp,
                    json.dumps(event.detail),
                ),
            ).rowcount
        )

    async def incident(self, event, reason, observe=False):
        """Create incident and containment work together, never in separate commits."""

        def create(c):
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT id FROM security_incidents WHERE guild_id=? AND actor_id=? AND at>? AND status IN ('queued','contained','partial') AND resolved=0",
                (event.guild_id, event.actor_id, time.time() - 30),
            ).fetchone()
            if previous:
                return None
            cursor = c.execute(
                "INSERT OR IGNORE INTO security_incidents(guild_id,actor_id,event_id,rule,reason,status,at) VALUES(?,?,?,?,?,?,?)",
                (
                    event.guild_id,
                    event.actor_id,
                    event.id,
                    event.rule,
                    reason,
                    "observed" if observe else "queued",
                    time.time(),
                ),
            )
            if not cursor.rowcount:
                return None
            ident = cursor.lastrowid
            if not observe:
                c.execute(
                    "INSERT INTO security_jobs(guild_id,kind,payload,priority) VALUES(?,?,?,0)",
                    (
                        event.guild_id,
                        "contain",
                        json.dumps(
                            dict(
                                incident_id=ident,
                                actor_id=event.actor_id,
                                event_id=event.id,
                            )
                        ),
                    ),
                )
            return ident

        return await self.store._run(create)

    async def job(self, guild_id, kind, payload, priority=50):
        return await self.store.execute(
            "INSERT INTO security_jobs(guild_id,kind,payload,priority) VALUES(?,?,?,?)",
            (guild_id, kind, json.dumps(payload), priority),
        )

    async def take_job(self, containment):
        def take(c):
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM security_jobs WHERE status='pending' AND available<=? AND (priority<10)=? ORDER BY priority,id LIMIT 1",
                (time.time(), int(containment)),
            ).fetchone()
            if not row:
                return None
            c.execute(
                "UPDATE security_jobs SET status='running',attempts=attempts+1 WHERE id=?",
                (row["id"],),
            )
            return dict(row)

        return await self.store._run(take)

    async def import_legacy(self):
        if await self.store.one(
            "SELECT value FROM security_meta WHERE key='legacy_import_v1'"
        ):
            return

        # Only read existing persisted databases. Do not enable any new server.
        def read(filename, table):
            path = Path(legacy_path(filename))
            if not path.exists():
                return []
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as c:
                c.row_factory = sqlite3.Row
                if not c.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone():
                    return []
                return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]

        import asyncio

        antinuke, trusted, automod, punishments, ignored, logs = (
            await asyncio.to_thread(
                lambda: (
                    read("anti.db", "antinuke"),
                    read("anti.db", "whitelisted_users"),
                    read("automod.db", "automod"),
                    read("automod.db", "automod_punishments"),
                    read("automod.db", "automod_ignored"),
                    read("automod.db", "automod_logging"),
                )
            )
        )
        from .automod import default_config

        configs = {}
        for row in antinuke:
            p = default_policy()
            p["enabled"] = bool(row["status"])
            # Preserve the archived strict enforcement for existing enabled servers.
            for r in p["rules"].values():
                r["count"] = 1
            configs[row["guild_id"]] = p
        for row in trusted:
            p = configs.setdefault(row["guild_id"], default_policy())
            grants = {}
            for key, value in row.items():
                if key in ("guild_id", "user_id") or not value:
                    continue
                names = (
                    [x for x in RULES if x.startswith("webhook_")]
                    if key == "mngweb"
                    else (
                        ["emoji_delete", "sticker_delete"]
                        if key == "mngstemo"
                        else [ALIASES.get(key, key)]
                    )
                )
                for name in names:
                    if name in RULES:
                        grants[name] = {"budget": 100, "expires": 0}
            p["trust"][str(row["user_id"])] = grants
        names = {
            "Anti spam": "spam",
            "Anti caps": "caps",
            "Anti link": "links",
            "Anti invites": "invites",
            "Anti invite": "invites",
            "Anti mass mention": "mentions",
            "Anti emoji spam": "emoji",
        }
        am = {r["guild_id"]: default_config() for r in automod}
        for r in automod:
            am[r["guild_id"]]["enabled"] = bool(r["enabled"])
        for r in punishments:
            name = names.get(r["event"])
            if name and r["guild_id"] in am:
                p = am[r["guild_id"]]["rules"][name]
                p["enabled"] = True
                p["action"] = {
                    "mute": "timeout",
                    "ban": "ban",
                    "kick": "kick",
                    "warn": "warn",
                    "delete": "delete",
                }.get(r["punishment"].lower(), "timeout")
        for r in ignored:
            if r["guild_id"] in am and r["type"] in ("channel", "role"):
                am[r["guild_id"]]["ignored_" + r["type"] + "s"].append(r["id"])
        for r in logs:
            if r["guild_id"] in am:
                am[r["guild_id"]]["log_channel_id"] = r["log_channel"]

        def save(c):
            for gid, p in configs.items():
                c.execute(
                    "INSERT OR IGNORE INTO security_config VALUES(?,?,1)",
                    (gid, json.dumps(p)),
                )
            for gid, p in am.items():
                c.execute(
                    "INSERT OR IGNORE INTO automod_config VALUES(?,?,1)",
                    (gid, json.dumps(p)),
                )
            c.execute(
                "INSERT OR REPLACE INTO security_meta VALUES('legacy_import_v1','1')"
            )

        await self.store._run(save)
        self.cache.clear()
