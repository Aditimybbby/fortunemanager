"""Run FortuneManager and its dashboard in one process."""

import asyncio
import logging
import os
from fortune import settings
from fortune.bot import FortuneManager


async def main():
    if not settings.TOKEN:
        raise SystemExit(
            "Missing DISCORD_TOKEN. Copy .env.example to .env and add your bot token."
        )
    # Keep paths predictable for local and container deployments.
    os.chdir(settings.ROOT)
    async with FortuneManager() as bot:
        await bot.start(settings.TOKEN)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
