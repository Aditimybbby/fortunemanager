import discord
from discord.ext import commands

PERMISSIONS = {
    "kick": ("Kick members", "kick_members"),
    "ban": ("Ban / unban", "ban_members"),
    "mute": ("Mute / unmute", "moderate_members"),
    "timeout": ("Timeout members", "moderate_members"),
    "warn": ("Warn members", "moderate_members"),
    "purge": ("Delete messages", "manage_messages"),
    "lock": ("Lock / unlock", "manage_channels"),
    "delete_channel": ("Delete channels", "manage_channels"),
    "slowmode": ("Set slowmode", "manage_channels"),
    "ticket_access": ("Ticket access", None),
    "ticket_manage": ("Manage tickets", None),
    "nickname": ("Change nicknames", "manage_nicknames"),
}
DANGEROUS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "kick_members",
    "ban_members",
    "moderate_members",
    "manage_webhooks",
    "manage_messages",
    "manage_nicknames",
    "mention_everyone",
    "view_audit_log",
    "mute_members",
    "deafen_members",
    "move_members",
    "manage_expressions",
    "manage_events",
    "manage_threads",
)


def admin(member):
    return member.id == member.guild.owner_id or member.guild_permissions.administrator


async def allowed(bot, member, permission):
    if not isinstance(member, discord.Member):
        return False
    if admin(member):
        return True
    row = await bot.store.staff(member.guild.id, member.id)
    # An assigned member uses the explicit grant list, even if they hold native permissions.
    if row:
        return permission in row["permissions"]
    native = PERMISSIONS[permission][1]
    return bool(native and getattr(member.guild_permissions, native, False))


def require(permission):
    async def predicate(ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        if not await allowed(ctx.bot, ctx.author, permission):
            raise commands.CheckFailure(
                f"You need the {PERMISSIONS[permission][0]} staff permission."
            )
        return True

    return commands.check(predicate)


def require_admin():
    async def predicate(ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        if not admin(ctx.author):
            raise commands.CheckFailure(
                "Only the server owner or an administrator can manage staff."
            )
        return True

    return commands.check(predicate)


def check_target(actor, target):
    guild = actor.guild
    if target.id in (actor.id, guild.owner_id, guild.me.id):
        raise commands.BadArgument(
            "You cannot target yourself, the server owner, or this bot."
        )
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        raise commands.BadArgument("The member must be below your highest role.")
    if target.top_role >= guild.me.top_role:
        raise commands.BadArgument("Move the bot role above the target member.")


def check_role(role, actor, *, safe=False):
    if role is None or role.is_default() or role.managed:
        raise ValueError("Choose a normal, assignable role.")
    if role >= role.guild.me.top_role:
        raise ValueError("The role must be below the bot role.")
    if actor.id != role.guild.owner_id and role >= actor.top_role:
        raise ValueError("The role must be below your highest role.")
    if safe and any(getattr(role.permissions, p, False) for p in DANGEROUS):
        raise ValueError(
            "Use a role without moderation or administrative permissions. The bot enforces the selected grants."
        )
    if safe:
        for channel in role.guild.channels:
            allow, _ = channel.overwrites_for(role).pair()
            if any(getattr(allow, permission, False) for permission in DANGEROUS):
                raise ValueError(
                    "This role has moderation permissions in a channel. Choose a role without those permissions."
                )
