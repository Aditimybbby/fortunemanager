"""Optional original features, imported independently so one failure cannot stop the bot.

The reworked moderation, greetings, staff and ticket cogs are authoritative.
Original owner/global-control modules are intentionally not loaded.
"""

import importlib
import inspect
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
# These are replaced by the new services or conflict with their permission model.
REPLACED = {
    "Help",
    "Owner",
    "Global",
    "Welcomer",
    "greet",
    "Errors",
    "Mention",
    "Guild",
    "Moderation",
    "Ban",
    "Unban",
    "Mute",
    "Unmute",
    "Lock",
    "Unlock",
    "Kick",
    "Warn",
    "Role",
    "Message",
    "TopCheck",
    "AutoRole",
    "Autorole",
    "Autorole2",
    "React",
    "NoPrefix",
    "Blacklist",
    "Block",
    "AutoBlacklist",
}


async def load(bot):
    requested = {
        x.strip() for x in os.getenv("LEGACY_COGS", "").split(",") if x.strip()
    }
    if not requested:
        return
    from utils.Tools import setup_db

    await setup_db()
    modules = json.loads((Path(__file__).parent / "legacy_modules.json").read_text())
    available = {cls for _, cls in modules}
    for unknown in requested - available:
        log.warning("Unknown legacy cog: %s", unknown)
    for module_name, class_name in modules:
        if class_name not in requested:
            continue
        if class_name in REPLACED:
            log.warning(
                "Legacy cog %s is replaced by FortuneManager services and will not be loaded",
                class_name,
            )
            continue
        cog = None
        try:
            module = importlib.import_module(module_name)
            cog = getattr(module, class_name)(bot)
            # New commands always take precedence, including their aliases.
            conflicts = {
                c
                for c in cog.get_commands()
                if any(bot.get_command(n) for n in [c.name, *c.aliases])
            }
            cog.__cog_commands__ = tuple(
                c
                for c in cog.__cog_commands__
                if c not in conflicts and not any(p in conflicts for p in c.parents)
            )
            await bot.add_cog(cog)
            log.info("Loaded optional legacy cog %s", class_name)
        except Exception:
            bot.legacy_failures.append(class_name)
            log.exception(
                "Optional feature %s failed; core commands remain available", class_name
            )
            if cog:
                try:
                    result = cog.cog_unload()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass
                for value in vars(cog).values():
                    if (
                        hasattr(value, "closed")
                        and hasattr(value, "close")
                        and not value.closed
                    ):
                        result = value.close()
                        if inspect.isawaitable(result):
                            await result
