"""Load the original server features alongside the FortuneManager services."""
import asyncio
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import aiohttp
import aiosqlite
from discord.ext import tasks
from .legacy_storage import prepare_databases

log = logging.getLogger(__name__)
# Retain current help/error handling and deployment-owner controls. The archived
# global-control cogs contain another deployment's owner IDs and side effects.
REPLACED = {'Help', 'Owner', 'Global', 'Badges', 'NoPrefix', 'Block',
            'AutoBlacklist', 'Errors', 'Mention', 'Guild', 'Autorole', 'React'}


async def cleanup(cog):
    """Close DB handles and tasks that the original cogs did not clean up."""
    pending = []
    for name in dir(type(cog)):
        if isinstance(getattr(type(cog), name, None), tasks.Loop):
            loop = getattr(cog, name)
            task = loop.get_task()
            loop.cancel()
            if task:
                pending.append(task)
    for value in vars(cog).values():
        if isinstance(value, asyncio.Task):
            value.cancel()
            pending.append(value)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for value in list(vars(cog).values()):
        if isinstance(value, aiosqlite.Connection):
            await value.close()
        elif isinstance(value, aiohttp.ClientSession) and not value.closed:
            await value.close()


async def load(bot):
    setting = os.getenv('LEGACY_COGS', 'all').strip()
    if setting.lower() in {'none', 'off', '0'}:
        return
    modules = json.loads((Path(__file__).parent / 'legacy_modules.json').read_text())
    available = {cls for _, cls in modules}
    requested = available if setting.lower() in {'', 'all', '*'} else {
        name.strip() for name in setting.split(',') if name.strip()}
    if 'Antinuke' in requested:
        requested.update(cls for mod, cls in modules if mod.startswith('cogs.antinuke.'))
        requested.update({'Whitelist', 'Unwhitelist', 'Extraowner'})
    if 'Automod' in requested:
        requested.update(cls for mod, cls in modules if mod.startswith('cogs.automod.'))
    for parent, child in [('AutoRole', 'Autorole2'), ('Welcomer', 'greet'),
                          ('AutoReaction', 'AutoReactListener')]:
        if parent in requested:
            requested.add(child)
    for unknown in sorted(requested - available):
        bot.legacy_failures.append(unknown)
        log.error('Unknown legacy cog: %s', unknown)
    await asyncio.to_thread(prepare_databases)
    from utils.Tools import setup_db
    await setup_db()
    for module_name, class_name in modules:
        if class_name not in requested or class_name in REPLACED or class_name.startswith('_'):
            continue
        cog = None
        try:
            module = importlib.import_module(module_name)
            cog = getattr(module, class_name)(bot)
            if bot.get_cog(cog.qualified_name):
                cog.__cog_name__ = f'Olympus{cog.qualified_name}'
            # An alias collision must not erase a whole command.
            conflicts = {c for c in cog.get_commands() if bot.get_command(c.name)}
            for command in cog.get_commands():
                command.aliases = [a for a in command.aliases if not bot.get_command(a)]
            cog.__cog_commands__ = tuple(c for c in cog.__cog_commands__
                if c not in conflicts and not any(parent in conflicts for parent in c.parents))
            await bot.add_cog(cog)
            bot.legacy_loaded.append(cog.qualified_name)
            log.info('Loaded Olympus feature %s', class_name)
        except Exception as error:
            bot.legacy_failures.append(class_name)
            bot.legacy_errors[class_name] = f'{type(error).__name__}: {error}'
            log.exception('Olympus feature %s failed to load', class_name)
            if cog:
                try:
                    result = cog.cog_unload()
                    if inspect.isawaitable(result):
                        await result
                finally:
                    await cleanup(cog)
