"""Compact category help, backed by the commands actually loaded."""

from collections import OrderedDict
from difflib import get_close_matches
import discord
from discord.ext import commands
from .branding import embed

CATEGORIES = [
    ("🛡️", "Security", {"Antinuke"}),
    ("🚨", "Automoderation", {"Automod"}),
    ("🛠️", "Moderation", {"Moderation"}),
    ("👥", "Staff", {"Staff"}),
    ("🎫", "Tickets", {"Tickets"}),
    ("📊", "Invite Tracking", {"Tracking"}),
    ("🏆", "Events & Rewards", {"Events"}),
    ("⚙️", "Server Setup & Utility", {"Community", "ServerTools"}),
]


def categories(bot):
    result = OrderedDict()
    for command in sorted(bot.commands, key=lambda c: c.name):
        if command.hidden or not command.enabled or command.name.startswith("__"):
            continue
        label = next(
            (
                title
                for emoji, title, names in CATEGORIES
                if command.cog and command.cog.__class__.__name__ in names
            ),
            "Other",
        )
        result.setdefault(label, []).append(command)
    order = [title for emoji, title, _ in CATEGORIES] + ["Other"]
    return OrderedDict((label, result[label]) for label in order if label in result)


def command_pages(ctx, title, items):
    pages = []
    for offset in range(0, len(items), 6):
        page = embed(
            title,
            f"Use `{ctx.clean_prefix}help <command>` for details.\n<argument> is required; [argument] is optional.",
        )
        for command in items[offset : offset + 6]:
            usage = f"{ctx.clean_prefix}{command.qualified_name} {command.signature}".strip()
            description = command.short_doc or "Use this command to see its options."
            page.add_field(name=usage[:256], value=description[:600], inline=False)
        pages.append(page)
    return pages or [embed(title, "No commands are currently loaded in this category.")]


class CategorySelect(discord.ui.Select):
    def __init__(self, groups):
        options = [discord.SelectOption(label="Home", value="0")]
        options += [
            discord.SelectOption(label=label, value=str(index))
            for label, index in groups
        ]
        super().__init__(placeholder="Choose a category", options=options, row=1)

    async def callback(self, interaction):
        await self.view.show(interaction, int(self.values[0]))


class HelpView(discord.ui.View):
    def __init__(self, ctx, pages, groups=()):
        super().__init__(timeout=180)
        self.ctx, self.pages, self.index = ctx, pages, 0
        if groups:
            self.add_item(CategorySelect(groups))
        self.refresh()

    def refresh(self):
        self.home.disabled = self.previous.disabled = self.index == 0
        self.next.disabled = self.last.disabled = self.index == len(self.pages) - 1
        self.pages[self.index].set_footer(text=f"Page {self.index+1}/{len(self.pages)}")

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Run the help command to open your own menu.", ephemeral=True
            )
            return False
        return True

    async def show(self, interaction, index):
        self.index = max(0, min(index, len(self.pages) - 1))
        self.refresh()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="First", style=discord.ButtonStyle.secondary, row=0)
    async def home(self, interaction, button):
        await self.show(interaction, 0)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction, button):
        await self.show(interaction, self.index - 1)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=0)
    async def close_menu(self, interaction, button):
        await interaction.response.edit_message(view=None)
        self.stop()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction, button):
        await self.show(interaction, self.index + 1)

    @discord.ui.button(label="Last", style=discord.ButtonStyle.secondary, row=0)
    async def last(self, interaction, button):
        await self.show(interaction, len(self.pages) - 1)


class OlympusHelp(commands.HelpCommand):
    def __init__(self):
        super().__init__(
            command_attrs={
                "name": "help",
                "aliases": ["h"],
                "help": "Show all command categories, or help for a command/category.",
            },
            verify_checks=False,
        )

    async def command_callback(self, ctx, *, command=None):
        self.context = ctx
        if command:
            command = command.strip()
            for label, items in categories(ctx.bot).items():
                if command.casefold() == label.casefold():
                    return await self.send_pages(command_pages(ctx, label, items))
            # Resolve original cog names and mixed-case commands too.
            cog = next(
                (
                    c
                    for c in ctx.bot.cogs.values()
                    if c.qualified_name.casefold() == command.casefold()
                ),
                None,
            )
            if cog:
                return await self.send_cog_help(cog)
        return await super().command_callback(ctx, command=command)

    async def send_pages(self, pages, groups=()):
        view = HelpView(self.context, pages, groups)
        await self.context.send(embed=pages[0], view=view)

    async def send_bot_help(self, mapping):
        ctx = self.context
        sections = categories(ctx.bot)
        home = embed(
            "FortuneManager",
            f"Prefix: `{ctx.clean_prefix}`\n"
            f"Use `{ctx.clean_prefix}help <command>` for usage and options.\n"
            "Choose a category below to browse commands.",
        )
        for label, items in sections.items():
            names = ", ".join(f"`{c.name}`" for c in items[:5])
            if len(items) > 5:
                names += f" +{len(items)-5}"
            home.add_field(name=label, value=names, inline=True)
        pages, groups = [home], []
        for label, items in sections.items():
            groups.append((label, len(pages)))
            pages.extend(command_pages(ctx, label, items))
        await self.send_pages(pages, groups)

    async def send_command_help(self, command):
        ctx = self.context
        page = embed(
            f"{ctx.clean_prefix}{command.qualified_name}",
            command.help or "No additional description.",
        )
        page.add_field(
            name="Usage",
            value=f"`{ctx.clean_prefix}{command.qualified_name} {command.signature}`"[
                :1024
            ],
            inline=False,
        )
        page.add_field(
            name="Aliases", value=", ".join(command.aliases) or "None", inline=False
        )
        page.add_field(
            name="Arguments",
            value="<argument> = required • [argument] = optional",
            inline=False,
        )
        await ctx.send(embed=page)

    async def send_group_help(self, group):
        items = sorted(
            (c for c in group.commands if not c.hidden), key=lambda c: c.name
        )
        pages = command_pages(
            self.context, f"{self.context.clean_prefix}{group.qualified_name}", items
        )
        pages[0].description = (group.help or "") + "\n\n" + pages[0].description
        await self.send_pages(pages)

    async def send_cog_help(self, cog):
        await self.send_pages(
            command_pages(
                self.context,
                cog.qualified_name,
                sorted(
                    (c for c in cog.get_commands() if not c.hidden),
                    key=lambda c: c.name,
                ),
            )
        )

    async def command_not_found(self, string):
        matches = get_close_matches(
            string, [c.qualified_name for c in self.context.bot.walk_commands()]
        )
        return (
            f"No loaded command or category named `{discord.utils.escape_markdown(string)}`."
            + (
                "\nDid you mean: " + ", ".join(f"`{name}`" for name in matches)
                if matches
                else "\nUse `help` to view the categories."
            )
        )

    async def send_error_message(self, error):
        await self.context.send(embed=embed("Help", str(error)[:3500]))
