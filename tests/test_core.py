import asyncio
import copy
import json
from collections import defaultdict
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import discord
import pytest
from discord.ext import commands
from fortune.bot import FortuneManager
from fortune.branding import install_branding
from fortune.configuration import validate_config
from fortune.permissions import allowed, check_target, check_role
from fortune.staff import set_staff, remove_staff, StaffView
from fortune.moderation import Moderation, duration
from fortune.store import Store, DEFAULT_CONFIG
from fortune.tickets import Tickets, PanelView, TicketControls


class Role:
    def __init__(self, id, position, permissions=None):
        self.id, self.position, self.permissions = (
            id,
            position,
            permissions or discord.Permissions.none(),
        )
        self.managed = False
        self.guild = None
        self.name = f"Role {id}"

    def __ge__(self, o):
        return self.position >= o.position

    def __le__(self, o):
        return self.position <= o.position

    def is_default(self):
        return self.position == 0


def member(guild, id, rank, permissions=None):
    m = MagicMock(spec=discord.Member)
    m.guild = guild
    m.id = id
    m.top_role = Role(id, rank)
    m.roles = [m.top_role]
    m.guild_permissions = permissions or discord.Permissions.none()
    m.bot = False
    m.display_name = f"Member {id}"
    m.mention = f"<@{id}>"
    m.add_roles = AsyncMock()
    m.remove_roles = AsyncMock()
    m.kick = AsyncMock()
    m.ban = AsyncMock()
    m.timeout = AsyncMock()
    m.edit = AsyncMock()
    return m


def world():
    guild = MagicMock(spec=discord.Guild)
    guild.id = 100
    guild.owner_id = 1
    guild.name = "Test server"
    guild.member_count = 3
    guild.me = member(guild, 9, 90, discord.Permissions.all())
    guild.default_role = Role(100, 0)
    owner = member(
        guild, 1, 80, discord.Permissions(administrator=True, manage_guild=True)
    )
    staff = member(guild, 2, 20)
    target = member(guild, 3, 5)
    role = Role(40, 10)
    role.guild = guild
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 200
    category.name = "Tickets"
    category.permissions_for.return_value = discord.Permissions.all()
    category.overwrites_for.return_value = discord.PermissionOverwrite()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 300
    channel.guild = guild
    channel.name = "test"
    channel.mention = "<#300>"
    channel.topic = ""
    channel.send = AsyncMock(
        return_value=NS(
            id=500,
            jump_url="https://discord.com/channels/100/300/500",
            delete=AsyncMock(),
        )
    )
    channel.set_permissions = AsyncMock()
    channel.delete = AsyncMock()
    channel.edit = AsyncMock()
    channel.permissions_for.return_value = discord.Permissions.all()
    channel.overwrites_for.return_value = discord.PermissionOverwrite(
        send_messages=None, send_messages_in_threads=True, attach_files=True
    )

    async def history(**kwargs):
        yield NS(
            created_at=discord.utils.utcnow(),
            author=owner,
            content="Hello, ticket!",
            embeds=[],
            attachments=[],
        )

    channel.history = history
    roles = {40: role}
    channels = {200: category, 300: channel}
    members = {1: owner, 2: staff, 3: target, 9: guild.me}
    guild.get_role.side_effect = roles.get
    guild.get_channel.side_effect = channels.get
    guild.get_member.side_effect = members.get
    guild.fetch_member = AsyncMock(side_effect=lambda id: members[id])
    guild.create_text_channel = AsyncMock(return_value=channel)
    guild.roles = [guild.default_role, role, guild.me.top_role]
    guild.channels = [category, channel]
    guild.text_channels = [channel]
    guild.members = list(members.values())
    return NS(
        guild=guild,
        owner=owner,
        staff=staff,
        target=target,
        role=role,
        channel=channel,
        category=category,
    )


async def service(tmp_path):
    store = Store(tmp_path / "test.db")
    await store.init()
    bot = NS(
        store=store,
        guild_locks=defaultdict(asyncio.Lock),
        ticket_locks=defaultdict(asyncio.Lock),
        channel_locks=defaultdict(asyncio.Lock),
        get_cog=lambda name: None,
        user=NS(id=9),
    )
    return bot


@pytest.mark.asyncio
async def test_bootstrap_registers_all_requested_commands(tmp_path):
    async with FortuneManager(
        database=tmp_path / "db", start_dashboard=False, load_legacy=False
    ) as bot:
        await bot.setup_hook()
        for name in [
            "kick",
            "ban",
            "mute",
            "timeout",
            "lock",
            "unlock",
            "deletechannel",
            "staff",
            "staff remove",
            "staff list",
            "ticket setup",
            "ticket panel",
            "ticket add",
            "ticket remove",
            "dashboard",
            "greettest",
            "help",
        ]:
            assert bot.get_command(name), name
        assert len(bot.persistent_views) == 3
        assert bot.persistent_views[0].is_persistent()


@pytest.mark.asyncio
async def test_config_restart_and_concurrent_version_conflict(tmp_path):
    bot = await service(tmp_path)
    config, version = await bot.store.config(100)
    assert version == 0
    a = copy.deepcopy(config)
    b = copy.deepcopy(config)
    a["prefix"] = "!"
    b["prefix"] = "?"
    results = await asyncio.gather(
        bot.store.save_config(100, a, 0),
        bot.store.save_config(100, b, 0),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ValueError) for r in results) == 1
    other = Store(bot.store.path)
    await other.init()
    saved, version = await other.config(100)
    assert saved["prefix"] in ("!", "?") and version == 1
    assert (await other.config(101))[0]["prefix"] == "."


@pytest.mark.asyncio
async def test_staff_grants_are_persistent_and_guild_scoped(tmp_path):
    bot = await service(tmp_path)
    w = world()
    w.staff.guild_permissions = discord.Permissions(ban_members=True)
    assert await allowed(bot, w.staff, "ban")
    await bot.store.save_staff(100, 2, {"kick"}, 40, 1)
    assert await allowed(bot, w.staff, "kick")
    assert not await allowed(bot, w.staff, "ban")
    w.guild.id = 101
    assert not await allowed(bot, w.staff, "kick")
    w.guild.id = 100
    await bot.store.save_staff(100, 2, set(), 40, 1)
    assert not await allowed(bot, w.staff, "kick")
    assert await allowed(bot, w.owner, "kick")


@pytest.mark.asyncio
async def test_staff_save_role_and_database_and_revocation(tmp_path):
    bot = await service(tmp_path)
    w = world()
    config, v = await bot.store.config(100)
    config["staff_role_id"] = "40"
    await bot.store.save_config(100, config, v)
    await set_staff(bot, w.guild, w.owner, w.staff, {"kick", "lock"})
    w.staff.add_roles.assert_awaited_once()
    assert set((await bot.store.staff(100, 2))["permissions"]) == {"kick", "lock"}
    w.staff.roles.append(w.role)
    await remove_staff(bot, w.guild, w.owner, w.staff)
    assert await bot.store.staff(100, 2) is None
    w.staff.remove_roles.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_staff_role_assignment_does_not_save(tmp_path):
    bot = await service(tmp_path)
    w = world()
    c, v = await bot.store.config(100)
    c["staff_role_id"] = "40"
    await bot.store.save_config(100, c, v)
    w.staff.add_roles.side_effect = RuntimeError("Discord unavailable")
    with pytest.raises(RuntimeError):
        await set_staff(bot, w.guild, w.owner, w.staff, {"kick"})
    assert await bot.store.staff(100, 2) is None


@pytest.mark.asyncio
async def test_staff_cannot_grant_permissions_and_unknown_grants_rejected(tmp_path):
    bot = await service(tmp_path)
    w = world()
    with pytest.raises(ValueError, match="Administrator"):
        await set_staff(bot, w.guild, w.staff, w.target, {"kick"})
    with pytest.raises(ValueError, match="Unknown"):
        await set_staff(bot, w.guild, w.owner, w.staff, {"administrator"})


@pytest.mark.asyncio
async def test_staff_selector_turns_green_and_rechecks_owner(tmp_path):
    bot = await service(tmp_path)
    w = world()
    view = StaffView(bot, w.owner, w.staff, {"kick"})
    kick = next(b for b in view.children if b.label == "Kick members")
    assert kick.style == discord.ButtonStyle.success
    interaction = NS(
        user=w.owner, response=NS(edit_message=AsyncMock(), send_message=AsyncMock())
    )
    await kick.callback(interaction)
    assert kick.style == discord.ButtonStyle.secondary and "kick" not in view.selected
    interaction.user = w.staff
    assert not await view.interaction_check(interaction)
    view.stop()


@pytest.mark.parametrize(
    "value,seconds", [("10m", 600), ("1s", 1), ("2h", 7200), ("28d", 2419200)]
)
def test_timeout_units(value, seconds):
    assert duration(value).total_seconds() == seconds


@pytest.mark.parametrize("value", ["0m", "29d", "2years", "-1h", "1.5h", "10"])
def test_timeout_invalid(value):
    with pytest.raises(commands.BadArgument):
        duration(value)


def test_role_and_member_hierarchy():
    w = world()
    check_target(w.staff, w.target)
    with pytest.raises(commands.BadArgument):
        check_target(w.staff, w.owner)
    with pytest.raises(commands.BadArgument):
        check_target(w.staff, w.staff)
    w.role.permissions.administrator = True
    with pytest.raises(ValueError, match="without moderation"):
        check_role(w.role, w.owner, safe=True)


def test_embeds_keep_authors_and_explicit_images_without_forced_branding():
    install_branding()
    e = discord.Embed(title="Hello")
    e.set_author(name="Old author")
    e.set_image(url="https://example.com/welcome.png")
    d = e.to_dict()
    assert d["author"]["name"] == "Old author"
    assert "timestamp" not in d
    assert "thumbnail" not in d
    assert d["image"]["url"] == "https://example.com/welcome.png"
    assert "image" not in discord.Embed(title="Default").to_dict()


def test_configuration_rejects_cross_server_channels_and_unsafe_roles():
    w = world()
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["greet"]["channel_id"] = "999"
    with pytest.raises(ValueError, match="belong"):
        validate_config(raw, w.guild, w.owner, DEFAULT_CONFIG)
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["greet"]["autorole_ids"] = ["40"]
    w.role.permissions.ban_members = True
    with pytest.raises(ValueError, match="without moderation"):
        validate_config(raw, w.guild, w.owner, DEFAULT_CONFIG)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(prefix=" " * 2),
        lambda c: c["ticket"].update(max_open=10),
        lambda c: c["greet"].update(image="javascript:alert(1)"),
        lambda c: c["greet"].update(enabled=True),
        lambda c: c["greet"].update(color="#nope"),
        lambda c: c.update(staff_role_id=40),
    ],
)
def test_configuration_invalid_fields(mutation):
    w = world()
    raw = copy.deepcopy(DEFAULT_CONFIG)
    mutation(raw)
    with pytest.raises(ValueError):
        validate_config(raw, w.guild, w.owner, DEFAULT_CONFIG)


def test_staff_role_cannot_be_a_self_assignable_role():
    w = world()
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["staff_role_id"] = "40"
    c["greet"]["autorole_ids"] = ["40"]
    with pytest.raises(ValueError, match="staff role"):
        validate_config(c, w.guild, w.owner, DEFAULT_CONFIG)


PANEL = {
    "id": "support",
    "title": "Support",
    "description": "Choose a category",
    "style": "dropdown",
    "options": [
        {"id": "general", "label": "General", "description": "", "category_id": None}
    ],
}


@pytest.mark.asyncio
async def test_panel_buttons_and_dropdown_are_persistent(tmp_path):
    cog = Tickets(await service(tmp_path))
    for style in ("dropdown", "buttons"):
        panel = copy.deepcopy(PANEL)
        panel["style"] = style
        view = PanelView(cog, panel)
        assert view.is_persistent()
        assert all(c.custom_id for c in view.children)
        view.stop()
    view = TicketControls(cog)
    assert view.is_persistent()
    view.stop()


async def ticket_service(tmp_path):
    bot = await service(tmp_path)
    w = world()
    c, v = await bot.store.config(100)
    c["ticket"].update(enabled=True, category_id="200")
    await bot.store.save_config(100, c, v)
    await bot.store.execute(
        "CREATE TABLE ticket_members(ticket_id INTEGER,user_id INTEGER,PRIMARY KEY(ticket_id,user_id))"
    )
    return bot, w, Tickets(bot)


@pytest.mark.asyncio
async def test_duplicate_ticket_submissions_create_only_one_channel(tmp_path):
    bot, w, cog = await ticket_service(tmp_path)
    outcomes = await asyncio.gather(
        *(
            cog.open_ticket(
                w.guild, w.staff, PANEL, PANEL["options"][0], "Need help", "Details"
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ValueError) for r in outcomes) == 1
    w.guild.create_text_channel.assert_awaited_once()
    assert len(await bot.store.rows("SELECT * FROM tickets WHERE status='open'")) == 1
    overwrites = w.guild.create_text_channel.call_args.kwargs["overwrites"]
    assert overwrites[w.guild.default_role].view_channel is False
    assert overwrites[w.staff].view_channel is True
    assert w.role not in overwrites


@pytest.mark.asyncio
async def test_ticket_creation_failure_cleans_channel_and_limit(tmp_path):
    bot, w, cog = await ticket_service(tmp_path)
    w.channel.send.side_effect = RuntimeError("Send failed")
    with pytest.raises(RuntimeError):
        await cog.open_ticket(
            w.guild, w.staff, PANEL, PANEL["options"][0], "Subject", "Details"
        )
    w.channel.delete.assert_awaited_once()
    assert not await bot.store.rows(
        "SELECT * FROM tickets WHERE status IN ('open','creating')"
    )


@pytest.mark.asyncio
async def test_ticket_close_transcript_reopen_and_claim(tmp_path, monkeypatch):
    import fortune.tickets as mod

    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    bot, w, cog = await ticket_service(tmp_path)
    await cog.open_ticket(
        w.guild, w.staff, PANEL, PANEL["options"][0], "Subject", "Details"
    )
    interaction = NS(
        guild=w.guild, guild_id=100, channel=w.channel, channel_id=300, user=w.staff
    )
    await cog.close_ticket(interaction)
    row = await bot.store.one("SELECT * FROM tickets")
    assert row["status"] == "closed"
    assert (
        "Hello, ticket!"
        in (tmp_path / "transcripts" / "100" / "ticket-1.txt").read_text()
    )
    with pytest.raises(ValueError, match="permission"):
        await cog.reopen(interaction)
    interaction.user = w.owner
    await cog.reopen(interaction)
    await cog.claim(interaction)
    row = await bot.store.one("SELECT * FROM tickets")
    assert row["status"] == "open" and row["claimed_by"] == 1


@pytest.mark.asyncio
async def test_ticket_other_member_cannot_close(tmp_path):
    bot, w, cog = await ticket_service(tmp_path)
    await cog.open_ticket(
        w.guild, w.staff, PANEL, PANEL["options"][0], "Subject", "Details"
    )
    interaction = NS(
        guild=w.guild, guild_id=100, channel=w.channel, channel_id=300, user=w.target
    )
    with pytest.raises(ValueError, match="permission"):
        await cog.close_ticket(interaction)
    assert (await bot.store.one("SELECT status FROM tickets"))["status"] == "open"


@pytest.mark.asyncio
async def test_staff_ticket_access_revocation_removes_channel_override(tmp_path):
    bot, w, cog = await ticket_service(tmp_path)
    await cog.open_ticket(
        w.guild, w.target, PANEL, PANEL["options"][0], "Subject", "Details"
    )
    await cog.sync_staff_access(w.guild, w.staff, {"ticket_access"})
    assert w.channel.set_permissions.call_args.kwargs["overwrite"].view_channel is True
    await cog.sync_staff_access(w.guild, w.staff, set())
    assert w.channel.set_permissions.call_args.kwargs["overwrite"] is None


@pytest.mark.asyncio
async def test_lock_unlock_restores_tristate_and_other_permissions(tmp_path):
    bot = await service(tmp_path)
    w = world()
    cog = Moderation(bot)
    ctx = NS(guild=w.guild, channel=w.channel, author=w.owner, send=AsyncMock())
    await cog.lock.callback(cog, ctx, w.channel)
    original = w.channel.set_permissions.call_args.kwargs["overwrite"]
    assert original.send_messages is False and original.attach_files is True
    with pytest.raises(commands.BadArgument):
        await cog.lock.callback(cog, ctx, w.channel)
    await cog.unlock.callback(cog, ctx, w.channel)
    restored = w.channel.set_permissions.call_args.kwargs["overwrite"]
    assert (
        restored.send_messages is None
        and restored.send_messages_in_threads is True
        and restored.attach_files is True
    )
    assert await bot.store.one("SELECT * FROM channel_locks") is None


@pytest.mark.asyncio
async def test_published_panels_are_registered_after_restart(tmp_path):
    path = tmp_path / "db"
    store = Store(path)
    await store.init()
    await store.execute(
        "INSERT INTO ticket_panels VALUES(?,?,?,?)", (500, 100, 300, json.dumps(PANEL))
    )
    async with FortuneManager(
        database=path, start_dashboard=False, load_legacy=False
    ) as bot:
        await bot.setup_hook()
        assert len(bot.persistent_views) == 4
        assert all(view.is_persistent() for view in bot.persistent_views)


@pytest.mark.asyncio
async def test_reaction_roles_add_remove_and_ignore_unconfigured_channel(tmp_path):
    from fortune.community import Community

    bot = await service(tmp_path)
    w = world()
    bot.get_guild = lambda gid: w.guild if gid == 100 else None
    config, v = await bot.store.config(100)
    config["reaction_roles"] = [
        {"channel_id": "300", "message_id": "500", "role_id": "40", "emoji": "🌿"}
    ]
    await bot.store.save_config(100, config, v)
    cog = Community(bot)
    payload = NS(
        guild_id=100,
        user_id=3,
        channel_id=300,
        message_id=500,
        emoji=discord.PartialEmoji.from_str("🌿"),
        member=w.target,
    )
    await cog.reaction(payload, True)
    w.target.add_roles.assert_awaited_once()
    await cog.reaction(payload, False)
    w.target.remove_roles.assert_awaited_once()
    payload.channel_id = 301
    await cog.reaction(payload, True)
    assert w.target.add_roles.await_count == 1


@pytest.mark.asyncio
async def test_kick_command_enforces_saved_permission_check(tmp_path):
    bot = await service(tmp_path)
    w = world()
    cog = Moderation(bot)
    ctx = NS(bot=bot, guild=w.guild, author=w.staff, bot_permissions=w.guild.me.guild_permissions)
    with pytest.raises(commands.CheckFailure):
        await discord.utils.async_all(check(ctx) for check in cog.kick.checks)
    await bot.store.save_staff(100, 2, {"kick"}, 40, 1)
    assert await discord.utils.async_all(check(ctx) for check in cog.kick.checks)
