import asyncio
import json
import logging
import discord
from discord.ext import commands
from .branding import embed
from .permissions import allowed
from .settings import DATA_DIR
from .store import now

log = logging.getLogger(__name__)


async def reply(interaction, text, **kwargs):
    if interaction.response.is_done():
        return await interaction.followup.send(text, ephemeral=True, **kwargs)
    return await interaction.response.send_message(text, ephemeral=True, **kwargs)


class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        log.exception("Ticket interaction failed", exc_info=error)
        await reply(
            interaction,
            "Could not complete this action. Check the bot permissions and try again.",
        )


class TicketForm(discord.ui.Modal, title="Open a ticket"):
    subject = discord.ui.TextInput(label="Subject", max_length=100)
    details = discord.ui.TextInput(
        label="How can we help?", style=discord.TextStyle.paragraph, max_length=1800
    )

    def __init__(self, cog, panel, option):
        super().__init__(timeout=600)
        self.cog, self.panel, self.option = cog, panel, option

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = await self.cog.open_ticket(
                interaction.guild,
                interaction.user,
                self.panel,
                self.option,
                str(self.subject),
                str(self.details),
            )
            await reply(interaction, f"Your ticket is ready: {channel.mention}")
        except (ValueError, discord.HTTPException) as exc:
            await reply(interaction, str(exc))

    async def on_error(self, interaction, error):
        log.exception("Ticket form failed", exc_info=error)
        await reply(interaction, "Ticket creation failed. Please try again.")


class PanelView(SafeView):
    def __init__(self, cog, panel):
        super().__init__(timeout=None)
        self.cog, self.panel = cog, panel
        options = panel["options"]
        if panel.get("style", "dropdown") == "dropdown":
            select = discord.ui.Select(
                placeholder="Choose a ticket category…",
                custom_id=f"fm:panel:{panel['id']}",
                options=[
                    discord.SelectOption(
                        label=o["label"],
                        value=o["id"],
                        description=o.get("description", "")[:100] or None,
                    )
                    for o in options
                ],
            )

            async def choose(interaction):
                await self.choose(interaction, select.values[0])

            select.callback = choose
            self.add_item(select)
        else:
            for i, option in enumerate(options):
                button = discord.ui.Button(
                    label=option["label"][:80],
                    style=discord.ButtonStyle.primary,
                    custom_id=f"fm:panel:{panel['id']}:{option['id']}",
                    row=i // 5,
                )

                async def choose(interaction, oid=option["id"]):
                    await self.choose(interaction, oid)

                button.callback = choose
                self.add_item(button)

    async def choose(self, interaction, option_id):
        row = await self.cog.bot.store.one(
            "SELECT data FROM ticket_panels WHERE message_id=? AND guild_id=?",
            (interaction.message.id, interaction.guild_id),
        )
        if not row:
            return await reply(interaction, "This panel is no longer active.")
        panel = json.loads(row["data"])
        option = next((o for o in panel["options"] if o["id"] == option_id), None)
        config, _ = await self.cog.bot.store.config(interaction.guild_id)
        if not config["ticket"]["enabled"] or not option:
            return await reply(interaction, "Tickets are currently disabled.")
        await interaction.response.send_modal(TicketForm(self.cog, panel, option))


class CloseConfirm(SafeView):
    def __init__(self, cog, actor_id, delete=False):
        super().__init__(timeout=60)
        self.cog, self.actor_id, self.delete = cog, actor_id, delete

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.actor_id:
            return await reply(
                interaction, "This confirmation belongs to another member."
            )
        await interaction.response.defer(ephemeral=True)
        try:
            if self.delete:
                await self.cog.delete_ticket(interaction)
            else:
                await self.cog.close_ticket(interaction)
        except (ValueError, discord.HTTPException) as exc:
            return await reply(interaction, str(exc))
        await interaction.edit_original_response(
            content="Ticket deleted."
            if self.delete
            else "Ticket closed and transcript saved.",
            view=None,
        )
        self.stop()


class TicketControls(SafeView):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Claim", style=discord.ButtonStyle.primary, custom_id="fm:ticket:claim"
    )
    async def claim(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.claim(interaction)
        except ValueError as exc:
            return await reply(interaction, str(exc))
        await reply(interaction, "Ticket assigned to you.")

    @discord.ui.button(
        label="Close", style=discord.ButtonStyle.danger, custom_id="fm:ticket:close"
    )
    async def close(self, interaction, button):
        try:
            await self.cog.authorize(interaction, owner_ok=True)
        except ValueError as exc:
            return await reply(interaction, str(exc))
        await reply(
            interaction,
            "Close this ticket and save its transcript?",
            view=CloseConfirm(self.cog, interaction.user.id),
        )

    @discord.ui.button(
        label="Transcript",
        style=discord.ButtonStyle.secondary,
        custom_id="fm:ticket:transcript",
    )
    async def transcript(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            row = await self.cog.authorize(interaction, owner_ok=True, access_ok=True)
            path = await self.cog.transcript(interaction.channel, row)
            await reply(interaction, "Ticket transcript", file=discord.File(path))
        except ValueError as exc:
            await reply(interaction, str(exc))

    @discord.ui.button(
        label="Reopen", style=discord.ButtonStyle.success, custom_id="fm:ticket:reopen"
    )
    async def reopen(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.reopen(interaction)
        except (ValueError, discord.HTTPException) as exc:
            return await reply(interaction, str(exc))
        await reply(interaction, "Ticket reopened.")

    @discord.ui.button(
        label="Delete",
        style=discord.ButtonStyle.secondary,
        custom_id="fm:ticket:delete",
    )
    async def delete(self, interaction, button):
        try:
            await self.cog.authorize(interaction)
        except ValueError as exc:
            return await reply(interaction, str(exc))
        await reply(
            interaction,
            "Permanently delete this closed ticket channel? The saved transcript will be retained.",
            view=CloseConfirm(self.cog, interaction.user.id, True),
        )


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reconciled = False

    async def cog_load(self):
        await self.bot.store.execute(
            "CREATE TABLE IF NOT EXISTS ticket_members(ticket_id INTEGER,user_id INTEGER,PRIMARY KEY(ticket_id,user_id))"
        )
        self.bot.add_view(TicketControls(self))
        for row in await self.bot.store.rows("SELECT * FROM ticket_panels"):
            self.bot.add_view(
                PanelView(self, json.loads(row["data"])), message_id=row["message_id"]
            )

    @commands.Cog.listener()
    async def on_ready(self):
        if self.reconciled:
            return
        self.reconciled = True
        for row in await self.bot.store.rows(
            "SELECT * FROM tickets WHERE status IN ('creating','open','closed')"
        ):
            guild = self.bot.get_guild(row["guild_id"])
            if not guild:
                continue
            channel = (
                guild.get_channel(row["channel_id"])
                if row["channel_id"]
                else next(
                    (
                        c
                        for c in guild.text_channels
                        if c.topic and c.topic.startswith(f"fm-ticket:{row['id']} ")
                    ),
                    None,
                )
            )
            if row["status"] == "creating":
                await self.bot.store.execute(
                    "UPDATE tickets SET status=?,channel_id=? WHERE id=?",
                    (
                        "open" if channel else "failed",
                        channel.id if channel else None,
                        row["id"],
                    ),
                )
                if channel:
                    try:
                        await channel.send(
                            embed=embed(
                                "Ticket restored",
                                "Your ticket controls were restored after a restart.",
                            ),
                            view=TicketControls(self),
                        )
                    except discord.HTTPException:
                        log.warning("Cannot restore ticket controls for %s", row["id"])
            elif not channel:
                await self.bot.store.execute(
                    "UPDATE tickets SET status='deleted' WHERE id=?", (row["id"],)
                )

    async def is_manager(self, member):
        if await allowed(self.bot, member, "ticket_manage"):
            return True
        config, _ = await self.bot.store.config(member.guild.id)
        support = {int(x) for x in config["ticket"]["support_role_ids"]}
        return any(r.id in support for r in member.roles)

    async def authorize(self, interaction, owner_ok=False, access_ok=False):
        if not interaction.guild:
            raise ValueError("Use this in a server ticket channel.")
        row = await self.bot.store.one(
            "SELECT * FROM tickets WHERE guild_id=? AND channel_id=?",
            (interaction.guild.id, interaction.channel.id),
        )
        if not row:
            raise ValueError("This is not a FortuneManager ticket.")
        member = await interaction.guild.fetch_member(interaction.user.id)
        if await self.is_manager(member):
            return row
        if owner_ok and row["owner_id"] == member.id:
            return row
        if access_ok and await allowed(self.bot, member, "ticket_access"):
            return row
        raise ValueError("You do not have permission to manage this ticket.")

    async def open_ticket(self, guild, member, panel, option, subject, details):
        async with self.bot.ticket_locks[(guild.id, member.id)]:
            config, _ = await self.bot.store.config(guild.id)
            settings = config["ticket"]
            if not settings["enabled"]:
                raise ValueError("Tickets are disabled.")
            active = await self.bot.store.rows(
                "SELECT channel_id FROM tickets WHERE guild_id=? AND owner_id=? AND status IN ('open','creating')",
                (guild.id, member.id),
            )
            if len(active) >= settings["max_open"]:
                raise ValueError("You already have the maximum number of open tickets.")
            category = guild.get_channel(
                int(option.get("category_id") or settings["category_id"] or 0)
            )
            if not isinstance(category, discord.CategoryChannel):
                raise ValueError(
                    "Ask an administrator to configure a valid ticket category."
                )
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    attach_files=True,
                ),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                ),
            }
            for rid in settings["support_role_ids"]:
                role = guild.get_role(int(rid))
                if role:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    )
            for staff in await self.bot.store.rows(
                "SELECT user_id,permissions FROM staff WHERE guild_id=?", (guild.id,)
            ):
                if not {"ticket_access", "ticket_manage"} & set(
                    json.loads(staff["permissions"])
                ):
                    continue
                user = guild.get_member(staff["user_id"])
                if not user:
                    try:
                        user = await guild.fetch_member(staff["user_id"])
                    except discord.NotFound:
                        continue
                overwrites[user] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
            if len(overwrites) > 95:
                raise ValueError(
                    "Too many individual ticket staff. Configure a support role instead."
                )
            tid = await self.bot.store.execute(
                "INSERT INTO tickets(guild_id,owner_id,panel_id,option_id,created_at) VALUES(?,?,?,?,?)",
                (guild.id, member.id, panel["id"], option["id"], now()),
            )
            channel = None
            try:
                channel = await guild.create_text_channel(
                    f"ticket-{tid:04}",
                    category=category,
                    overwrites=overwrites,
                    topic=f"fm-ticket:{tid} | Owner: {member.id} | {subject}"[:1024],
                    reason="FortuneManager ticket",
                )
                await channel.send(
                    content=member.mention,
                    embed=embed(f"{option['label']} · {subject}", details),
                    view=TicketControls(self),
                    allowed_mentions=discord.AllowedMentions(users=[member]),
                )
                await self.bot.store.execute(
                    "UPDATE tickets SET channel_id=?,status='open' WHERE id=?",
                    (channel.id, tid),
                )
            except Exception:
                if channel:
                    try:
                        await channel.delete(
                            reason="Incomplete ticket creation cleanup"
                        )
                    except discord.HTTPException:
                        await self.bot.store.execute(
                            "UPDATE tickets SET channel_id=?,status='open' WHERE id=?",
                            (channel.id, tid),
                        )
                        raise
                await self.bot.store.execute(
                    "UPDATE tickets SET status='failed' WHERE id=?", (tid,)
                )
                raise
            await self.bot.store.audit(guild.id, member.id, "ticket.open", tid)
            return channel

    async def publish_panel(self, guild, channel, panel):
        message = await channel.send(
            embed=embed(panel["title"], panel["description"]),
            view=PanelView(self, panel),
        )
        try:
            await self.bot.store.execute(
                "INSERT INTO ticket_panels VALUES(?,?,?,?)",
                (message.id, guild.id, channel.id, json.dumps(panel)),
            )
        except Exception:
            await message.delete()
            raise
        return message

    async def sync_staff_access(self, guild, member, permissions):
        access = bool({"ticket_access", "ticket_manage"} & permissions)
        for row in await self.bot.store.rows(
            "SELECT * FROM tickets WHERE guild_id=? AND status IN ('open','closed')",
            (guild.id,),
        ):
            channel = guild.get_channel(row["channel_id"])
            if not channel or row["owner_id"] == member.id:
                continue
            participant = await self.bot.store.one(
                "SELECT 1 FROM ticket_members WHERE ticket_id=? AND user_id=?",
                (row["id"], member.id),
            )
            overwrite = (
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=access or row["status"] == "open",
                    read_message_history=True,
                )
                if access or participant
                else None
            )
            await channel.set_permissions(
                member,
                overwrite=overwrite,
                reason="FortuneManager staff ticket access changed",
            )

    async def transcript(self, channel, row):
        path = (
            DATA_DIR / "transcripts" / str(row["guild_id"]) / f"ticket-{row['id']}.txt"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        chunks = [
            f"FortuneManager | Ticket #{row['id']} | Guild {row['guild_id']} | Owner {row['owner_id']}\nExported {now()}\n\n"
        ]
        size = 0
        count = 0
        async for message in channel.history(limit=10001, oldest_first=True):
            count += 1
            text = f"[{message.created_at.isoformat()}] {message.author} ({message.author.id})\n{message.content}\n"
            for e in message.embeds:
                text += f"[Embed] {e.title or ''}\n{e.description or ''}\n"
                for field in e.fields:
                    text += f"{field.name}: {field.value}\n"
            for attachment in message.attachments:
                text += f"[Attachment] {attachment.filename}: {attachment.url}\n"
            size += len(text.encode("utf-8"))
            if size > 7_000_000 or count > 10000:
                chunks.append(
                    "\n[EXPORT LIMIT REACHED: transcript truncated after 10,000 messages or 7 MB.]\n"
                )
                break
            chunks.append(text + "\n")
        await asyncio.to_thread(path.write_text, "".join(chunks), encoding="utf-8")
        return path

    async def set_participants_writing(self, channel, row, enabled):
        ids = {row["owner_id"]} | {
            r["user_id"]
            for r in await self.bot.store.rows(
                "SELECT user_id FROM ticket_members WHERE ticket_id=?", (row["id"],)
            )
        }
        for uid in ids:
            member = channel.guild.get_member(uid)
            if member is None:
                try:
                    member = await channel.guild.fetch_member(uid)
                except discord.NotFound:
                    continue
            ow = channel.overwrites_for(member)
            ow.view_channel = True
            ow.read_message_history = True
            ow.send_messages = enabled
            await channel.set_permissions(
                member, overwrite=ow, reason="Ticket state change"
            )

    async def close_ticket(self, interaction):
        async with self.bot.channel_locks[interaction.channel_id]:
            row = await self.authorize(interaction, owner_ok=True)
            if row["status"] != "open":
                raise ValueError("This ticket is already closed.")
            path = await self.transcript(interaction.channel, row)
            await self.set_participants_writing(interaction.channel, row, False)
            await self.bot.store.execute(
                "UPDATE tickets SET status='closed',closed_at=? WHERE id=?",
                (now(), row["id"]),
            )
            await self.bot.store.audit(
                interaction.guild_id, interaction.user.id, "ticket.close", row["id"]
            )
            await interaction.channel.send(
                embed=embed(
                    "Ticket closed",
                    f"Closed by {interaction.user.mention}. The transcript was saved.",
                ),
                view=TicketControls(self),
            )
            config, _ = await self.bot.store.config(interaction.guild_id)
            channel = interaction.guild.get_channel(
                int(config["ticket"]["log_channel_id"] or 0)
            )
            if channel:
                try:
                    await channel.send(
                        embed=embed(
                            f"Ticket #{row['id']} closed",
                            f"Owner: <@{row['owner_id']}>",
                        ),
                        file=discord.File(path),
                    )
                except discord.HTTPException:
                    log.warning(
                        "Unable to send transcript to log channel for ticket %s",
                        row["id"],
                    )

    async def reopen(self, interaction):
        async with self.bot.channel_locks[interaction.channel_id]:
            row = await self.authorize(interaction)
            async with self.bot.ticket_locks[(interaction.guild_id, row["owner_id"])]:
                if row["status"] != "closed":
                    raise ValueError("Only closed tickets can be reopened.")
                config, _ = await self.bot.store.config(interaction.guild_id)
                active = await self.bot.store.one(
                    "SELECT COUNT(*) AS count FROM tickets WHERE guild_id=? AND owner_id=? AND status IN ('open','creating')",
                    (interaction.guild_id, row["owner_id"]),
                )
                if active["count"] >= config["ticket"]["max_open"]:
                    raise ValueError("The owner is at their open-ticket limit.")
                await self.set_participants_writing(interaction.channel, row, True)
                await self.bot.store.execute(
                    "UPDATE tickets SET status='open',closed_at=NULL WHERE id=?",
                    (row["id"],),
                )
                await self.bot.store.audit(
                    interaction.guild_id,
                    interaction.user.id,
                    "ticket.reopen",
                    row["id"],
                )
                await interaction.channel.send(
                    embed=embed(
                        "Ticket reopened", f"Reopened by {interaction.user.mention}."
                    ),
                    view=TicketControls(self),
                )

    async def claim(self, interaction):
        async with self.bot.channel_locks[interaction.channel_id]:
            row = await self.authorize(interaction)
            if row["status"] != "open":
                raise ValueError("Only open tickets can be claimed.")
            if row["claimed_by"] and row["claimed_by"] != interaction.user.id:
                raise ValueError(
                    f"This ticket is already claimed by <@{row['claimed_by']}>."
                )
            await self.bot.store.execute(
                "UPDATE tickets SET claimed_by=? WHERE id=?",
                (interaction.user.id, row["id"]),
            )
            await interaction.channel.send(
                embed=embed(
                    "Ticket claimed", f"{interaction.user.mention} will help you."
                )
            )

    async def delete_ticket(self, interaction):
        async with self.bot.channel_locks[interaction.channel_id]:
            row = await self.authorize(interaction)
            if row["status"] != "closed":
                raise ValueError("Close the ticket before deleting it.")
            await self.transcript(interaction.channel, row)
            await interaction.channel.delete(
                reason=f"Ticket deleted by {interaction.user.id}"
            )
            await self.bot.store.execute(
                "UPDATE tickets SET status='deleted' WHERE id=?", (row["id"],)
            )
            await self.bot.store.audit(
                interaction.guild_id, interaction.user.id, "ticket.delete", row["id"]
            )

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def ticket(self, ctx):
        await ctx.send(
            embed=embed(
                "Ticket commands",
                f"`{ctx.clean_prefix}ticket setup #category @support-role`\n`{ctx.clean_prefix}ticket panel #channel`\n`{ctx.clean_prefix}ticket add @member` / `ticket remove @member`\nUse the dashboard for combined panels with buttons or dropdowns.",
            )
        )

    @ticket.command(name="setup")
    @commands.has_guild_permissions(administrator=True)
    async def setup_ticket(
        self, ctx, category: discord.CategoryChannel, support: discord.Role = None
    ):
        if support and support.is_default():
            raise commands.BadArgument("The support role cannot be @everyone.")
        async with self.bot.guild_locks[ctx.guild.id]:
            config, version = await self.bot.store.config(ctx.guild.id)
            if support and str(support.id) == config["staff_role_id"]:
                raise commands.BadArgument(
                    "Use a separate support role; the staff role is governed by individual grants."
                )
            config["ticket"].update(
                enabled=True,
                category_id=str(category.id),
                support_role_ids=[str(support.id)] if support else [],
            )
            if not config["ticket"]["panels"]:
                config["ticket"]["panels"] = [
                    {
                        "id": "support",
                        "title": "How can we help?",
                        "description": "Choose a category to open a private ticket.",
                        "style": "dropdown",
                        "options": [
                            {
                                "id": "general",
                                "label": "General support",
                                "description": "Get help from the team",
                                "category_id": None,
                            }
                        ],
                    }
                ]
            await self.bot.store.save_config(ctx.guild.id, config, version)
        await ctx.send(
            embed=embed(
                "Tickets configured",
                f"Publish a panel with `{ctx.clean_prefix}ticket panel #channel`.",
            )
        )

    @ticket.command(name="panel")
    @commands.has_guild_permissions(manage_guild=True)
    async def panel_command(
        self, ctx, channel: discord.TextChannel = None, panel_id: str = "support"
    ):
        config, _ = await self.bot.store.config(ctx.guild.id)
        panel = next(
            (p for p in config["ticket"]["panels"] if p["id"] == panel_id), None
        )
        if not panel:
            raise commands.BadArgument(
                "Panel not found. Configure one in the dashboard or run ticket setup."
            )
        msg = await self.publish_panel(ctx.guild, channel or ctx.channel, panel)
        await ctx.send(embed=embed("Panel published", f"[Open panel]({msg.jump_url})"))

    async def participant(self, ctx, member, add):
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        if not await self.is_manager(ctx.author):
            raise commands.CheckFailure("Ticket management permission is required.")
        row = await self.bot.store.one(
            "SELECT * FROM tickets WHERE guild_id=? AND channel_id=?",
            (ctx.guild.id, ctx.channel.id),
        )
        if not row or row["status"] != "open":
            raise commands.BadArgument("Use this in an open ticket.")
        if member.id in (row["owner_id"], ctx.guild.me.id):
            raise commands.BadArgument("Cannot change the ticket owner or bot access.")
        if not add and (
            await self.is_manager(member)
            or await allowed(self.bot, member, "ticket_access")
        ):
            raise commands.BadArgument("Revoke staff/support access first.")
        async with self.bot.channel_locks[ctx.channel.id]:
            await ctx.channel.set_permissions(
                member,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
                if add
                else None,
            )
            if add:
                await self.bot.store.execute(
                    "INSERT OR IGNORE INTO ticket_members VALUES(?,?)",
                    (row["id"], member.id),
                )
            else:
                await self.bot.store.execute(
                    "DELETE FROM ticket_members WHERE ticket_id=? AND user_id=?",
                    (row["id"], member.id),
                )
        await ctx.send(
            embed=embed(
                "Ticket access updated",
                f"{member.mention} was {'added' if add else 'removed'}.",
            )
        )

    @ticket.command(name="add")
    async def add_member(self, ctx, member: discord.Member):
        await self.participant(ctx, member, True)

    @ticket.command(name="remove")
    async def remove_member(self, ctx, member: discord.Member):
        await self.participant(ctx, member, False)
