"""Small server tools that share the bot's permissions, storage, and embed style."""

import asyncio
import re
import time
import discord
from discord.ext import commands
from .branding import embed
from .permissions import check_role, DANGEROUS
from .moderation import duration

SCHEMA = """
CREATE TABLE IF NOT EXISTS tags(guild_id INTEGER,name TEXT,content TEXT,author_id INTEGER,PRIMARY KEY(guild_id,name));
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,channel_id INTEGER,user_id INTEGER,content TEXT,due REAL,status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS verification(guild_id INTEGER PRIMARY KEY,role_id INTEGER,min_age_hours INTEGER,enabled INTEGER);
"""


class VerifyView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Verify", style=discord.ButtonStyle.primary, custom_id="fortune:verify:v1"
    )
    async def verify(self, interaction, button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer(ephemeral=True)
        row = await self.bot.store.one(
            "SELECT * FROM verification WHERE guild_id=?", (interaction.guild.id,)
        )
        if not row or not row["enabled"]:
            return await interaction.followup.send(
                "Verification is disabled.", ephemeral=True
            )
        member = interaction.user
        role = interaction.guild.get_role(row["role_id"])
        try:
            check_role(role, interaction.guild.me, safe=True)
            config, _ = await self.bot.store.config(interaction.guild.id)
            if str(role.id) == str(config["staff_role_id"]):
                raise ValueError(
                    "The staff role cannot be assigned through verification."
                )
            if member.bot:
                raise ValueError("Verification is for member accounts.")
            if (discord.utils.utcnow() - member.created_at).total_seconds() < row[
                "min_age_hours"
            ] * 3600:
                raise ValueError(
                    f'Your account must be at least {row["min_age_hours"]} hours old.'
                )
            if role not in member.roles:
                await member.add_roles(
                    role, reason="Member completed verification", atomic=True
                )
            await interaction.followup.send("You are verified.", ephemeral=True)
        except (ValueError, discord.HTTPException) as error:
            await interaction.followup.send(str(error)[:1500], ephemeral=True)


class ServerTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task = None
        self.view = VerifyView(bot)

    async def cog_load(self):
        await self.bot.store._run(lambda c: c.executescript(SCHEMA))
        self.bot.add_view(self.view)
        self.task = asyncio.create_task(self.reminder_worker())

    async def cog_unload(self):
        self.view.stop()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def cog_check(self, ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage("Use this command in a server.")
        return True

    @commands.hybrid_group(name="tag", invoke_without_command=True)
    async def tag(self, ctx, name: str = None):
        """Send a saved server response. Example: tag rules. Use tag list to browse."""
        if name is None:
            return await ctx.send_help(ctx.command)
        row = await self.bot.store.one(
            "SELECT content FROM tags WHERE guild_id=? AND name=?",
            (ctx.guild.id, name.lower()),
        )
        if not row:
            raise ValueError("Tag not found. Use `tag list`.")
        await ctx.send(row["content"], allowed_mentions=discord.AllowedMentions.none())

    @tag.command(name="get")
    async def tag_get(self, ctx, name: str):
        """Send a saved response by name."""
        await self.tag.callback(self, ctx, name)

    @tag.command(name="create", aliases=["add", "edit"])
    @commands.has_guild_permissions(manage_messages=True)
    async def tag_create(self, ctx, name: str, *, content: str):
        """Create or update a saved response. Requires Manage Messages. No automatic mentions."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", name) or name.lower() in (
            "create",
            "add",
            "delete",
            "remove",
            "list",
        ):
            raise ValueError(
                "Use a non-reserved tag name: 1–32 letters, numbers, hyphens, or underscores."
            )
        if len(content) > 1900:
            raise ValueError("Keep tag content under 1,900 characters.")
        await self.bot.store.execute(
            "INSERT INTO tags VALUES(?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET content=excluded.content,author_id=excluded.author_id",
            (ctx.guild.id, name.lower(), content, ctx.author.id),
        )
        await self.bot.store.audit(ctx.guild.id, ctx.author.id, "tag.save", name)
        await ctx.send(
            embed=embed("Tag saved", f"Use `{ctx.clean_prefix}tag {name.lower()}`.")
        )

    @tag.command(name="delete", aliases=["remove"])
    @commands.has_guild_permissions(manage_messages=True)
    async def tag_delete(self, ctx, name: str):
        """Delete a saved server response. Requires Manage Messages."""
        await self.bot.store.execute(
            "DELETE FROM tags WHERE guild_id=? AND name=?", (ctx.guild.id, name.lower())
        )
        await ctx.send(
            embed=embed("Tag removed", f"`{name.lower()}` was removed if present.")
        )

    @tag.command(name="list")
    async def tag_list(self, ctx):
        """List saved responses in this server."""
        rows = await self.bot.store.rows(
            "SELECT name FROM tags WHERE guild_id=? ORDER BY name", (ctx.guild.id,)
        )
        for off in range(0, max(1, len(rows)), 50):
            await ctx.send(
                embed=embed(
                    "Server tags",
                    ", ".join(f'`{r["name"]}`' for r in rows[off : off + 50])
                    or "No tags yet.",
                )
            )

    @commands.hybrid_command(name="remindme", aliases=["remind"])
    async def remindme(self, ctx, when: str, *, text: str):
        """Schedule your reminder in this channel. Example: remindme 30m check the event. Limit: 30 pending reminders."""
        seconds = duration(when).total_seconds()
        if not 30 <= seconds <= 2419200:
            raise ValueError(
                "Choose a duration from 30 seconds to 28 days, such as 30m or 2h."
            )
        if len(text) > 1500:
            raise ValueError("Keep reminders under 1,500 characters.")

        def create(c):
            c.execute("BEGIN IMMEDIATE")
            count = c.execute(
                "SELECT COUNT(*) FROM reminders WHERE user_id=? AND status='pending'",
                (ctx.author.id,),
            ).fetchone()[0]
            if count >= 30:
                raise ValueError("You already have 30 pending reminders.")
            return c.execute(
                "INSERT INTO reminders(guild_id,channel_id,user_id,content,due) VALUES(?,?,?,?,?)",
                (
                    ctx.guild.id,
                    ctx.channel.id,
                    ctx.author.id,
                    text,
                    time.time() + seconds,
                ),
            ).lastrowid

        ident = await self.bot.store._run(create)
        await ctx.send(
            embed=embed(
                "Reminder set",
                f"**#{ident}** · <t:{int(time.time()+seconds)}:R> in {ctx.channel.mention}.",
            )
        )

    @commands.hybrid_group(name="reminders", invoke_without_command=True)
    async def reminders(self, ctx):
        """List your pending reminders in this server."""
        rows = await self.bot.store.rows(
            "SELECT * FROM reminders WHERE guild_id=? AND user_id=? AND status='pending' ORDER BY due",
            (ctx.guild.id, ctx.author.id),
        )
        await ctx.send(
            embed=embed(
                "Your reminders",
                "\n".join(
                    f'**#{r["id"]}** · <t:{int(r["due"])}:R> · <#{r["channel_id"]}>'
                    for r in rows
                )
                or "No pending reminders.",
            )
        )

    @reminders.command(name="list")
    async def reminders_list(self, ctx):
        """List your pending reminders in this server."""
        await self.reminders.callback(self, ctx)

    @reminders.command(name="cancel")
    async def reminder_cancel(self, ctx, reminder_id: int):
        """Cancel one of your pending reminders."""
        count = await self.bot.store._run(
            lambda c: c.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=? AND guild_id=? AND user_id=? AND status='pending'",
                (reminder_id, ctx.guild.id, ctx.author.id),
            ).rowcount
        )
        if not count:
            raise ValueError(
                "No pending reminder with that ID belongs to you in this server."
            )
        await ctx.send(
            embed=embed("Reminder cancelled", f"Reminder **#{reminder_id}** cancelled.")
        )

    async def deliver_reminders(self):
        rows = await self.bot.store.rows(
            "SELECT * FROM reminders WHERE due<=? AND status='pending' ORDER BY due LIMIT 30",
            (time.time(),),
        )
        for row in rows:
            channel = self.bot.get_channel(row["channel_id"])
            status = "failed"
            if channel and channel.guild.id == row["guild_id"]:
                member = channel.guild.get_member(row["user_id"])
                if member and channel.permissions_for(member).view_channel:
                    try:
                        await channel.send(
                            f'<@{row["user_id"]}>',
                            embed=embed("Reminder", row["content"]),
                            allowed_mentions=discord.AllowedMentions(
                                users=[discord.Object(row["user_id"])]
                            ),
                        )
                        status = "sent"
                    except discord.HTTPException:
                        pass
            await self.bot.store.execute(
                "UPDATE reminders SET status=? WHERE id=?", (status, row["id"])
            )

    async def reminder_worker(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.deliver_reminders()
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging

                logging.getLogger(__name__).exception("Reminder delivery failed")
            await asyncio.sleep(15)

    @commands.hybrid_group(name="verification", invoke_without_command=True)
    @commands.has_guild_permissions(manage_guild=True)
    async def verification(self, ctx):
        """Configure a persistent verification button and minimum account age. This is a role gate, not a CAPTCHA."""
        row = await self.bot.store.one(
            "SELECT * FROM verification WHERE guild_id=?", (ctx.guild.id,)
        )
        await ctx.send(
            embed=embed(
                "Verification",
                (
                    f'Role: <@&{row["role_id"]}>\nMinimum account age: {row["min_age_hours"]} hours\nEnabled: {bool(row["enabled"])}'
                    if row
                    else "Use `verification setup #channel @role [minimum account age in hours]`."
                ),
            )
        )

    @verification.command(name="status")
    @commands.has_guild_permissions(manage_guild=True)
    async def verification_status(self, ctx):
        """Show verification status, role, and minimum account age."""
        await self.verification.callback(self, ctx)

    @verification.command(name="setup")
    @commands.has_guild_permissions(manage_guild=True)
    async def verification_setup(
        self,
        ctx,
        channel: discord.TextChannel,
        role: discord.Role,
        min_age_hours: int = 24,
    ):
        """Publish a verification button. The role must have no moderation or admin powers. Channel access stays under your control."""
        check_role(role, ctx.author, safe=True)
        if not 0 <= min_age_hours <= 720:
            raise ValueError("Minimum account age must be 0–720 hours.")
        config, _ = await self.bot.store.config(ctx.guild.id)
        if str(role.id) == str(config["staff_role_id"]):
            raise ValueError("Choose a role other than the staff role.")
        message = await channel.send(
            embed=embed(
                "Server verification",
                "Read the server rules, then press Verify to receive access.",
            ),
            view=VerifyView(self.bot),
        )
        await self.bot.store.execute(
            "INSERT OR REPLACE INTO verification VALUES(?,?,?,1)",
            (ctx.guild.id, role.id, min_age_hours),
        )
        await ctx.send(
            embed=embed(
                "Verification ready",
                f"[Open verification]({message.jump_url})\nRole: {role.mention}. Configure your channel permissions to use this role for access.",
            )
        )

    @verification.command(name="disable")
    @commands.has_guild_permissions(manage_guild=True)
    async def verification_disable(self, ctx):
        """Disable all FortuneManager verification buttons in this server."""
        await self.bot.store.execute(
            "UPDATE verification SET enabled=0 WHERE guild_id=?", (ctx.guild.id,)
        )
        await ctx.send(
            embed=embed(
                "Verification disabled",
                "Existing buttons will no longer assign a role.",
            )
        )

    @commands.hybrid_group(
        name="reactionrole", aliases=["rr"], invoke_without_command=True
    )
    @commands.has_guild_permissions(manage_roles=True)
    async def reactionrole(self, ctx):
        """Show reaction role mappings. Use reactionrole add #channel message_id emoji @role."""
        p, _ = await self.bot.store.config(ctx.guild.id)
        lines = [
            f'<#{r["channel_id"]}> · `{r["message_id"]}` · {r["emoji"]} → <@&{r["role_id"]}>'
            for r in p["reaction_roles"]
        ]
        for off in range(0, max(1, len(lines)), 15):
            await ctx.send(
                embed=embed(
                    "Reaction roles",
                    "\n".join(lines[off : off + 15]) or "No reaction roles.",
                )
            )

    @reactionrole.command(name="list")
    @commands.has_guild_permissions(manage_roles=True)
    async def reactionrole_list(self, ctx):
        """List reaction role mappings."""
        await self.reactionrole.callback(self, ctx)

    @reactionrole.command(name="add")
    @commands.has_guild_permissions(manage_roles=True)
    async def reactionrole_add(
        self,
        ctx,
        channel: discord.TextChannel,
        message_id: str,
        emoji: str,
        role: discord.Role,
    ):
        """Bind an emoji on an existing message to a safe role. Members can remove the reaction to remove the role."""
        check_role(role, ctx.author, safe=True)
        if not message_id.isdigit():
            raise ValueError("Supply the message ID as digits.")
        if not channel.permissions_for(ctx.author).view_channel:
            raise ValueError("You cannot access that channel.")
        parsed = discord.PartialEmoji.from_str(emoji)
        if len(emoji) > 100 or (parsed.id is None and all(ord(c) < 128 for c in emoji)):
            raise ValueError("Use a Unicode emoji or <:name:id>.")
        async with self.bot.guild_locks[ctx.guild.id]:
            p, v = await self.bot.store.config(ctx.guild.id)
            if str(role.id) == str(p["staff_role_id"]):
                raise ValueError("The staff role cannot be self-assigned.")
            if len(p["reaction_roles"]) >= 100:
                raise ValueError("This server already has 100 reaction role mappings.")
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(parsed)
            p["reaction_roles"] = [
                r
                for r in p["reaction_roles"]
                if not (
                    str(r["channel_id"]) == str(channel.id)
                    and str(r["message_id"]) == message_id
                    and r["emoji"] == str(parsed)
                )
            ]
            p["reaction_roles"].append(
                dict(
                    channel_id=str(channel.id),
                    message_id=message_id,
                    emoji=str(parsed),
                    role_id=str(role.id),
                )
            )
            await self.bot.store.save_config(ctx.guild.id, p, v)
        await ctx.send(embed=embed("Reaction role saved", f"{parsed} → {role.mention}"))

    @reactionrole.command(name="remove")
    @commands.has_guild_permissions(manage_roles=True)
    async def reactionrole_remove(self, ctx, message_id: str, emoji: str):
        """Stop one reaction role binding. Existing member roles are kept."""
        async with self.bot.guild_locks[ctx.guild.id]:
            p, v = await self.bot.store.config(ctx.guild.id)
            p["reaction_roles"] = [
                r
                for r in p["reaction_roles"]
                if not (
                    str(r["message_id"]) == message_id
                    and r["emoji"] == str(discord.PartialEmoji.from_str(emoji))
                )
            ]
            await self.bot.store.save_config(ctx.guild.id, p, v)
        await ctx.send(
            embed=embed(
                "Reaction role removed",
                "The binding was removed. Existing roles were kept.",
            )
        )

    @commands.command(name="synccommands", hidden=True)
    @commands.is_owner()
    async def synccommands(self, ctx):
        """Bot owner only: publish the currently loaded slash commands to Discord."""
        synced = await self.bot.tree.sync()
        await ctx.send(
            embed=embed(
                "Slash commands synced",
                f"Published **{len(synced)}** top-level application commands.",
            )
        )
