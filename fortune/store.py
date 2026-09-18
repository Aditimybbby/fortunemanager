"""Short SQLite transactions; all async access is serialized off the event loop."""

import asyncio
import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .settings import DEFAULT_PREFIX

DEFAULT_CONFIG = {
    "prefix": DEFAULT_PREFIX,
    "staff_role_id": None,
    "log_channel_id": None,
    "greet": {
        "enabled": False,
        "channel_id": None,
        "content": "Welcome {user} to {server}!",
        "use_embed": True,
        "title": "Welcome to {server}",
        "description": "Hey {user}, you are member #{member_count}.",
        "color": "#63d6ac",
        "image": "",
        "autorole_ids": [],
    },
    "ticket": {
        "enabled": False,
        "category_id": None,
        "log_channel_id": None,
        "support_role_ids": [],
        "max_open": 1,
        "panels": [],
    },
    "embeds": {
        "channel_id": None,
        "content": "",
        "title": "An update from the team",
        "description": "Share something with your community.",
        "color": "#63d6ac",
        "image": "",
        "fields": [],
    },
    "reaction_roles": [],
}

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS config(guild_id INTEGER PRIMARY KEY, data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS staff(guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, permissions TEXT NOT NULL,
 role_id INTEGER, actor_id INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 owner_id INTEGER NOT NULL, channel_id INTEGER UNIQUE, panel_id TEXT NOT NULL, option_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'creating', claimed_by INTEGER, created_at TEXT NOT NULL, closed_at TEXT);
CREATE TABLE IF NOT EXISTS ticket_panels(message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channel_locks(guild_id INTEGER,channel_id INTEGER, previous TEXT NOT NULL, PRIMARY KEY(guild_id,channel_id));
CREATE TABLE IF NOT EXISTS warnings(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER,actor_id INTEGER,reason TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,actor_id INTEGER,action TEXT,detail TEXT,created_at TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def merge_defaults(data):
    result = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in data.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k].update(v)
        else:
            result[k] = v
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(lambda c: c.executescript(SCHEMA))

    async def _run(self, operation):
        def run():
            with sqlite3.connect(self.path, timeout=15) as conn:
                conn.row_factory = sqlite3.Row
                return operation(conn)

        async with self.lock:
            return await asyncio.to_thread(run)

    async def execute(self, sql, args=()):
        return await self._run(lambda c: c.execute(sql, args).lastrowid)

    async def rows(self, sql, args=()):
        return await self._run(
            lambda c: [dict(r) for r in c.execute(sql, args).fetchall()]
        )

    async def one(self, sql, args=()):
        rows = await self.rows(sql, args)
        return rows[0] if rows else None

    async def config(self, guild_id):
        row = await self.one(
            "SELECT data,version FROM config WHERE guild_id=?", (guild_id,)
        )
        return merge_defaults(json.loads(row["data"]) if row else {}), row[
            "version"
        ] if row else 0

    async def save_config(self, guild_id, data, expected_version):
        def save(c):
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT version FROM config WHERE guild_id=?", (guild_id,)
            ).fetchone()
            current = row["version"] if row else 0
            if current != expected_version:
                raise ValueError(
                    "Settings changed in another tab. Reload before saving."
                )
            c.execute(
                "INSERT INTO config VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data,version=excluded.version",
                (guild_id, json.dumps(data), current + 1),
            )
            return current + 1

        return await self._run(save)

    async def staff(self, guild_id, user_id):
        row = await self.one(
            "SELECT * FROM staff WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        if row:
            row["permissions"] = json.loads(row["permissions"])
        return row

    async def save_staff(self, guild_id, user_id, permissions, role_id, actor_id):
        await self.execute(
            "INSERT INTO staff VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET permissions=excluded.permissions,role_id=excluded.role_id,actor_id=excluded.actor_id,updated_at=excluded.updated_at",
            (
                guild_id,
                user_id,
                json.dumps(sorted(permissions)),
                role_id,
                actor_id,
                now(),
            ),
        )

    async def audit(self, guild_id, actor_id, action, detail):
        await self.execute(
            "INSERT INTO audit(guild_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?)",
            (guild_id, actor_id, action, str(detail)[:2000], now()),
        )
