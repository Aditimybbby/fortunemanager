import asyncio
from collections import defaultdict
import logging
import os
import aiohttp
import discord
from discord.ext import commands
from . import settings
from .branding import install_branding, embed
from .store import Store

log = logging.getLogger(__name__)


class FortuneManager(commands.Bot):
    def __init__(self, *, database=None, start_dashboard=True, load_legacy=True):
        install_branding()
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.presences = os.getenv("ENABLE_PRESENCES", "0") == "1"
        super().__init__(
            command_prefix=self.resolve_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            strip_after_prefix=True,
            owner_ids=settings.OWNER_IDS or None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=False, replied_user=False
            ),
        )
        self.store = Store(database or settings.DATA_DIR / "fortune.db")
        self.guild_locks = defaultdict(asyncio.Lock)
        self.ticket_locks = defaultdict(asyncio.Lock)
        self.channel_locks = defaultdict(asyncio.Lock)
        self.session = None
        self.dashboard_server = None
        self.start_dashboard = start_dashboard
        self.load_legacy = load_legacy
        self.legacy_failures = []
        self.legacy_errors = {}
        self.legacy_loaded = []

    async def resolve_prefix(self, bot, message):
        prefix = settings.DEFAULT_PREFIX
        if message.guild:
            config, _ = await self.store.config(message.guild.id)
            prefix = config["prefix"]
        return commands.when_mentioned_or(*dict.fromkeys([prefix, "."]))(self, message)

    async def setup_hook(self):
        await self.store.init()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        from .staff import Staff
        from .moderation import Moderation
        from .tickets import Tickets
        from .community import Community
        from .tracking import Tracking
        from .events import Events

        for cls in (Staff, Moderation, Tickets, Community, Tracking, Events):
            await self.add_cog(cls(self))
        if self.load_legacy:
            from .legacy import load

            await load(self)
        from .help import OlympusHelp
        self.help_command = OlympusHelp()
        if self.start_dashboard and os.getenv("ENABLE_DASHBOARD", "1") == "1":
            from .dashboard import Dashboard

            self.dashboard_server = Dashboard(self)
            await self.dashboard_server.start()
        log.info(
            "Loaded %s commands and %s persistent views",
            len(list(self.walk_commands())),
            len(self.persistent_views),
        )

    async def on_ready(self):
        await self.change_presence(
            activity=discord.Game(f"{settings.DEFAULT_PREFIX}help | .gg/fortuneleaf")
        )
        log.info(
            "FortuneManager connected as %s in %s servers", self.user, len(self.guilds)
        )

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return await ctx.send(embed=embed('Unknown command',
                f'Use `{ctx.clean_prefix}help` to see the loaded commands. '
                'If an original module is missing, an administrator can use `modulestatus`.'))
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingRequiredArgument):
            message = f"Missing `{error.param.name}`. Use `{ctx.clean_prefix}help {ctx.command.qualified_name}`."
        elif isinstance(error, commands.BotMissingPermissions):
            message = "I need these Discord permissions: " + ", ".join(
                error.missing_permissions
            )
        elif isinstance(
            error,
            (
                commands.CheckFailure,
                commands.BadArgument,
                commands.CommandOnCooldown,
                ValueError,
            ),
        ):
            message = str(error)
        elif isinstance(error, discord.Forbidden):
            message = "Discord denied this action. Check my permissions and place my role above the target role."
        elif isinstance(error, discord.NotFound):
            message = "That Discord member, message, or channel no longer exists."
        else:
            log.error("Command failed: %s", ctx.command, exc_info=error)
            message = (
                "Something went wrong. Check the bot console for details and try again."
            )
        try:
            await ctx.send(
                embed=embed(
                    "Unable to complete that action", message[:3500], color=0xEF8999
                )
            )
        except discord.HTTPException:
            log.warning("Cannot report an error in channel %s", ctx.channel.id)

    async def close(self):
        if self.dashboard_server:
            await self.dashboard_server.close()
            self.dashboard_server = None
        # Allow old cogs to cancel tasks before the shared HTTP session closes.
        from .legacy import cleanup
        for name in list(self.cogs):
            cog = self.get_cog(name)
            try:
                await self.remove_cog(name)
            finally:
                if name in self.legacy_loaded:
                    await cleanup(cog)
        if self.session and not self.session.closed:
            await self.session.close()
        await super().close()

    async def send_raw(self, channel_id, content, **kwargs):
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        return await channel.send(content, **kwargs)

    async def get_context(self, origin, /, *, cls=None):
        from core.Context import Context
        return await super().get_context(origin, cls=cls or Context)

    async def invoke_help_command(self, ctx):
        return await ctx.send_help(ctx.command)

    async def fetch_message_by_channel(self, channel, messageID):
        return await channel.fetch_message(messageID)


def setup_bot():
    return FortuneManager()
