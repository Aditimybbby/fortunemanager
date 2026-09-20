"""Plain Discord embeds. No global authors, thumbnails, banners, or advertising."""

import discord


def install_branding():
    """Compatibility hook; standard Embed serialization is kept."""
    return None


def embed(title=None, description=None, **kwargs):
    return discord.Embed(
        title=title,
        description=description,
        color=kwargs.pop("color", 0x5865F2),
        **kwargs
    )
