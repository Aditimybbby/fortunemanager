"""One-time import path for pre-core antinuke and automod settings."""
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
    for filename in ('anti.db', 'automod.db'):
        source = settings.ROOT / 'db' / filename
        if not source.exists():
            continue
        target = Path(legacy_path(source.name))
        if not target.exists():
            with sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True) as original:
                with sqlite3.connect(target) as destination:
                    original.backup(destination)
