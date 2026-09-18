import discord
from discord.ext import commands
from .branding import embed
from .permissions import PERMISSIONS, admin, check_role, check_target, require_admin


async def set_staff(bot, guild, actor, target, permissions):
    if not admin(actor):
        raise ValueError("Administrator permission is required.")
    if not set(permissions) <= PERMISSIONS.keys():
        raise ValueError("Unknown permission.")
    if target.bot:
        raise ValueError("Choose a human member.")
    check_target(actor, target)
    async with bot.guild_locks[guild.id]:
        config, _ = await bot.store.config(guild.id)
        role = guild.get_role(int(config["staff_role_id"] or 0))
        check_role(role, actor, safe=True)
        old = await bot.store.staff(guild.id, target.id)
        old_role = guild.get_role(old["role_id"]) if old and old["role_id"] else None
        if (
            str(role.id) in config["ticket"]["support_role_ids"]
            or str(role.id) in config["greet"]["autorole_ids"]
            or any(r["role_id"] == str(role.id) for r in config["reaction_roles"])
        ):
            raise ValueError(
                "The staff role must not be a support, join, or reaction role."
            )
        added = role not in target.roles
        removed_old = False
        if added:
            await target.add_roles(
                role, reason=f"FortuneManager staff assignment by {actor.id}"
            )
        tickets = bot.get_cog("Tickets")
        try:
            if tickets:
                await tickets.sync_staff_access(guild, target, set(permissions))
            if old_role and old_role != role and old_role in target.roles:
                await target.remove_roles(
                    old_role, reason="FortuneManager staff role changed"
                )
                removed_old = True
            await bot.store.save_staff(
                guild.id, target.id, permissions, role.id, actor.id
            )
        except Exception:
            if added:
                await target.remove_roles(role, reason="Staff assignment rollback")
            if removed_old:
                await target.add_roles(old_role, reason="Staff assignment rollback")
            if tickets:
                await tickets.sync_staff_access(
                    guild, target, set(old["permissions"]) if old else set()
                )
            raise
        await bot.store.audit(
            guild.id, actor.id, "staff.save", f"{target.id}: {sorted(permissions)}"
        )


async def remove_staff(bot, guild, actor, target):
    if not admin(actor):
        raise ValueError("Administrator permission is required.")
    check_target(actor, target)
    async with bot.guild_locks[guild.id]:
        row = await bot.store.staff(guild.id, target.id)
        if not row:
            raise ValueError("That member is not assigned as staff.")
        role = guild.get_role(row["role_id"]) if row["role_id"] else None
        tickets = bot.get_cog("Tickets")
        if tickets:
            await tickets.sync_staff_access(guild, target, set())
        try:
            if role and role in target.roles:
                await target.remove_roles(role, reason=f"Staff revoked by {actor.id}")
            await bot.store.execute(
                "DELETE FROM staff WHERE guild_id=? AND user_id=?",
                (guild.id, target.id),
            )
        except Exception:
            if tickets:
                await tickets.sync_staff_access(guild, target, set(row["permissions"]))
            raise
        await bot.store.audit(guild.id, actor.id, "staff.remove", target.id)


class StaffView(discord.ui.View):
    def __init__(self, bot, actor, target, selected):
        super().__init__(timeout=300)
        self.bot, self.actor_id, self.target_id = bot, actor.id, target.id
        self.selected, self.message = set(selected), None
        for index, (key, (label, _)) in enumerate(PERMISSIONS.items()):
            button = discord.ui.Button(
                label=label,
                row=index // 4,
                style=discord.ButtonStyle.success
                if key in selected
                else discord.ButtonStyle.secondary,
            )

            async def toggle(interaction, key=key, button=button):
                if key in self.selected:
                    self.selected.remove(key)
                else:
                    self.selected.add(key)
                button.style = (
                    discord.ButtonStyle.success
                    if key in self.selected
                    else discord.ButtonStyle.secondary
                )
                await interaction.response.edit_message(view=self)

            button.callback = toggle
            self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.actor_id or not admin(interaction.user):
            await interaction.response.send_message(
                "Only the administrator who opened this selector can use it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Done", style=discord.ButtonStyle.primary, row=3)
    async def done(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            actor = await interaction.guild.fetch_member(self.actor_id)
            target = await interaction.guild.fetch_member(self.target_id)
            await set_staff(self.bot, interaction.guild, actor, target, self.selected)
        except (ValueError, commands.CommandError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(
            embed=embed(
                "Staff permissions saved",
                f"{target.mention}\n"
                + (
                    "\n".join("✓ " + PERMISSIONS[p][0] for p in sorted(self.selected))
                    or "No command permissions selected."
                ),
            ),
            view=self,
        )
        await interaction.followup.send(
            "Saved to the database and the configured staff role was assigned.",
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=3)
    async def cancel(self, interaction, button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Staff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
    @require_admin()
    async def staff(self, ctx, member: discord.Member):
        """Select a member's permissions with buttons, then save."""
        config, _ = await self.bot.store.config(ctx.guild.id)
        if not config["staff_role_id"]:
            return await ctx.send(
                embed=embed(
                    "Set a staff role first",
                    f"Use `{ctx.clean_prefix}staffrole @role` or Dashboard → Staff.",
                )
            )
        check_target(ctx.author, member)
        row = await self.bot.store.staff(ctx.guild.id, member.id)
        view = StaffView(
            self.bot, ctx.author, member, row["permissions"] if row else []
        )
        view.message = await ctx.send(
            embed=embed(
                f"Staff permissions · {member.display_name}",
                "Select permissions below. Green means selected. Press **Done** to save and assign the staff role.",
            ),
            view=view,
        )

    @staff.command(name="remove")
    @require_admin()
    async def staff_remove(self, ctx, member: discord.Member):
        await remove_staff(self.bot, ctx.guild, ctx.author, member)
        await ctx.send(embed=embed("Staff access removed", member.mention))

    @staff.command(name="list")
    @require_admin()
    async def staff_list(self, ctx):
        rows = await self.bot.store.rows(
            "SELECT user_id,permissions FROM staff WHERE guild_id=?", (ctx.guild.id,)
        )
        lines = [f"<@{r['user_id']}> · {r['permissions']}" for r in rows]
        for start in range(0, max(1, len(lines)), 10):
            await ctx.send(
                embed=embed(
                    "Assigned staff",
                    "\n".join(lines[start : start + 10]) or "No staff assigned.",
                )
            )

    @commands.command()
    @require_admin()
    async def staffrole(self, ctx, role: discord.Role):
        """Choose the role assigned after staff permissions are saved."""
        check_role(role, ctx.author, safe=True)
        async with self.bot.guild_locks[ctx.guild.id]:
            config, version = await self.bot.store.config(ctx.guild.id)
            if (
                str(role.id) in config["ticket"]["support_role_ids"]
                or str(role.id) in config["greet"]["autorole_ids"]
                or any(r["role_id"] == str(role.id) for r in config["reaction_roles"])
            ):
                raise ValueError(
                    "The staff role must not be a support, join, or reaction role."
                )
            config["staff_role_id"] = str(role.id)
            await self.bot.store.save_config(ctx.guild.id, config, version)
        await ctx.send(
            embed=embed(
                "Staff role configured",
                f"{role.mention}. Existing assignments change role when you save that member again.",
            )
        )
