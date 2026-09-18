import discord
from discord.ext import commands
import asyncio

class React(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        for owner in self.bot.owner_ids:
            if f"<@{owner}>" in message.content:
                try:
                    if owner == 677952614390038559:
                        
                        emojis = [
                            "<a:owner:1272731689948287068>",
                            "<:7club_ban:1274766732786925750>",
                            "<:land_yildiz:1274781969640722475>",
                            "<a:_rose:1291476899557671054>",
                            "<:land_yildiz:1274766735702233089>",
                            "<a:37496alert:1273959128490049556>",
                            "<:sq_HeadMod:1292538970500235366>",
                            "<:Dc_RedCrownEsports:1287302832244129823>",
                            "<a:GIFD:1275850452323401789>",
                            "<a:GIFN:1275850451212042391>",
                            "<a:max__A:1295014945641201685>",
                            "<:Heeriye:1274769360560328846>",
                            "<:heart_em:1274781856406962250>",
                            "<a:Star:1273588820373147803>",
                            "<a:king:1234399917792034846>",
                            "<:headmod:1274781954482376857>",
                            "<a:sg_rd:1273974278433280122> ",
                            "<a:RedHeart:1272229548280512547>",
                            " <a:star:1251876754516349059>"
                        ]
                        for emoji in emojis:
                            await message.add_reaction(emoji)
                    else:
                        
                        await message.add_reaction("<a:owner:1272731689948287068>")
                except discord.errors.RateLimited as e:
                    await asyncio.sleep(e.retry_after)
                    await message.add_reaction("<a:owner:1272731689948287068>")
                except Exception as e:
                    print(f"An unexpected error occurred Auto react owner mention: {e}")
