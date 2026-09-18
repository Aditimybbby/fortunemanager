"""Writable, persistent storage for the restored Olympus modules."""
import os
import sqlite3
from pathlib import Path
from . import settings


def legacy_directory():
    directory = Path(os.getenv('LEGACY_DATA_DIR', str(settings.DATA_DIR / 'legacy'))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def legacy_path(filename):
    return str(Path(legacy_directory()) / filename)


def prepare_databases():
    # Copy an existing database once. Never replace a persisted database on restart.
    for source in (settings.ROOT / 'db').glob('*.db'):
        target = Path(legacy_path(source.name))
        if not target.exists():
            with sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True) as original:
                with sqlite3.connect(target) as destination:
                    original.backup(destination)
    # Checks are shared by many cogs, even when their configuration cog is disabled.
    schemas = {
        'block.db': '''CREATE TABLE IF NOT EXISTS user_blacklist (user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS guild_blacklist (guild_id INTEGER PRIMARY KEY);''',
        'ignore.db': '''CREATE TABLE IF NOT EXISTS ignored_commands (guild_id INTEGER, command_name TEXT);
            CREATE TABLE IF NOT EXISTS ignored_channels (guild_id INTEGER, channel_id INTEGER);
            CREATE TABLE IF NOT EXISTS ignored_users (guild_id INTEGER, user_id INTEGER);
            CREATE TABLE IF NOT EXISTS bypassed_users (guild_id INTEGER, user_id INTEGER);''',
        'topcheck.db': '''CREATE TABLE IF NOT EXISTS topcheck (guild_id INTEGER PRIMARY KEY, enabled INTEGER);''',
        'anti.db': '''CREATE TABLE IF NOT EXISTS extraowners (guild_id INTEGER PRIMARY KEY, owner_id INTEGER);
            CREATE TABLE IF NOT EXISTS antinuke (guild_id INTEGER PRIMARY KEY, status BOOLEAN);
            CREATE TABLE IF NOT EXISTS whitelisted_users (
                guild_id INTEGER, user_id INTEGER, ban BOOLEAN DEFAULT FALSE,
                kick BOOLEAN DEFAULT FALSE, prune BOOLEAN DEFAULT FALSE, botadd BOOLEAN DEFAULT FALSE,
                serverup BOOLEAN DEFAULT FALSE, memup BOOLEAN DEFAULT FALSE, chcr BOOLEAN DEFAULT FALSE,
                chdl BOOLEAN DEFAULT FALSE, chup BOOLEAN DEFAULT FALSE, rlcr BOOLEAN DEFAULT FALSE,
                rlup BOOLEAN DEFAULT FALSE, rldl BOOLEAN DEFAULT FALSE, meneve BOOLEAN DEFAULT FALSE,
                mngweb BOOLEAN DEFAULT FALSE, mngstemo BOOLEAN DEFAULT FALSE,
                PRIMARY KEY(guild_id, user_id));''',
    }
    for filename, schema in schemas.items():
        with sqlite3.connect(legacy_path(filename)) as connection:
            connection.executescript(schema)
