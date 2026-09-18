"""Apply branding at serialization, including legacy and interaction embeds."""

import discord
from .settings import LOGO_URL, BANNER_URL

_original = discord.Embed.to_dict


def branded_dict(self):
    data = _original(self)
    data["author"] = {
        "name": ".gg/fortuneleaf",
        "icon_url": LOGO_URL,
        "url": "https://discord.gg/fortuneleaf",
    }
    data.setdefault("timestamp", discord.utils.utcnow().isoformat())
    data["thumbnail"] = {"url": LOGO_URL}
    # Preserve a meaningful image (avatar, generated image, or user-configured embed).
    data.setdefault("image", {"url": BANNER_URL})
    data.setdefault("footer", {"text": "FortuneManager"})
    return data


def install_branding():
    if discord.Embed.to_dict is not branded_dict:
        discord.Embed.to_dict = branded_dict


def embed(title=None, description=None, **kwargs):
    return discord.Embed(
        title=title,
        description=description,
        color=kwargs.pop("color", 0x63D6AC),
        timestamp=discord.utils.utcnow(),
        **kwargs,
    )
