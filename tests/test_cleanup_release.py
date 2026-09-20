"""Regression tests for command routing, strict moderation, and reply cleanup.

Discord I/O is mocked; command parsing, policy persistence, and decision paths
are real. No live server or real member is changed by these tests.
"""
import asyncio
import copy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
import pytest
from discord.ext import commands

from fortune.automod import default_config, validate_config, repeated_text, contains_word
from fortune.configuration import validate_config as validate_server_config, validate_prefix
from fortune.bot import FortuneManager
from fortune.store import Store, DEFAULT_CONFIG
from test_core import Role
from test_security import security, message, parse


async def enable_words(bot, w, words=None):
    cog = bot.get_cog('Automod')
    p, v = await cog.config_for(w.guild.id)
    p.update(enabled=True, ban_words=words or ['forbidden'])
    await cog.save(w.guild.id, p, v, w.owner.id)
    return cog


def routed_message(bot, w, text, ident=800, author=None):
    m = message(w, ident, text, author)
    m._state = bot._connection
    m.created_at = discord.utils.utcnow()
    m.edited_at = None
    return m


@pytest.mark.parametrize('text', [
    'hello .ping', 'text . help', '.', '.ordinary', 'example.com',
    '.com', 'hello', 'ping', '<@9>', '<@9> ping', ' .ping', '.pingpong',
])
async def test_non_commands_never_invoke_or_send(security, text):
    bot, w, _ = security
    bot.invoke = AsyncMock()
    await bot.on_message(routed_message(bot, w, text))
    bot.invoke.assert_not_awaited()
    w.channel.send.assert_not_awaited()


@pytest.mark.parametrize('text', ['.ping', '.PING', '.help', '.help ban', '.automod config'])
async def test_registered_commands_route_once(security, text):
    bot, w, _ = security
    bot.invoke = AsyncMock()
    await bot.on_message(routed_message(bot, w, text))
    bot.invoke.assert_awaited_once()
    assert bot.invoke.call_args.args[0].valid


async def test_bot_and_webhook_messages_do_not_invoke(security):
    bot, w, _ = security
    bot.invoke = AsyncMock()
    m = routed_message(bot, w, '.ping')
    m.webhook_id = 123
    await bot.on_message(m)
    m.webhook_id = None
    m.author.bot = True
    await bot.on_message(m)
    bot.invoke.assert_not_awaited()


async def test_prefix_replacement_via_real_command_and_reload(security):
    bot, w, _ = security
    for previous, next_prefix in [('.', '!'), ('!', '??')]:
        ctx = await parse(bot, w, f'{previous}prefix {next_prefix}')
        await ctx.command.invoke(ctx)
        assert (await bot.get_context(routed_message(bot, w, next_prefix + 'ping'))).valid
        assert not (await bot.get_context(routed_message(bot, w, previous + 'ping'))).valid
        assert not (await bot.get_context(routed_message(bot, w, '.ping'))).valid
    restarted_store = Store(bot.store.path)
    assert (await restarted_store.config(w.guild.id))[0]['prefix'] == '??'
    assert await bot.resolve_prefix(bot, NS(guild=NS(id=999))) == '.'
    assert await bot.resolve_prefix(bot, NS(guild=None)) == '.'


@pytest.mark.parametrize('prefix', ['', '  ', ' !', '! ', 'a b', '\n', '\u200b', '123456789'])
def test_invalid_prefixes(prefix):
    with pytest.raises(ValueError):
        validate_prefix(prefix)


async def test_hard_preset_and_restart(security):
    bot, w, _ = security
    ctx = await parse(bot, w, '.automod hard')
    await ctx.command.invoke(ctx)
    cog = bot.get_cog('Automod')
    p, _ = await cog.config_for(100)
    assert p['enabled'] and p['mode'] == 'enforce'
    assert p['spam_count'] == 4 and p['timeout_seconds'] == 3600
    cog.cache.clear()
    assert (await cog.config_for(100))[0] == p
    spam = message(w, 40, 'hello there ' * 4)
    assert await cog.handle(spam)
    spam.delete.assert_awaited_once()
    w.target.timeout.assert_awaited_once_with(timedelta(hours=1), reason='Automod: repetition')


@pytest.mark.parametrize('text', [
    'hi hi hi hi', 'go now\ngo now\ngo now\ngo now',
    'Hi! HI! hi! Hi!', 'a' * 12, 'spam\u200b spam spam spam',
    'prefix hello friend hello friend hello friend hello friend suffix',
])
def test_repeated_text_inside_one_message(text):
    assert repeated_text(text)


@pytest.mark.parametrize('text', [
    'hello everyone how are you', 'ha ha ha', 'hi hi hello hi',
    'The next meeting is tomorrow. Please read the agenda.',
    '------------', '...', 'https://example.com/path',
])
def test_normal_text_not_repeated_spam(text):
    assert not repeated_text(text)


@pytest.mark.parametrize('text', ['FORBIDDEN', 'hey forbidden!', 'ｆｏｒｂｉｄｄｅｎ', 'for\u200bbidden', '**forbidden**', '_forbidden_'])
async def test_banwords_delete_and_ban_normalized_matches(security, text):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    m = message(w, 1, text)
    assert await cog.handle(m)
    m.delete.assert_awaited_once()
    w.target.ban.assert_awaited_once()


@pytest.mark.parametrize('text', ['unforbidden', 'forbiddenness', 'perfectly ordinary text'])
async def test_banwords_do_not_match_unrelated_substrings(security, text):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    m = message(w, 1, text)
    assert not await cog.handle(m)
    m.delete.assert_not_awaited()
    w.target.ban.assert_not_awaited()


def test_banned_phrases_collapse_whitespace_and_escape_regex():
    assert contains_word('bad\n\tphrase', ['bad phrase'])
    assert contains_word('use c++ here', ['c++'])
    assert not contains_word('use c here', ['c++'])


async def test_banwords_add_list_remove_and_permission_checks(security):
    bot, w, _ = security
    for text in ('.banwords add BAD Phrase', '.banwords add bad phrase', '.banwords list'):
        ctx = await parse(bot, w, text)
        await ctx.command.invoke(ctx)
        assert ctx.send.await_count
    cog = bot.get_cog('Automod')
    p, _ = await cog.config_for(100)
    assert p['ban_words'] == ['bad phrase']
    assert p['enabled'] and p['mode'] == 'enforce'
    ctx = await parse(bot, w, '.banwords remove bad phrase')
    await ctx.command.invoke(ctx)
    assert (await cog.config_for(100))[0]['ban_words'] == []
    # Ordinary members cannot edit lists; Manage Server alone cannot create bans.
    for permission in (discord.Permissions.none(), discord.Permissions(manage_guild=True)):
        w.staff.guild_permissions = permission
        ctx = await parse(bot, w, '.banwords add forbidden', w.staff)
        with pytest.raises(commands.CheckFailure):
            await ctx.command.invoke(ctx)
    w.guild.me.guild_permissions.ban_members = False
    ctx = await parse(bot, w, '.banwords add forbidden')
    with pytest.raises(commands.BotMissingPermissions):
        await ctx.command.invoke(ctx)
    assert not (await cog.config_for(100))[0]['ban_words']


async def test_banwords_moderator_can_remove_existing_word_without_self_ban(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    w.staff.guild_permissions = discord.Permissions(manage_guild=True, ban_members=True)
    m = routed_message(bot, w, '.banwords remove forbidden', author=w.staff)
    assert not await cog.handle(m)
    ctx = await bot.get_context(m)
    ctx.send = AsyncMock()
    await ctx.command.invoke(ctx)
    w.staff.ban.assert_not_awaited()
    assert not (await cog.config_for(100))[0]['ban_words']


async def test_banwords_override_ordinary_exemptions(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    p, v = await cog.config_for(100)
    p.update(ignored_roles=[w.target.top_role.id], ignored_channels=[w.channel.id])
    await cog.save(100, p, v, 1)
    w.target.guild_permissions.administrator = True
    m = routed_message(bot, w, 'forbidden')
    assert await cog.handle(m)
    w.target.ban.assert_awaited_once()
    # Owners are never punishable, even below the bot role.
    assert not await cog.handle(message(w, 22, 'forbidden', w.owner))
    w.owner.ban.assert_not_awaited()


async def test_ban_is_not_suppressed_by_prior_timeout(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    assert await cog.handle(message(w, 1, 'spam spam spam spam'))
    w.target.timeout.assert_awaited_once()
    assert await cog.handle(message(w, 2, 'forbidden'))
    w.target.ban.assert_awaited_once()


async def test_duplicate_burst_deleted_without_other_users(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    messages = [message(w, i, text) for i, text in enumerate(['hello', 'HELLO ', 'he\u200bllo'])]
    other = message(w, 9, 'hello', w.staff)
    await cog.handle(other)
    assert [await cog.handle(m) for m in messages] == [False, False, True]
    assert all(m.delete.await_count == 1 for m in messages)
    other.delete.assert_not_awaited()
    w.target.timeout.assert_awaited_once()


async def test_repeated_edits_and_concurrent_delivery_apply_once(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    m = message(w, 20, 'normal')
    assert not await cog.handle(m)
    m.content = 'forbidden'
    assert await asyncio.gather(cog.handle(m, edited=True), cog.handle(m, edited=True)) == [True, True]
    m.delete.assert_awaited_once()
    w.target.ban.assert_awaited_once()
    assert len(cog.messages[(100, w.target.id)]) == 1


async def test_editing_multiple_messages_into_duplicates_cannot_bypass(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    messages = [message(w, i, f'original {i}') for i in range(3)]
    for m in messages:
        assert not await cog.handle(m)
    for m in messages:
        m.content = 'duplicate'
        await cog.handle(m, edited=True)
    assert all(m.delete.await_count == 1 for m in messages)
    w.target.timeout.assert_awaited_once()
    assert len(cog.messages[(100, w.target.id)]) == 3


async def test_failed_deletion_still_bans_and_logs_failure(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    m = message(w, 1, 'forbidden')
    m.delete.side_effect = discord.Forbidden(NS(status=403, reason='Forbidden'), 'Missing Manage Messages')
    assert await cog.handle(m)
    w.target.ban.assert_awaited_once()
    record = await bot.store.one("SELECT detail FROM audit WHERE action='automod.action'")
    assert 'Delete 1 failed' in record['detail'] and 'action: ban' in record['detail']


async def test_failed_ban_still_deletes_and_does_not_claim_success(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    m = message(w, 1, 'forbidden')
    w.target.ban.side_effect = discord.Forbidden(NS(status=403, reason='Forbidden'), 'Missing Ban Members')
    assert await cog.handle(m)
    m.delete.assert_awaited_once()
    record = await bot.store.one("SELECT detail FROM audit WHERE action='automod.action'")
    assert 'action: delete' in record['detail'] and 'Missing Ban Members' in record['detail']


async def test_moderated_command_does_not_execute(security):
    bot, w, _ = security
    await enable_words(bot, w)
    bot.invoke = AsyncMock()
    m = routed_message(bot, w, '.tag forbidden')
    await bot.on_message(m)
    bot.invoke.assert_not_awaited()
    m.delete.assert_awaited_once()
    w.target.ban.assert_awaited_once()


async def test_observe_mode_and_disable_prevent_bans(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    p, v = await cog.config_for(100)
    p['mode'] = 'observe'
    await cog.save(100, p, v, 1)
    assert not await cog.handle(message(w, 1, 'forbidden'))
    p, v = await cog.config_for(100)
    p.update(mode='enforce', enabled=False)
    await cog.save(100, p, v, 1)
    assert not await cog.handle(message(w, 2, 'forbidden'))
    w.target.ban.assert_not_awaited()


async def test_attachment_only_edit_is_rechecked(security):
    bot, w, _ = security
    cog = await enable_words(bot, w)
    p, v = await cog.config_for(100)
    p['rules']['attachments']['enabled'] = True
    await cog.save(100, p, v, 1)
    m = message(w, 1)
    assert not await cog.handle(m)
    m.attachments = [NS(id=1, filename='malware.exe')]
    w.guild.get_channel_or_thread.return_value = w.channel
    w.channel.fetch_message = AsyncMock(return_value=m)
    await cog.on_raw_message_edit(NS(cached_message=None, data={'attachments': []}, guild_id=100, channel_id=w.channel.id, message_id=1))
    m.delete.assert_awaited_once()


def test_old_automod_config_migrates_without_losing_choices():
    p = default_config()
    p['rules'].pop('repetition')
    for key in ('repeat_count', 'repeat_characters', 'ban_words'):
        p.pop(key)
    p['rules']['invites']['action'] = 'kick'
    upgraded = validate_config(p)
    assert upgraded['rules']['repetition']['enabled']
    assert upgraded['rules']['invites']['action'] == 'kick'
    assert upgraded['ban_words'] == []


@pytest.mark.parametrize('word', [' ', '\u200b', 'a' * 101])
def test_blank_and_oversized_banwords_rejected(word):
    p = default_config()
    p['ban_words'] = [word]
    with pytest.raises(ValueError):
        validate_config(p)


async def test_reply_cleanup_send_reply_and_interactive_menus(security):
    bot, w, _ = security
    m = routed_message(bot, w, '.ping', author=w.owner)
    ctx = await bot.get_context(m)
    with patch.object(commands.Context, 'send', new_callable=AsyncMock) as send:
        await ctx.send('hello')
        assert send.call_args.kwargs['delete_after'] == 20
        await ctx.reply('hello')
        assert send.call_args.kwargs['delete_after'] == 20
        await ctx.send('explicit', delete_after=8)
        assert send.call_args.kwargs['delete_after'] == 8
        view = discord.ui.View(timeout=180)
        await ctx.send('menu', view=view)
        assert send.call_args.kwargs['delete_after'] == 185
        view.stop()
        persistent = discord.ui.View(timeout=None)
        await ctx.send('panel', view=persistent)
        assert send.call_args.kwargs['delete_after'] is None
        persistent.stop()
    ctx = await parse(bot, w, '.autodelete 45')
    await ctx.command.invoke(ctx)
    config, _ = await bot.store.config(100)
    assert config['reply_delete_after'] == 45
    ctx = await bot.get_context(m)
    with patch.object(commands.Context, 'send', new_callable=AsyncMock) as send:
        await ctx.send('new delay')
        assert send.call_args.kwargs['delete_after'] == 45
    ctx = await parse(bot, w, '.autodelete 0')
    await ctx.command.invoke(ctx)
    ctx = await bot.get_context(m)
    with patch.object(commands.Context, 'send', new_callable=AsyncMock) as send:
        await ctx.send('no cleanup')
        assert send.call_args.kwargs['delete_after'] is None


async def test_dashboard_config_preserves_custom_cleanup(security):
    _, w, _ = security
    previous = copy.deepcopy(DEFAULT_CONFIG)
    previous['reply_delete_after'] = 45
    raw = copy.deepcopy(previous)
    raw.pop('reply_delete_after')
    assert validate_server_config(raw, w.guild, w.owner, previous)['reply_delete_after'] == 45


async def test_role_management_respects_permissions_and_hierarchy(security):
    bot, w, _ = security
    cog = bot.get_cog('Moderation')
    ctx = await parse(bot, w, '.role')
    await cog.change_role(ctx, w.target, w.role, True)
    w.target.add_roles.assert_awaited_once()
    w.role.position = 100
    with pytest.raises(ValueError):
        await cog.change_role(ctx, w.target, w.role, True)
    w.role.position = 10
    w.role.permissions.administrator = True
    ctx.author = w.staff
    w.staff.guild_permissions.manage_roles = True
    with pytest.raises(commands.CheckFailure):
        await cog.change_role(ctx, w.target, w.role, True)


def test_no_packaged_images_or_removed_modules():
    root = Path(__file__).resolve().parents[1]
    assert not any(p.is_file() for p in root.rglob('*.png'))
    for path in ('cogs', 'games', 'prodia', 'fortune/legacy.py'):
        assert not (root / path).exists()


@pytest.mark.parametrize('name', ['role add', 'role remove'])
async def test_role_children_require_manage_roles(security, name):
    bot, w, _ = security
    ctx = await parse(bot, w, '.role', w.target)
    with pytest.raises(commands.MissingPermissions):
        await bot.get_command(name).can_run(ctx)


async def test_dashboard_banwords_requires_ban_permission(security):
    from aiohttp import web
    from fortune.dashboard import Dashboard
    bot, w, _ = security
    w.staff.guild_permissions = discord.Permissions(manage_guild=True)
    dashboard = Dashboard(bot)
    dashboard.context = AsyncMock(return_value=(w.guild, w.staff))
    p, version = await bot.get_cog('Automod').config_for(100)
    p['ban_words'] = ['forbidden']
    dashboard.body = AsyncMock(return_value={'config': p, 'version': version})
    with pytest.raises(web.HTTPForbidden):
        await dashboard.save_automod(NS())
    assert not (await bot.get_cog('Automod').config_for(100))[0]['ban_words']
    w.staff.guild_permissions.ban_members = True
    response = await dashboard.save_automod(NS())
    assert response.status == 200
    assert (await bot.get_cog('Automod').config_for(100))[0]['ban_words'] == ['forbidden']
