import discord
import psutil
import sys
import os
import time
import aiosqlite
import platform
import pkg_resources
import datetime
from discord import Embed, ButtonStyle
from discord.ui import Button, View
from discord.ext import commands
from utils.Tools import *
import aiosqlite 
import wavelink

class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()
        self.total_songs_played = 0
        self.bot.loop.create_task(self.setup_database())

    
    async def setup_database(self):
        async with aiosqlite.connect("db/stats.db") as db:
           # await db.execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)")
           # await db.commit()  
            async with db.execute("SELECT value FROM stats WHERE key = 'total_songs_played'") as cursor:
                row = await cursor.fetchone()
                self.total_songs_played = row[0] if row else 0

    async def update_total_songs_played(self):
        async with aiosqlite.connect("db/stats.db") as db:
            await db.execute("INSERT OR REPLACE INTO stats (key, value) VALUES ('total_songs_played', ?)", (self.total_songs_played,))
            await db.commit()

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        self.total_songs_played += 1
        await self.update_total_songs_played()

    def count_code_stats(self, file_path):
        total_lines = 0
        total_words = 0
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                for line in file:
                    stripped_line = line.strip()
                    if stripped_line and not stripped_line.startswith(('〇')):
                        total_lines += 1
                        total_words += len(stripped_line.split())
        except (UnicodeDecodeError, IOError):
            pass
        return total_lines, total_words

    def gather_file_stats(self, directory):
        total_files = 0
        total_lines = 0
        total_words = 0
        for root, _, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                if file.endswith('.py') and '.local' not in root:
                    total_files += 1
                    file_lines, file_words = self.count_code_stats(file_path)
                    total_lines += file_lines
                    total_words += file_words
        return total_files, total_lines, total_words

    @commands.hybrid_command(name="stats", aliases=["botinfo", "botstats", "bi", "statistics"], help="Shows the bot's information.")
    @blacklist_check()
    @ignore_check()
    @commands.cooldown(1, 7, commands.BucketType.user)
    async def stats(self, ctx):
        processing_message = await ctx.send("<a:loading:1272527164256030873> Loading FortuneManager information...")
        
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count for g in self.bot.guilds if g.member_count is not None)
        bot_count = sum(sum(1 for m in g.members if m.bot) for g in self.bot.guilds)
        human_count = user_count - bot_count
        channel_count = len(set(self.bot.get_all_channels()))
        blahh = human_count + bot_count
        text_channel_count = len([c for c in self.bot.get_all_channels() if isinstance(c, discord.TextChannel)])
        voice_channel_count = len([c for c in self.bot.get_all_channels() if isinstance(c, discord.VoiceChannel)])
        category_channel_count = len([c for c in self.bot.get_all_channels() if isinstance(c, discord.CategoryChannel)])
        slash_commands = len([cmd for cmd in self.bot.tree.get_commands()])
        commands_count = len(set(self.bot.walk_commands()))
        uptime_seconds = int(round(time.time() - self.start_time))
        uptime_timedelta = datetime.timedelta(seconds=uptime_seconds)
        uptime = f"{uptime_timedelta.days} days, {uptime_timedelta.seconds // 3600} hours, {(uptime_timedelta.seconds // 60) % 60} minutes, {uptime_timedelta.seconds % 60} seconds"

        total_files, total_lines, total_words = self.gather_file_stats('.')

        cpu_info = psutil.cpu_freq()
        memory_info = psutil.virtual_memory()
        
        total_libraries = sum(1 for _ in pkg_resources.working_set)
        channels_connected = sum(1 for vc in self.bot.voice_clients if vc)
        playing_tracks = sum(1 for vc in self.bot.voice_clients if vc.playing)

        embed = Embed(title="FortuneManager Statistics: General", color=0x000000)
        embed.add_field(name="<:channel:1204242537804734544> Channels", value=f"Total: **{channel_count}**\nText: **{text_channel_count}**   |   Voice: **{voice_channel_count}**   |   Category: **{category_channel_count}**", inline=False)
        embed.add_field(name="<:uptime:1292499357660086343> Uptime", value=f"{uptime}", inline=False)
        embed.add_field(name="<a:users:1292506970019987578> User Count", value=f"Humans: **{human_count}**   |   Bots: **{bot_count}**", inline=False)
        embed.add_field(name="<:Folders:1302319651476340748> Commands", value=f"Total: **{commands_count}**   |   Slash: **{slash_commands}**", inline=False)
        embed.add_field(name="<:python:1219641847262085210> Libraries Used", value=f"Discord Library: **[discord.py](https://discordpy.readthedocs.io/en/stable/)**\nTotal Libraries: **{total_libraries}**", inline=False)
        embed.add_field(name="<:code:1292498725930930279> Codebase Stats", value=f"Total Python Files: **{total_files}**\nTotal Lines: **{total_lines}**\nTotal Words: **{total_words}**", inline=False)
        embed.add_field(
    name="<:gvMusic:1213831433219481722> Music Stats",
    value=f"Currently Connected: **[{channels_connected}](https://discord.gg/fortuneleaf)**\n"
          f"Currently Playing: **[{playing_tracks}](https://discord.gg/fortuneleaf)**\n"
          f"Total Songs Played: **[{self.total_songs_played}](https://discord.gg/fortuneleaf)**",
    inline=False
        )
        embed.set_footer(text="Powered by FortuneManager Development™", icon_url=self.bot.user.display_avatar.url)

        view = View()
        

        general_button = Button(label="General", style=ButtonStyle.gray)
        async def general_button_callback(interaction):
            if interaction.user == ctx.author:
                await interaction.response.edit_message(embed=embed, view=view)
        general_button.callback = general_button_callback
        view.add_item(general_button)

        system_button = Button(label="System", style=ButtonStyle.gray)
        async def system_button_callback(interaction):
            if interaction.user == ctx.author:
                system_embed = Embed(title="FortuneManager Statistics: System", color=0x000000)

                system_embed.add_field(name="<:system:1292507895379918891> System Info", value=f"• Discord.py: **{discord.__version__}**\n• Python: **{platform.python_version()}**\n• Architecture: **{platform.machine()}**\n• Platform: **{platform.system()}**", inline=False)

                system_embed.add_field(name="<:memory_:1292508839132008533> Memory Info", value=f"• Total Memory: **{memory_info.total / (1024 ** 2):,.2f} MB**\n• Memory Left: **{memory_info.available / (1024 ** 2):,.2f} MB**\n• Heap Total: **{memory_info.used / (1024 ** 2):,.2f} MB**", inline=False)
                system_embed.add_field(name="<:cpu:1292508956882767975> CPU Info", value=f"• CPU: **{psutil.cpu_freq().max}' GHz**\n• CPU Usage: **{psutil.cpu_percent()}%**\n• CPU Cores: **{psutil.cpu_count(logical=False)}**\n• CPU Speed: **{cpu_info.current:.2f} MHz**", inline=False)
                system_embed.set_footer(text="Powered by FortuneManager Development™", icon_url=self.bot.user.display_avatar.url)
                
                await interaction.response.edit_message(embed=system_embed, view=view)
        system_button.callback = system_button_callback
        view.add_item(system_button)


        ping_button = Button(label="Ping", style=ButtonStyle.green)
        async def ping_button_callback(interaction):
            if interaction.user == ctx.author:
                s_id = ctx.guild.shard_id
                sh = self.bot.get_shard(s_id)

                db_latency = None
                try:
                    async with aiosqlite.connect("db/afk.db") as db:
                        start_time = time.perf_counter()
                        await db.execute("SELECT 1")
                        end_time = time.perf_counter()
                        db_latency = (end_time - start_time) * 1000
                        db_latency = round(db_latency, 2)
                except Exception as e:
                    db_latency = "N/A"

                wsping = round(self.bot.latency * 1000, 2)

                ping_embed = Embed(title="Bot Statistic: Ping", color=0x000000)
                ping_embed.add_field(name="<:pong:1218740734463840348> Bot Latency", value=f"{round(sh.latency * 800)} ms", inline=False)
                ping_embed.add_field(name="<:websocket:1292512590379094139> Database Latency", value=f"{db_latency} ms", inline=False)
                ping_embed.add_field(name="<:database:1292512419016347762> Websocket Latency", value=f"{wsping} ms", inline=False)
                ping_embed.set_footer(text="Powered by FortuneManager Development™", icon_url=self.bot.user.display_avatar.url)
                await interaction.response.edit_message(embed=ping_embed, view=view)
        ping_button.callback = ping_button_callback
        view.add_item(ping_button)

        

        """team_button = Button(label="Team", style=ButtonStyle.primary)
        async def team_button_callback(interaction):
            if interaction.user == ctx.author:
                team_embed = Embed(title="FortuneManager Team", color=0x000000)
                team_embed.add_field(name="**<:FortuneManager_owner:1228227536207740989> Bot Owner(s)**", value=">>> **[Sonu](https://discord.com/users/1070619070468214824)**,   **[!⌁𓆩ζ͜͡𝘿𝙉𝘼 𝙎𝙚𝙧𝙞𝙚𝙨ㄚㄒᥫ᭡](https://discord.com/users/677952614390038559)**,   **[Pritam](https://discord.com/users/1087282349395411015)**,   **[CuTeBoY.Ly](https://discord.com/users/995898882607292506)**", inline=False)
                team_embed.add_field(name="**<:FortuneManager_developer:1250290657256538196> Bot Developer(s)**", value="> **[Sonu!?](https://discord.com/users/213347081799073793)** (Lead Developer)", inline=False)
                team_embed.add_field(name="**<:FortuneManager_general:1222789674687397930> Web Developer(s)**", value="> **[Love](https://discord.com/users/773755998665441280)** (Lead Web Developer)", inline=False)
                team_embed.add_field(name="**<:FortuneManager_utility:1222787342474154094> Tester(s)**", value="> **[! Lucifer](https://discord.com/users/1113040686686674987)**", inline=False)
                team_embed.add_field(name="**<:FortuneManager_team:1243399230651895809> Team(s)**", value="> **[FortuneManager Development™](https://discord.gg/fortuneleaf)**", inline=False)
                team_embed.add_field(name="**<:FortuneManager_partner:1228228301089144976> Partner(s)**", value="> **[Endercloud](https://endercloud.in/)**", inline=False)
                team_embed.set_footer(text="Powered by FortuneManager Development™", icon_url=self.bot.user.display_avatar.url)
                await interaction.response.edit_message(embed=team_embed, view=view)
        team_button.callback = team_button_callback
        view.add_item(team_button)"""

        
        delete_button = Button(label="🗑️", style=ButtonStyle.red)
        async def delete_button_callback(interaction):
            if interaction.user == ctx.author:
                await interaction.message.delete()
        delete_button.callback = delete_button_callback
        view.add_item(delete_button)
        

        server_count_button = Button(label=f"Servers: {guild_count}    |    Users: {blahh}", style=ButtonStyle.success, disabled=True)
        view.add_item(server_count_button)
        

        await ctx.reply(embed=embed, view=view)
        await processing_message.delete()

"""
@Author: Sonu Jana
    + Discord: me.sonu
    + Community: https://discord.gg/fortuneleaf (FortuneManager Development)
    + for any queries reach out Community or DM me.
"""