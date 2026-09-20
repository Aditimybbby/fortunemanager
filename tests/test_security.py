"""Incident simulations: real command parsing and persistence, mocked Discord I/O."""

import asyncio
import copy
import json
import time
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import discord
import pytest
from discord.ext import commands
from fortune.bot import FortuneManager
from fortune.security_policy import (
    Evidence,
    Detector,
    default_policy,
    normalize,
    validate_policy,
)
from fortune.security_data import SecurityData
from fortune.automod import (
    default_config,
    content_matches,
    validate_config,
    host,
    matches_host,
)
from fortune.store import Store
from test_core import world, Role

Role.__lt__ = lambda a, b: a.position < b.position
Role.__gt__ = lambda a, b: a.position > b.position


@pytest.fixture
async def security(tmp_path):
    async with FortuneManager(
        database=tmp_path / "test.db", start_dashboard=False, load_legacy=False
    ) as bot:
        await bot.setup_hook()
        w = world()
        w.owner.guild_permissions = discord.Permissions.all()
        bot._connection.user = NS(id=9)
        bot.get_guild = lambda gid: w.guild if gid == 100 else None
        bot.get_channel = lambda cid: w.guild.get_channel(cid)
        for r in w.guild.roles + [w.staff.top_role, w.target.top_role]:
            r.colour = discord.Colour(0)
            r.hoist = False
            r.mentionable = False
            r.edit = AsyncMock()
        w.guild.verification_level = discord.VerificationLevel.low
        w.guild.default_notifications = discord.NotificationLevel.only_mentions
        w.guild.explicit_content_filter = discord.ContentFilter.disabled
        w.category.guild = w.guild
        w.category.type = discord.ChannelType.category
        w.category.position = 0
        w.category.category_id = None
        w.category.overwrites = {}
        w.channel.type = discord.ChannelType.text
        w.channel.position = 1
        w.channel.category_id = 200
        w.channel.overwrites = {}
        w.channel.slowmode_delay = 0
        w.channel.nsfw = False
        w.channel.default_auto_archive_duration = 1440
        w.channel.default_thread_slowmode_delay = 0
        w.category.permissions_for.side_effect = lambda m: m.guild_permissions
        w.channel.permissions_for.side_effect = lambda m: m.guild_permissions
        w.guild.ban = AsyncMock()
        w.guild.unban = AsyncMock()
        w.guild.kick = AsyncMock()
        engine = bot.get_cog("Antinuke").engine
        p, v = await engine.data.policy(100)
        p.update(enabled=True, punishment="ban")
        await engine.data.save(100, p, v, 1)
        yield bot, w, engine


def entry(
    w,
    number=1001,
    action="channel_delete",
    actor=None,
    target=None,
    ago=0,
    before=None,
    after=None,
):
    return NS(
        id=number,
        guild=w.guild,
        user_id=(actor or w.staff).id,
        user=actor or w.staff,
        target=target or w.channel,
        action=getattr(discord.AuditLogAction, action),
        created_at=discord.utils.utcnow() - timedelta(seconds=ago),
        before=before or NS(),
        after=after or NS(),
        reason=None,
    )


def evidence(n, rule="ban", actor=2, ago=0):
    return Evidence(n, 100, actor, 300, rule, time.time() - ago, {})


async def parse(bot, w, text, author=None):
    m = MagicMock(spec=discord.Message)
    m.content = text
    m.author = author or w.owner
    m.guild = w.guild
    m.channel = w.channel
    m._state = bot._connection
    m.created_at = discord.utils.utcnow()
    m.edited_at = None
    m.attachments = []
    ctx = await bot.get_context(m)
    ctx.send = AsyncMock()
    ctx.reply = ctx.send
    return ctx


@pytest.mark.parametrize(
    "text",
    [
        ".antinuke rules",
        ".antinuke config",
        ".antinuke health",
        ".antinuke limit ban 4 20 50",
        ".antinuke mode observe",
        ".antinuke punishment strip",
        ".antinuke scores 30 80",
        ".antinuke autolockdown off",
        ".antinuke rule webhook_create off",
        ".antinuke incidents",
        ".antinuke jobs",
        ".antinuke backup",
        ".automod config",
        ".automod enable",
        ".automod mode observe",
        ".automod limit mention_count 8",
        ".automod rule links on delete",
        ".automod punishment spam timeout",
        ".automod words add blocked phrase",
        ".automod domain blocked add bad.example",
        ".automod raid on 12 10 24",
        ".automod test https://discord.gg/test",
        ".automod ignore show",
        ".automod ignore reset",
        ".extraowner",
    ],
)
async def test_command_parser_and_outputs(security, text):
    bot, w, e = security
    ctx = await parse(bot, w, text)
    await ctx.command.invoke(ctx)
    assert ctx.send.await_count
    for call in ctx.send.call_args_list:
        page = call.kwargs.get("embed")
        if page:
            assert len(page) <= 6000
            assert "image" not in page.to_dict() and "thumbnail" not in page.to_dict()


async def test_owner_control_applies_to_subcommands(security):
    bot, w, e = security
    w.staff.guild_permissions.administrator = True
    for text in (
        ".antinuke disable",
        ".antinuke mode observe",
        ".whitelistreset confirm",
        ".emergency on",
    ):
        ctx = await parse(bot, w, text, w.staff)
        with pytest.raises(commands.CheckFailure):
            await ctx.command.invoke(ctx)
    assert (await e.data.policy(100))[0]["enabled"]


async def test_invalid_limits_do_not_save(security):
    bot, w, e = security
    p, v = await e.data.policy(100)
    ctx = await parse(bot, w, ".antinuke limit ban 0 9999 1")
    with pytest.raises(commands.CommandInvokeError):
        await ctx.command.invoke(ctx)
    assert await e.data.policy(100) == (p, v)


async def test_burst_keeps_every_entry_and_deduplicates(security):
    bot, w, e = security
    for i in range(20):
        assert await e.ingest(entry(w, number=1000 + i))
    assert not await e.ingest(entry(w, number=1005))
    await e.process_pending()
    assert len(await bot.store.rows("SELECT * FROM security_events")) == 20
    incidents = await bot.store.rows("SELECT * FROM security_incidents")
    assert len(incidents) == 1
    job = await e.data.take_job(True)
    await e.execute_job(job)
    w.guild.ban.assert_awaited_once()
    assert (await bot.store.one("SELECT status FROM security_incidents"))[
        "status"
    ] == "contained"
    assert (await e.data.take_job(True)) is None


async def test_simultaneous_actors_matched_exactly(security):
    bot, w, e = security
    await asyncio.gather(
        e.ingest(entry(w, actor=w.staff)),
        e.ingest(entry(w, number=1002, actor=w.target)),
    )
    await e.process_pending()
    assert {
        r["actor_id"] for r in await bot.store.rows("SELECT * FROM security_incidents")
    } == {2, 3}
    for _ in range(2):
        await e.execute_job(await e.data.take_job(True))
    assert {call.args[0].id for call in w.guild.ban.call_args_list} == {2, 3}


async def test_owner_self_and_unknown_actor_not_punished(security):
    bot, w, e = security
    assert not await e.ingest(entry(w, actor=w.owner))
    assert not await e.ingest(entry(w, actor=w.guild.me))
    missing = entry(w)
    missing.user_id = None
    missing.user = None
    assert not await e.ingest(missing)
    assert not await bot.store.rows("SELECT * FROM security_incidents")


async def test_delayed_audit_still_works_but_old_outage_is_observed(security):
    bot, w, e = security
    await e.ingest(entry(w, ago=15))
    await e.ingest(entry(w, number=1002, actor=w.target, ago=200))
    await e.process_pending()
    rows = await bot.store.rows("SELECT * FROM security_incidents ORDER BY actor_id")
    assert [r["status"] for r in rows] == ["queued", "observed"]
    assert (
        len(await bot.store.rows("SELECT * FROM security_jobs WHERE kind='contain'"))
        == 1
    )


async def test_observe_never_punishes(security):
    bot, w, e = security
    p, v = await e.data.policy(100)
    p["mode"] = "observe"
    await e.data.save(100, p, v, 1)
    await e.ingest(entry(w))
    await e.process_pending()
    assert (await bot.store.one("SELECT status FROM security_incidents"))[
        "status"
    ] == "observed"
    assert await e.data.take_job(True) is None


async def test_hierarchy_failure_is_visible(security):
    bot, w, e = security
    w.staff.top_role.position = 100
    await e.ingest(entry(w))
    await e.process_pending()
    await e.execute_job(await e.data.take_job(True))
    incident = await bot.store.one("SELECT * FROM security_incidents")
    assert incident["status"] == "failed" and "highest role" in incident["result"]
    w.guild.ban.assert_not_awaited()


async def test_strip_then_check_remaining_permissions(security):
    bot, w, e = security
    p, v = await e.data.policy(100)
    p["punishment"] = "strip"
    await e.data.save(100, p, v, 1)
    w.staff.roles = [w.staff.top_role]
    w.staff.guild_permissions = discord.Permissions.none()
    await e.ingest(entry(w))
    await e.process_pending()
    await e.execute_job(await e.data.take_job(True))
    w.staff.edit.assert_awaited_once()
    w.guild.ban.assert_not_awaited()
    assert await bot.store.one("SELECT * FROM security_contained WHERE user_id=2")


async def test_remaining_admin_after_strip_falls_back_to_ban(security):
    bot, w, e = security
    p, v = await e.data.policy(100)
    p["punishment"] = "strip"
    await e.data.save(100, p, v, 1)
    w.staff.guild_permissions = discord.Permissions(administrator=True)
    await e.ingest(entry(w))
    await e.process_pending()
    await e.execute_job(await e.data.take_job(True))
    w.staff.edit.assert_awaited_once()
    w.guild.ban.assert_awaited_once()


async def test_disabling_cancels_queued_enforcement(security):
    bot, w, e = security
    await e.ingest(entry(w))
    await e.process_pending()
    p, v = await e.data.policy(100)
    p["enabled"] = False
    await e.data.save(100, p, v, 1)
    await e.execute_job(await e.data.take_job(True))
    w.guild.ban.assert_not_awaited()
    assert (await bot.store.one("SELECT status FROM security_incidents"))[
        "status"
    ] == "cancelled"


async def test_jobs_resume_and_seen_entries_survive_restart(security):
    bot, w, e = security
    await e.ingest(entry(w))
    await e.process_pending()
    job = await e.data.take_job(True)
    fresh = SecurityData(bot.store)
    await fresh.init()
    assert (await fresh.take_job(True))["id"] == job["id"]
    assert not await fresh.record(
        e.evidence((await bot.store.rows("SELECT * FROM security_events"))[0])
    )


async def test_rate_limit_retries_without_blinding_detector(security):
    bot, w, e = security
    await e.ingest(entry(w))
    await e.process_pending()
    w.guild.ban.side_effect = discord.HTTPException(
        NS(status=429, reason="Too many requests", headers={"Retry-After": "3"}),
        "rate limited",
    )
    job = await e.data.take_job(True)
    await e.execute_job(job)
    row = await bot.store.one("SELECT * FROM security_jobs WHERE id=?", (job["id"],))
    assert row["status"] == "pending" and row["available"] > time.time()
    assert await e.ingest(entry(w, number=1002, actor=w.target))
    await e.process_pending()
    assert len(await bot.store.rows("SELECT * FROM security_incidents")) == 2


async def test_permanent_error_does_not_retry_forever(security):
    bot, w, e = security
    await e.ingest(entry(w))
    await e.process_pending()
    w.guild.ban.side_effect = discord.Forbidden(
        NS(status=403, reason="Forbidden"), "missing permissions"
    )
    job = await e.data.take_job(True)
    await e.execute_job(job)
    assert (
        await bot.store.one("SELECT status FROM security_jobs WHERE id=?", (job["id"],))
    )["status"] == "failed"


async def test_snapshot_freezes_during_incident(security):
    bot, w, e = security
    snapshot = await e.recovery.snapshot(w.guild)
    await e.ingest(entry(w))
    await e.process_pending()
    with pytest.raises(ValueError, match="Resolve"):
        await e.recovery.snapshot(w.guild)
    assert (await e.recovery.get(100))["id"] == snapshot


async def test_recovery_is_queued_after_containment(security):
    bot, w, e = security
    await e.recovery.snapshot(w.guild)
    await e.ingest(entry(w))
    await e.process_pending()
    assert not await bot.store.rows("SELECT * FROM security_jobs WHERE kind='restore'")
    await e.execute_job(await e.data.take_job(True))
    w.guild.ban.assert_awaited_once()
    assert await bot.store.rows("SELECT * FROM security_jobs WHERE kind='restore'")


async def test_recovery_retries_do_not_create_duplicate_channels(security):
    bot, w, e = security
    sid = await e.recovery.snapshot(w.guild)
    snap = await e.recovery.get(100, sid)
    payload = dict(snapshot_id=sid, kind="channel", old_id=300, mode="missing")
    channels = {200: w.category}
    w.guild.get_channel.side_effect = channels.get

    async def audit(**kwargs):
        if False:
            yield None

    w.guild.audit_logs = audit
    new = MagicMock(spec=discord.TextChannel)
    new.id = 333

    async def create(**kwargs):
        channels[333] = new
        return new

    w.guild.create_text_channel = AsyncMock(side_effect=create)
    await e.recovery.restore(w.guild, payload)
    await e.recovery.restore(w.guild, payload)
    w.guild.create_text_channel.assert_awaited_once()
    assert (await e.recovery.mappings(sid))[300] == 333


async def test_missing_deny_role_prevents_unsafe_channel_creation(security):
    bot, w, e = security
    sid = await e.recovery.snapshot(w.guild)
    snap = await e.recovery.get(100, sid)
    snap["data"]["channels"][1]["overwrites"] = [
        dict(id=9999, role=True, allow=0, deny=1024)
    ]
    await bot.store.execute(
        "UPDATE security_snapshots SET data=? WHERE id=?",
        (json.dumps(snap["data"]), sid),
    )
    w.guild.get_channel.side_effect = lambda x: w.category if x == 200 else None

    async def audit(**kwargs):
        if False:
            yield None

    w.guild.audit_logs = audit
    with pytest.raises(ValueError, match="Restore role"):
        await e.recovery.restore(
            w.guild, dict(snapshot_id=sid, kind="channel", old_id=300, mode="missing")
        )
    w.guild.create_text_channel.assert_not_awaited()


async def test_confirmation_code_is_single_use_and_versioned(security):
    bot, w, e = security
    p, v = await e.data.policy(100)
    code = await e.data.issue_code(100, 1)
    await e.data.save(100, p, v, 1, confirmation=code)
    with pytest.raises(ValueError, match="confirmation code"):
        await e.data.save(100, p, v + 1, 1, confirmation=code)
    with pytest.raises(ValueError, match="changed"):
        await e.data.save(100, p, v, 1)


@pytest.mark.parametrize(
    "rule",
    ["ban", "channel_delete", "role_delete", "permission_grant", "webhook_create"],
)
def test_detection_limits(rule):
    p = default_policy()
    p["rules"][rule]["count"] = 3
    p["actor_score"] = 500
    p["guild_score"] = 500
    d = Detector()
    assert d.decide(evidence(1, rule), p) is None
    assert d.decide(evidence(2, rule), p) is None
    assert d.decide(evidence(3, rule), p)


def test_hourly_limit_catches_slow_actions():
    p = default_policy()
    p["rules"]["ban"].update(count=5, seconds=5, hourly=3)
    p["actor_score"] = 500
    d = Detector()
    assert d.decide(evidence(1, ago=120), p) is None
    assert d.decide(evidence(2, ago=60), p) is None
    assert "hourly" in d.decide(evidence(3), p)


def test_scoped_trust_expires_and_has_budget():
    p = default_policy()
    p["rules"]["ban"]["count"] = 1
    p["trust"] = {"2": {"ban": dict(budget=2, expires=time.time() + 100)}}
    d = Detector()
    assert d.decide(evidence(1), p) is None
    assert d.decide(evidence(2), p) is None
    assert "allowance" in d.decide(evidence(3), p)
    assert Detector().decide(evidence(4, "channel_delete"), p)
    p["trust"]["2"]["ban"]["expires"] = time.time() - 1
    assert Detector().decide(evidence(5), p)


def test_coordinated_activity_combines_actors():
    p = default_policy()
    p["rules"]["ban"]["count"] = 50
    p["actor_score"] = 500
    p["guild_score"] = 8
    d = Detector()
    assert d.decide(evidence(1, actor=2), p) is None
    assert "Coordinated" in d.decide(evidence(2, actor=3), p)


def test_permission_grants_are_distinguished_from_cosmetic_updates():
    w = world()
    e = entry(
        w,
        action="role_update",
        before=NS(name="old", permissions=discord.Permissions.none()),
        after=NS(name="new", permissions=discord.Permissions.none()),
    )
    assert normalize(e).rule == "role_update"
    e.after.permissions = discord.Permissions(administrator=True)
    assert normalize(e).rule == "permission_grant"
    assert normalize(e).detail["after_permissions"] == 8


def message(w, ident, content="hello", author=None):
    m = MagicMock(spec=discord.Message)
    m.guild = w.guild
    m.author = author or w.target
    m.channel = w.channel
    m.id = ident
    m.content = content
    m.mentions = []
    m.role_mentions = []
    m.mention_everyone = False
    m.attachments = []
    m.webhook_id = None
    m.delete = AsyncMock()
    return m


@pytest.mark.parametrize(
    "text,rule",
    [
        ("discord.gg/example", "invites"),
        ("https://discord.com/invite/abc", "invites"),
        ("dis\u200bcord.gg/example", "invites"),
        ("https://unsafe.example/test", "links"),
        ("LOUD WORDS IN UPPERCASE", "caps"),
        ("😀" * 12, "emoji"),
        ("this blocked phrase appears", "words"),
        ("https://sub.bad.example/path", "domains"),
        ("https://trusted.example@bad.example/", "domains"),
    ],
)
def test_content_detection(text, rule):
    p = default_config()
    p["words"] = ["blocked phrase"]
    p["blocked_domains"] = ["bad.example"]
    for r in p["rules"].values():
        r["enabled"] = True
    assert rule in content_matches(text, 0, False, [], p)


@pytest.mark.parametrize(
    "text",
    [
        "https://bad.example.evil.test/",
        "https://notbad.example/",
        "https://good.example/?next=bad.example",
        "a blocked phrasing here",
        "normal Mixed case conversation",
    ],
)
def test_domain_and_word_filters_avoid_substring_false_positives(text):
    p = default_config()
    p["words"] = ["blocked phrase"]
    p["blocked_domains"] = ["bad.example"]
    result = content_matches(text, 0, False, [], p)
    assert "words" not in result and "domains" not in result


def test_allowlist_is_exact_domain_or_subdomain():
    p = default_config()
    p["rules"]["links"]["enabled"] = True
    p["allowed_domains"] = ["good.example"]
    assert "links" not in content_matches(
        "https://sub.good.example/path", 0, False, [], p
    )
    assert "links" in content_matches(
        "https://good.example.evil.test/", 0, False, [], p
    )


async def test_message_spam_one_punishment_and_every_violation_deleted(security):
    bot, w, e = security
    c = bot.get_cog("Automod")
    p, v = await c.config_for(100)
    p["enabled"] = True
    p["rules"]["duplicates"]["enabled"] = False
    await c.save(100, p, v, 1)
    messages = [message(w, i, "message " + str(i)) for i in range(10)]
    results = await asyncio.gather(*(c.handle(m) for m in messages))
    assert results == [False] * 5 + [True] * 5
    assert all(m.delete.await_count == 1 for m in messages[5:])
    w.target.timeout.assert_awaited_once()


async def test_edited_message_does_not_bypass_filter_or_increment_spam(security):
    bot, w, e = security
    c = bot.get_cog("Automod")
    p, v = await c.config_for(100)
    p["enabled"] = True
    await c.save(100, p, v, 1)
    m = message(w, 77)
    assert not await c.handle(m)
    m.content = "discord.gg/spam"
    assert await c.handle(m, edited=True)
    assert len(c.messages[(100, 3)]) == 1
    m.delete.assert_awaited_once()


async def test_automod_observe_and_exemptions(security):
    bot, w, e = security
    c = bot.get_cog("Automod")
    p, v = await c.config_for(100)
    p.update(enabled=True, mode="observe")
    await c.save(100, p, v, 1)
    m = message(w, 1, "discord.gg/test")
    assert not await c.handle(m)
    m.delete.assert_not_awaited()
    p, v = await c.config_for(100)
    p.update(mode="enforce", ignored_roles=[w.target.top_role.id])
    await c.save(100, p, v, 1)
    assert not await c.handle(message(w, 2, "discord.gg/test"))
    assert not await c.handle(message(w, 3, "discord.gg/test", w.owner))


async def test_automod_hierarchy_failure_still_deletes_message(security):
    bot, w, e = security
    c = bot.get_cog("Automod")
    p, v = await c.config_for(100)
    p["enabled"] = True
    p["rules"]["invites"]["action"] = "timeout"
    await c.save(100, p, v, 1)
    w.target.top_role.position = 100
    m = message(w, 77, "discord.gg/test")
    assert await c.handle(m)
    m.delete.assert_awaited_once()
    w.target.timeout.assert_not_awaited()
    row = await bot.store.one("SELECT detail FROM audit WHERE action='automod.action'")
    assert "above the bot" in row["detail"]


async def test_native_rules_update_in_place_and_remove_only_ours(security):
    bot, w, e = security
    c = bot.get_cog("Automod")
    ours = NS(
        name="FortuneManager | Mentions",
        creator_id=9,
        edit=AsyncMock(),
        delete=AsyncMock(),
    )
    other = NS(name="Someone else", creator_id=22, edit=AsyncMock(), delete=AsyncMock())
    w.guild.fetch_automod_rules = AsyncMock(return_value=[ours, other])
    w.guild.create_automod_rule = AsyncMock()
    ctx = await parse(bot, w, ".automod native on")
    await ctx.command.invoke(ctx)
    ours.edit.assert_awaited_once()
    w.guild.create_automod_rule.assert_awaited_once()
    ctx = await parse(bot, w, ".automod native off")
    await ctx.command.invoke(ctx)
    ours.delete.assert_awaited_once()
    other.delete.assert_not_awaited()


async def test_tags_persist_and_cannot_cross_servers(security):
    bot, w, e = security
    ctx = await parse(bot, w, ".tag create rules Read the rules")
    await ctx.command.invoke(ctx)
    ctx = await parse(bot, w, ".tag rules", w.target)
    await ctx.command.invoke(ctx)
    assert ctx.send.call_args.args[0] == "Read the rules"
    assert not ctx.send.call_args.kwargs["allowed_mentions"].everyone
    await bot.store.execute("INSERT INTO tags VALUES(101,'private','Other server',1)")
    ctx = await parse(bot, w, ".tag private", w.target)
    with pytest.raises(commands.CommandInvokeError):
        await ctx.command.invoke(ctx)
    ctx = await parse(bot, w, ".tag create rules Changed", w.target)
    with pytest.raises(commands.MissingPermissions):
        await ctx.command.invoke(ctx)


async def test_reminder_delivery_and_ownership(security):
    bot, w, e = security
    c = bot.get_cog("ServerTools")
    w.channel.permissions_for.return_value = discord.Permissions.all()
    w.channel.permissions_for.side_effect = None
    ctx = await parse(bot, w, ".remindme 30m Check the event", w.target)
    await ctx.command.invoke(ctx)
    row = await bot.store.one("SELECT * FROM reminders")
    assert 1700 < row["due"] - time.time() < 1900
    ctx = await parse(bot, w, f'.reminders cancel {row["id"]}', w.staff)
    with pytest.raises(commands.CommandInvokeError):
        await ctx.command.invoke(ctx)
    await bot.store.execute("UPDATE reminders SET due=0")
    await c.deliver_reminders()
    await c.deliver_reminders()
    w.channel.send.assert_awaited_once()
    assert (await bot.store.one("SELECT status FROM reminders"))["status"] == "sent"


async def test_verification_refuses_roles_that_gained_dangerous_permissions(security):
    bot, w, e = security
    c = bot.get_cog("ServerTools")
    await bot.store.execute("INSERT INTO verification VALUES(100,40,0,1)")
    w.role.permissions.administrator = True
    interaction = NS(
        guild=w.guild,
        user=w.target,
        response=NS(defer=AsyncMock()),
        followup=NS(send=AsyncMock()),
    )
    await c.view.verify.callback(interaction)
    assert "without moderation" in interaction.followup.send.call_args.args[0]
    w.target.add_roles.assert_not_awaited()


async def test_verification_age_gate_and_idempotent_role_grant(security):
    bot, w, e = security
    c = bot.get_cog("ServerTools")
    await bot.store.execute("INSERT INTO verification VALUES(100,40,24,1)")
    w.target.created_at = discord.utils.utcnow() - timedelta(hours=2)
    interaction = NS(
        guild=w.guild,
        user=w.target,
        response=NS(defer=AsyncMock()),
        followup=NS(send=AsyncMock()),
    )
    await c.view.verify.callback(interaction)
    w.target.add_roles.assert_not_awaited()
    w.target.created_at = discord.utils.utcnow() - timedelta(days=2)
    await c.view.verify.callback(interaction)
    w.target.add_roles.assert_awaited_once()
    w.target.roles.append(w.role)
    await c.view.verify.callback(interaction)
    assert w.target.add_roles.await_count == 1


async def test_self_assignable_roles_reject_channel_management_overwrites(security):
    from fortune.permissions import check_role

    bot, w, e = security
    w.channel.overwrites_for.return_value = discord.PermissionOverwrite(
        manage_channels=True
    )
    with pytest.raises(ValueError, match="in a channel"):
        check_role(w.role, w.owner, safe=True)


def test_reprocessing_evidence_does_not_inflate_counts():
    d = Detector()
    p = default_policy()
    p["actor_score"] = 500
    p["rules"]["ban"]["count"] = 3
    event = evidence(1)
    assert d.decide(event, p) is None
    assert d.decide(event, p) is None
    assert d.decide(evidence(2), p) is None
    assert d.decide(evidence(3), p)


async def test_automatic_repairs_stop_after_switch_to_observe(security):
    bot, w, e = security
    p, v = await e.data.policy(100)
    p["mode"] = "observe"
    await e.data.save(100, p, v, 1)
    await e.data.job(100, "unban", dict(target_id=3, automatic=True), priority=20)
    await e.execute_job(await e.data.take_job(False))
    w.guild.unban.assert_not_awaited()
