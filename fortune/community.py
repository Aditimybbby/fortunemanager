import logging
import discord
from discord.ext import commands
from .branding import embed
from .permissions import check_role
from .settings import DASHBOARD_URL

log = logging.getLogger(__name__)


def render(template, member):
    values = {
        "user": member.mention,
        "username": discord.utils.escape_markdown(member.display_name),
        "server": discord.utils.escape_markdown(member.guild.name),
        "member_count": str(member.guild.member_count or len(member.guild.members)),
        "user_id": str(member.id),
    }
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


async def greet_message(channel, member, config):
    content = render(config["content"], member)[:2000] or None
    message = None
    if config["use_embed"]:
        message = embed(
            render(config["title"], member)[:256],
            render(config["description"], member)[:4096],
            color=int(config["color"][1:], 16),
        )
        if config["image"]:
            message.set_image(url=config["image"])
    return await channel.send(
        content=content,
        embed=message,
        allowed_mentions=discord.AllowedMentions(
            users=[member], roles=False, everyone=False
        ),
    )


class Community(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        config, _ = await self.bot.store.config(member.guild.id)
        greet = config["greet"]
        for rid in greet["autorole_ids"]:
            role = member.guild.get_role(int(rid))
            try:
                check_role(role, member.guild.me, safe=True)
                if str(rid) == config["staff_role_id"]:
                    continue
                await member.add_roles(role, reason="FortuneManager join role")
            except (ValueError, discord.HTTPException):
                log.warning(
                    "Unable to add greeting role %s in %s", rid, member.guild.id
                )
        if greet["enabled"]:
            channel = member.guild.get_channel(int(greet["channel_id"] or 0))
            if channel:
                try:
                    await greet_message(channel, member, greet)
                except discord.HTTPException:
                    log.warning("Cannot send greeting in %s", member.guild.id)

    async def reaction(self, payload, add):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        config, _ = await self.bot.store.config(guild.id)
        matches = [
            r
            for r in config["reaction_roles"]
            if r["message_id"] == str(payload.message_id)
            and r["channel_id"] == str(payload.channel_id)
            and str(discord.PartialEmoji.from_str(r["emoji"])) == str(payload.emoji)
        ]
        if not matches:
            return
        try:
            member = (
                payload.member
                if getattr(payload, "member", None)
                else await guild.fetch_member(payload.user_id)
            )
        except discord.NotFound:
            return
        if member.bot:
            return
        for row in matches:
            role = guild.get_role(int(row["role_id"]))
            try:
                check_role(role, guild.me, safe=True)
                if str(role.id) == config["staff_role_id"]:
                    continue
                if add:
                    await member.add_roles(role, reason="FortuneManager reaction role")
                else:
                    await member.remove_roles(
                        role, reason="FortuneManager reaction removed"
                    )
            except (ValueError, discord.HTTPException):
                log.warning(
                    "Reaction role failed for role %s in %s", row["role_id"], guild.id
                )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.reaction(payload, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.reaction(payload, False)

    @commands.command()
    async def dashboard(self, ctx):
        await ctx.send(
            embed=embed(
                "FortuneManager dashboard", f"[Configure your server]({DASHBOARD_URL})"
            )
        )

    @commands.command()
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def prefix(self, ctx, value: str):
        if not 1 <= len(value) <= 8 or value.isspace():
            raise commands.BadArgument("Use a prefix of 1–8 visible characters.")
        async with self.bot.guild_locks[ctx.guild.id]:
            config, version = await self.bot.store.config(ctx.guild.id)
            config["prefix"] = value
            await self.bot.store.save_config(ctx.guild.id, config, version)
        await ctx.send(embed=embed("Prefix updated", f"Your new prefix is `{value}`."))

    @commands.command()
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def greettest(self, ctx):
        config, _ = await self.bot.store.config(ctx.guild.id)
        await greet_message(ctx.channel, ctx.author, config["greet"])

    @commands.command()
    async def ping(self, ctx):
        await ctx.send(embed=embed("Pong", f"{round(self.bot.latency * 1000)} ms"))

    @commands.command()
    async def help(self, ctx, *, command: str = None):
        if command:
            cmd = self.bot.get_command(command)
            if not cmd:
                return await ctx.send(
                    embed=embed(
                        "Command not found", "Use `help` to view the main commands."
                    )
                )
            return await ctx.send(
                embed=embed(
                    f"{ctx.clean_prefix}{cmd.qualified_name} {cmd.signature}",
                    cmd.help or "No additional description.",
                )
            )
        e = embed(
            "FortuneManager",
            "Server management, made simple.\nUse `help <command>` for arguments.",
        )
        for name, value in [
            (
                "Staff",
                "staff @member · staff list · staff remove @member · staffrole @role",
            ),
            (
                "Moderation",
                "kick · ban · unban · mute · unmute · timeout · warn · warnings · purge · nickname",
            ),
            ("Channels", "lock · unlock · deletechannel · slowmode"),
            (
                "Tickets",
                "ticket setup · ticket panel · ticket add · ticket remove\nClaim, close, transcript, reopen, and delete buttons inside tickets.",
            ),
            ("Tracking", "invites @member · messages @member · leaderboard invites/messages · trackingstatus"),
            ("Events", "event · event end · event owners · eventticket · check · proof <title> + image\nStaff: eventrule · eventunflag · eventreview"),
            ("Server setup", "dashboard · prefix · greettest · ping"),
        ]:
            e.add_field(name=name, value=value, inline=False)
        await ctx.send(embed=e)
