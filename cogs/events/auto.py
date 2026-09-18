import discord
from discord.utils import *
from core import FortuneManager, Cog
from utils.Tools import *
from utils.config import BotName, serverLink
from discord.ext import commands
from discord.ui import Button, View

class Autorole(Cog):
    def __init__(self, bot: FortuneManager):
        self.bot = bot


    @commands.Cog.listener(name="on_guild_join")
    async def send_msg_to_adder(self, guild: discord.Guild):
        async for entry in guild.audit_logs(limit=3):
            if entry.action == discord.AuditLogAction.bot_add:
                embed = discord.Embed(
                    description=f"<:mail:1299664322225635338> **Thanks for adding me.**\n\n<:red_arrow:1247226071783440404> My default prefix is `$`\n<:red_arrow:1247226071783440404> Use the `$help` command to see a list of commands\n<:red_arrow:1247226071783440404> For detailed guides, FAQ and information, visit our **[Support Server](https://discord.gg/fortuneleaf)**",
                    color=0xff0000
                )
                embed.set_thumbnail(url=entry.user.avatar.url if entry.user.avatar else entry.user.default_avatar.url)
                embed.set_author(name=f"{guild.name}", icon_url=guild.me.display_avatar.url)
                embed.set_footer(text="Powered by FortuneManager Development™", icon_url="https://cdn.discordapp.com/icons/699587669059174461/f689b4366447d5a23eda8d0ec749c1ba.png")
                website_button = Button(label='Website', style=discord.ButtonStyle.link, url='https://olyumpus.vercel.app/')
                support_button = Button(label='Support', style=discord.ButtonStyle.link, url='https://discord.gg/fortuneleaf')
                vote_button = Button(label='Vote for Me', style=discord.ButtonStyle.link, url=f'https://top.gg/bot/{self.bot.user.id}/vote')
                view = View()
                view.add_item(support_button)
                view.add_item(website_button)
                #view.add_item(vote_button)
                if guild.icon:
                    embed.set_author(name=guild.name, icon_url=guild.icon.url)
                try:
                    await entry.user.send(embed=embed, view=view)
                except Exception as e:
                    print(e)
