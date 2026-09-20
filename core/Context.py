from __future__ import annotations

from discord.ext import commands
import discord
import functools
from typing import Optional, Any
import asyncio

__all__ = ("Context", )


class Context(commands.Context):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return "<core.Context>"

    @property
    async def session(self):
        return self.bot.session

    @discord.utils.cached_property
    def replied_reference(self) -> Optional[discord.Message]:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved.to_reference()
        return None

    def with_type(func):

        @functools.wraps(func)
        async def wrapped(self, *args, **kwargs):
            context = args[0] if isinstance(args[0],
                                            commands.Context) else args[1]
            try:
                async with context.typing():
                    await func(*args, **kwargs)
            except discord.Forbidden:  
                await func(*args, **kwargs)

        return wrapped

    async def show_help(self, command: str = None) -> Any:
        cmd = self.bot.get_command('help')
        command = command or self.command.qualified_name
        await self.invoke(cmd, command=command)

    async def send(self,
                   content: Optional[str] = None,
                   **kwargs) -> Optional[discord.Message]:
        if self.guild and not self.channel.permissions_for(self.me).send_messages:
            return
        if "delete_after" not in kwargs:
            delay = 20
            if self.guild:
                config, _ = await self.bot.store.config(self.guild.id)
                delay = config["reply_delete_after"]
            view = kwargs.get("view")
            if view is not None and view.timeout is None:
                delay = 0  # Published persistent controls must remain usable.
            elif view is not None and delay:
                delay = max(delay, view.timeout + 5)
            kwargs["delete_after"] = delay or None
        return await super().send(content, **kwargs)

    async def reply(self,
                    content: Optional[str] = None,
                    **kwargs) -> Optional[discord.Message]:
        # commands.Context.reply delegates to self.send, including cleanup.
        return await super().reply(content, **kwargs)

    async def error(self, message, **kwargs):
        from fortune.branding import embed
        return await self.send(embed=embed('Unable to complete that action', message, color=0xEF8999), **kwargs)

    async def success(self, message, **kwargs):
        from fortune.branding import embed
        return await self.send(embed=embed('Done', message), **kwargs)

    async def release(self, delay: Optional[int] = None) -> None:
        delay = delay or 0
        await asyncio.sleep(delay)
