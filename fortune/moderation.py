import re
import json
from datetime import timedelta
import discord
from discord.ext import commands
from .branding import embed
from .permissions import require, check_target, allowed
from .store import now


def duration(value):
    match = re.fullmatch(r"(\d+)(s|m|h|d)", value.lower())
    if not match:
        raise commands.BadArgument("Use a duration such as 10m, 2h, or 7d.")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if not 1 <= seconds <= 28 * 86400:
        raise commands.BadArgument("Duration must be between 1 second and 28 days.")
    return timedelta(seconds=seconds)


def reason_for(ctx, reason):
    return f"{ctx.author} ({ctx.author.id}): {reason}"[:512]


class DeleteConfirm(discord.ui.View):
    def __init__(self, ctx, channel):
        super().__init__(timeout=60)
        self.ctx, self.channel = ctx, channel

    @discord.ui.button(label="Delete channel", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "This confirmation belongs to another moderator.", ephemeral=True
            )
        actor = await interaction.guild.fetch_member(interaction.user.id)
        if not await allowed(self.ctx.bot, actor, "delete_channel"):
            return await interaction.response.send_message(
                "Your permission was revoked.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await self.channel.delete(reason=f"Confirmed by {actor.id}")
        await self.ctx.bot.store.audit(
            interaction.guild.id, actor.id, "channel.delete", self.channel.id
        )
        await interaction.followup.send("Channel deleted.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "This is not your confirmation.", ephemeral=True
            )
        await interaction.response.edit_message(view=None)
        self.stop()


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def result(self, ctx, action, target, reason=""):
        await self.bot.store.audit(
            ctx.guild.id, ctx.author.id, action, f"{target}: {reason}"
        )
        message = embed(
            action.replace(".", " ").title(),
            f"**Target:** {target}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason or 'No reason supplied'}",
        )
        await ctx.send(embed=message)
        config, _ = await self.bot.store.config(ctx.guild.id)
        log = ctx.guild.get_channel(int(config["log_channel_id"] or 0))
        if log and log != ctx.channel:
            try:
                await log.send(embed=message)
            except discord.HTTPException:
                pass

    @commands.command()
    @require("kick")
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason="No reason supplied"):
        check_target(ctx.author, member)
        await member.kick(reason=reason_for(ctx, reason))
        await self.result(ctx, "member.kick", member, reason)

    @commands.command()
    @require("ban")
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason="No reason supplied"):
        check_target(ctx.author, member)
        await member.ban(reason=reason_for(ctx, reason), delete_message_seconds=0)
        await self.result(ctx, "member.ban", member, reason)

    @commands.command()
    @require("ban")
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: int, *, reason="No reason supplied"):
        await ctx.guild.unban(
            discord.Object(id=user_id), reason=reason_for(ctx, reason)
        )
        await self.result(ctx, "member.unban", str(user_id), reason)

    async def apply_timeout(self, ctx, member, value, reason):
        check_target(ctx.author, member)
        if member.guild_permissions.administrator:
            raise commands.BadArgument(
                "Discord does not allow timeouts for administrators."
            )
        await member.timeout(duration(value), reason=reason_for(ctx, reason))
        await self.result(ctx, "member.timeout", member, f"{value} · {reason}")

    @commands.command()
    @require("mute")
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(
        self,
        ctx,
        member: discord.Member,
        duration: str = "10m",
        *,
        reason="No reason supplied",
    ):
        """Mute using Discord timeout, default 10 minutes."""
        await self.apply_timeout(ctx, member, duration, reason)

    @commands.command()
    @require("timeout")
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx,
        member: discord.Member,
        duration: str = "10m",
        *,
        reason="No reason supplied",
    ):
        await self.apply_timeout(ctx, member, duration, reason)

    @commands.command(aliases=["untimeout"])
    @require("mute")
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member, *, reason="No reason supplied"):
        check_target(ctx.author, member)
        await member.timeout(None, reason=reason_for(ctx, reason))
        await self.result(ctx, "member.unmute", member, reason)

    @commands.command()
    @require("warn")
    async def warn(self, ctx, member: discord.Member, *, reason):
        check_target(ctx.author, member)
        await self.bot.store.execute(
            "INSERT INTO warnings(guild_id,user_id,actor_id,reason,created_at) VALUES(?,?,?,?,?)",
            (ctx.guild.id, member.id, ctx.author.id, reason[:1500], now()),
        )
        await self.result(ctx, "member.warn", member, reason)

    @commands.command()
    @require("warn")
    async def warnings(self, ctx, member: discord.Member):
        rows = await self.bot.store.rows(
            "SELECT id,reason FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 15",
            (ctx.guild.id, member.id),
        )
        await ctx.send(
            embed=embed(
                "Recent warnings",
                "\n".join(f"#{r['id']} · {r['reason'][:200]}" for r in rows)
                or "No warnings.",
            )
        )

    @commands.command(aliases=["clear"])
    @require("purge")
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(self, ctx, amount: int):
        if not 1 <= amount <= 100:
            raise commands.BadArgument("Choose 1–100 messages.")
        deleted = await ctx.channel.purge(limit=amount + 1)
        await self.bot.store.audit(
            ctx.guild.id, ctx.author.id, "messages.purge", len(deleted)
        )
        await ctx.send(
            embed=embed(
                "Messages cleared", f"Removed {max(0, len(deleted) - 1)} messages."
            ),
            delete_after=5,
        )

    @commands.command()
    @require("lock")
    @commands.bot_has_permissions(manage_roles=True)
    async def lock(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        async with self.bot.guild_locks[ctx.guild.id]:
            old = await self.bot.store.one(
                "SELECT previous FROM channel_locks WHERE guild_id=? AND channel_id=?",
                (ctx.guild.id, channel.id),
            )
            if old:
                raise commands.BadArgument(
                    "This channel is already locked by FortuneManager."
                )
            ow = channel.overwrites_for(ctx.guild.default_role)
            previous = json.dumps(
                {
                    "send_messages": ow.send_messages,
                    "send_messages_in_threads": ow.send_messages_in_threads,
                    "create_public_threads": ow.create_public_threads,
                }
            )
            await self.bot.store.execute(
                "INSERT INTO channel_locks VALUES(?,?,?)",
                (ctx.guild.id, channel.id, previous),
            )
            ow.send_messages = False
            ow.send_messages_in_threads = False
            ow.create_public_threads = False
            try:
                await channel.set_permissions(
                    ctx.guild.default_role,
                    overwrite=ow,
                    reason=reason_for(ctx, "Channel lock"),
                )
            except Exception:
                await self.bot.store.execute(
                    "DELETE FROM channel_locks WHERE guild_id=? AND channel_id=?",
                    (ctx.guild.id, channel.id),
                )
                raise
        await self.result(ctx, "channel.lock", channel.mention)

    @commands.command()
    @require("lock")
    @commands.bot_has_permissions(manage_roles=True)
    async def unlock(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        async with self.bot.guild_locks[ctx.guild.id]:
            row = await self.bot.store.one(
                "SELECT previous FROM channel_locks WHERE guild_id=? AND channel_id=?",
                (ctx.guild.id, channel.id),
            )
            if not row:
                raise commands.BadArgument(
                    "No saved FortuneManager lock exists for this channel."
                )
            ow = channel.overwrites_for(ctx.guild.default_role)
            for key, value in json.loads(row["previous"]).items():
                setattr(ow, key, value)
            await channel.set_permissions(
                ctx.guild.default_role,
                overwrite=None if ow.is_empty() else ow,
                reason=reason_for(ctx, "Restore channel permissions"),
            )
            await self.bot.store.execute(
                "DELETE FROM channel_locks WHERE guild_id=? AND channel_id=?",
                (ctx.guild.id, channel.id),
            )
        await self.result(ctx, "channel.unlock", channel.mention)

    @commands.command(name="deletechannel", aliases=["delchannel"])
    @require("delete_channel")
    @commands.bot_has_permissions(manage_channels=True)
    async def delete_channel(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        await ctx.send(
            embed=embed(
                "Delete channel?",
                f"Delete {channel.mention} and its message history? This cannot be undone.",
            ),
            view=DeleteConfirm(ctx, channel),
        )

    @commands.command()
    @require("slowmode")
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx, seconds: int, channel: discord.TextChannel = None):
        if not 0 <= seconds <= 21600:
            raise commands.BadArgument("Use 0–21600 seconds.")
        channel = channel or ctx.channel
        await channel.edit(slowmode_delay=seconds, reason=reason_for(ctx, "Slowmode"))
        await self.result(ctx, "channel.slowmode", channel.mention, f"{seconds}s")

    @commands.command(aliases=["nick"])
    @require("nickname")
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nickname(self, ctx, member: discord.Member, *, name: str = None):
        check_target(ctx.author, member)
        if name and len(name) > 32:
            raise commands.BadArgument("Nickname must be at most 32 characters.")
        await member.edit(nick=name, reason=reason_for(ctx, "Nickname update"))
        await self.result(ctx, "member.nickname", member, name or "Reset")
