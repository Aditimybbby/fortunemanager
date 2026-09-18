import discord
from discord.ext import commands
import re
import aiohttp
import os

class Emoji(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="emoji")
    @commands.has_permissions(manage_emojis=True)
    async def upload_emojis(self, ctx):
        """Finds all custom emojis in the codebase, uploads them to the current server, and updates the codebase."""
        msg = await ctx.send("Scanning codebase for emojis...")
        
        emoji_pattern = re.compile(r'<a?:[a-zA-Z0-9_]+:([0-9]+)>')
        found_emojis = {} # ID: Name
        
        # Scan files
        for root, dirs, files in os.walk("."):
            for file in files:
                if file.endswith((".py", ".yml", ".json")):
                    try:
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            content = f.read()
                            matches = re.findall(r'<a?(:[a-zA-Z0-9_]+:)([0-9]+)>', content)
                            for name, eid in matches:
                                found_emojis[eid] = name.strip(':')
                    except:
                        continue

        if not found_emojis:
            return await msg.edit(content="No custom emojis found in the codebase.")

        await msg.edit(content=f"Found {len(found_emojis)} unique emojis. Uploading to this server...")

        new_emoji_map = {} # Old ID: New Emoji String
        
        async with aiohttp.ClientSession() as session:
            for eid, name in found_emojis.items():
                # Check if emoji already exists in guild to avoid duplicates
                existing = discord.utils.get(ctx.guild.emojis, name=name)
                if existing:
                    new_emoji_map[eid] = str(existing)
                    continue

                try:
                    url = f"https://cdn.discordapp.com/emojis/{eid}.png"
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            img_data = await resp.read()
                            new_emoji = await ctx.guild.create_custom_emoji(name=name, image=img_data)
                            new_emoji_map[eid] = str(new_emoji)
                        else:
                            # Try gif if png fails
                            url = f"https://cdn.discordapp.com/emojis/{eid}.gif"
                            async with session.get(url) as resp2:
                                if resp2.status == 200:
                                    img_data = await resp2.read()
                                    new_emoji = await ctx.guild.create_custom_emoji(name=name, image=img_data)
                                    new_emoji_map[eid] = str(new_emoji)
                except Exception as e:
                    print(f"Failed to upload {name}: {e}")

        await msg.edit(content="Emojis uploaded. Updating codebase...")

        # Replace in files
        replaced_count = 0
        for root, dirs, files in os.walk("."):
            if ".git" in root or "__pycache__" in root:
                continue
            for file in files:
                if file.endswith((".py", ".yml", ".json")):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        new_content = content
                        for old_id, new_emoji_str in new_emoji_map.items():
                            # Pattern to match the whole emoji string with that ID
                            pattern = rf'<a?:[a-zA-Z0-9_]+:{old_id}>'
                            new_content = re.sub(pattern, new_emoji_str, new_content)
                        
                        if new_content != content:
                            with open(file_path, 'w', encoding='utf-8') as f:
                                f.write(new_content)
                            replaced_count += 1
                    except Exception as e:
                        print(f"Failed to update {file_path}: {e}")

        await msg.edit(content=f"Done! Uploaded emojis and updated {replaced_count} files. Restart the bot to see changes.")

async def setup(bot):
    await bot.add_cog(Emoji(bot))
