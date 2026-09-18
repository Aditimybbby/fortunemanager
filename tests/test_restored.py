"""Offline regressions for the actual Olympus integration and event eligibility."""
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import pytest
from discord.ext import commands
from fortune.bot import FortuneManager
from fortune.help import HelpView, categories
from fortune.event_rules import invite_window
from fortune.legacy_storage import legacy_path, prepare_databases
from test_core import world
from test_events import setup, active_event, claim, REWARDS


async def test_full_olympus_startup_help_and_antinuke(tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setenv('LEGACY_DATA_DIR', str(tmp_path/'legacy'))
    # Existing deployments often have an empty LEGACY_COGS setting.
    monkeypatch.setenv('LEGACY_COGS', '')
    async with FortuneManager(database=tmp_path/'fortune.db',start_dashboard=False) as bot:
        await bot.setup_hook()
        w=world()
        w.channel.permissions_for.side_effect=lambda member: member.guild_permissions
        bot._connection.user=NS(id=w.guild.me.id, display_avatar=NS(url='https://example.com/avatar.png'))
        assert not bot.legacy_failures, bot.legacy_errors
        for name in ('help','h','antinuke','anti','automod','whitelist','unwhitelist',
                     'extraowner','nightmode','emergency','role','greet','autorole',
                     'afk','gstart','play','check','event','invites','modulestatus'):
            assert bot.get_command(name),name
        for name in ('AntiBan','AntiChannelDelete','AntiRoleDelete','AntiSpam','AntiLink'):
            assert bot.get_cog(name),name
        assert bot.get_command('ban').cog.__class__.__module__=='fortune.moderation'
        assert bot.get_cog('OlympusModeration')

        async def context(content,author=None):
            message=MagicMock(spec=discord.Message)
            message.content=content;message.author=author or w.owner
            message.guild=w.guild;message.channel=w.channel
            message._state=bot._connection
            message.created_at=discord.utils.utcnow();message.edited_at=None
            message.attachments=[]
            ctx=await bot.get_context(message)
            ctx.send=AsyncMock(return_value=NS(edit=AsyncMock(),delete=AsyncMock()))
            ctx.reply=ctx.send
            return ctx

        ctx=await context('.help')
        await ctx.command.invoke(ctx)
        sent=ctx.send.call_args.kwargs
        view=sent['view']
        assert isinstance(view,HelpView)
        assert any('Security' in opt.label for opt in view.children[-1].options)
        assert any('Events' in opt.label for opt in view.children[-1].options)
        for page in view.pages:
            assert len(page)<=6000
            assert len(page.fields)<=25
            assert all(len(f.name)<=256 and len(f.value)<=1024 for f in page.fields)
        interaction=NS(user=w.owner,response=NS(edit_message=AsyncMock(),send_message=AsyncMock()))
        assert await view.interaction_check(interaction)
        await view.show(interaction,9999)
        assert view.index==len(view.pages)-1 and view.next.disabled
        await view.show(interaction,-9)
        assert view.index==0 and view.previous.disabled
        interaction.user=w.target
        assert not await view.interaction_check(interaction)
        ctx=await context('.h antinuke')
        await ctx.command.invoke(ctx)
        assert 'antinuke' in ctx.send.call_args.kwargs['embed'].title.lower()
        ctx=await context('.help Security')
        await ctx.command.invoke(ctx)
        assert 'Security' in ctx.send.call_args.kwargs['embed'].title

        ctx=await context('.antinuke')
        await ctx.command.invoke(ctx)
        assert 'Antinuke' in ctx.send.call_args.kwargs['embed'].title
        ctx=await context('.antinuke',w.target)
        bot.get_command('antinuke').reset_cooldown(ctx)
        with pytest.raises(commands.MissingPermissions):
            await ctx.command.invoke(ctx)
        # Run the original enable/disable command with Discord I/O mocked.
        w.guild.create_role=AsyncMock(return_value=w.role)
        w.guild.edit_role_positions=AsyncMock()
        ctx=await context('.antinuke enable')
        bot.get_command('antinuke').reset_cooldown(ctx)
        with patch('cogs.commands.antinuke.asyncio.sleep',new=AsyncMock()):
            await ctx.command.invoke(ctx)
        anti=bot.get_cog('Antinuke')
        row=await (await anti.db.execute('SELECT status FROM antinuke WHERE guild_id=?',(w.guild.id,))).fetchone()
        assert row[0]==1
        # An actual original event listener reacts when enabled.
        async def audit_logs(**kwargs):
            yield NS(target=w.target,user=w.staff,created_at=discord.utils.utcnow())
        w.guild.audit_logs=audit_logs
        w.guild.ban=AsyncMock();w.guild.unban=AsyncMock()
        await bot.get_cog('AntiBan').on_member_ban(w.guild,w.target)
        w.guild.ban.assert_awaited_once_with(w.staff,reason='Member Ban | Unwhitelisted User')
        ctx=await context('.antinuke disable')
        bot.get_command('antinuke').reset_cooldown(ctx)
        await ctx.command.invoke(ctx)
        assert await (await anti.db.execute('SELECT status FROM antinuke WHERE guild_id=?',(w.guild.id,))).fetchone() is None
        # Re-preparing persisted DBs must not undo edits.
        prepare_databases()
        assert await (await anti.db.execute('SELECT status FROM antinuke WHERE guild_id=?',(w.guild.id,))).fetchone() is None
        ctx=await context('.automod')
        await ctx.command.invoke(ctx)
        assert ctx.send.await_count
        unknown=await context('.notarealcommand')
        await bot.on_command_error(unknown,commands.CommandNotFound('notarealcommand'))
        assert unknown.send.call_args.kwargs['embed'].title=='Unknown command'
        print(f'Full startup: {len(bot.legacy_loaded)} Olympus modules, {len(list(bot.walk_commands()))} commands, zero load failures.')


def test_event_window_compares_instants_and_preserves_ticket_boundary():
    rows=[
        dict(member_id=1,joined_at='2026-09-18T11:00:00+02:00'), # 09:00 UTC: before
        dict(member_id=2,joined_at='2026-09-18T12:30:00+02:00'), # 10:30: counts
        dict(member_id=3,joined_at='2026-09-18T11:00:00.000000Z'), # exact cutoff
        dict(member_id=4,joined_at='2026-09-18T11:00:00.000001+00:00'),
        dict(member_id=5,joined_at='invalid'),
    ]
    accepted,summary=invite_window(rows,'2026-09-18T10:00:00+00:00','2026-09-18T11:00:00Z')
    assert [r['member_id'] for r in accepted]==[2,3]
    assert summary==dict(total_verified=5,before_event=1,after_cutoff=1,invalid_timestamp=1)


async def add_invites(bot,w,dates):
    for uid,date in enumerate(dates,10000):
        await bot.store.execute('INSERT INTO invite_members VALUES(?,?,?,?,?,?,?)',
            (w.guild.id,uid,w.target.id,'code','invite',date,'2020-01-01T00:00:00+00:00'))


async def test_no_rules_rewards_do_not_depend_on_profile_lookup(setup):
    bot,w=setup;c=bot.get_cog('Events')
    eid=await active_event(bot,w,rules=False);cl=await claim(bot,w,eid,selected=1)
    await add_invites(bot,w,['2026-02-01T00:00:00+00:00']*6)
    w.guild.fetch_member.side_effect=discord.HTTPException(NS(status=503,reason='Unavailable'),'Try again')
    _,_,result=await c.evaluate(w.guild,cl)
    assert result['reward']=='$1.80' and result['eligible']==6 and not result['blocked']
    w.guild.fetch_member.assert_not_awaited()
    await c.present_rewards(w.guild,w.channel,cl)
    assert w.channel.send.call_args.kwargs['embed'].title=='Choose your reward'


async def test_event_only_count_and_report_explains_lifetime_mismatch(setup):
    bot,w=setup;c=bot.get_cog('Events')
    eid=await active_event(bot,w,rules=False);cl=await claim(bot,w,eid,selected=1)
    after=(discord.utils.utcnow()+timedelta(days=1)).isoformat()
    await add_invites(bot,w,['2025-01-01T00:00:00+00:00',after])
    await c.present_rewards(w.guild,w.channel,cl)
    first=w.channel.send.call_args_list[-2].kwargs['embed']
    fields={f.name:f.value for f in first.fields}
    assert 'All-time verified: **2**' in fields['Invite count breakdown']
    assert 'Excluded before event: **1**' in fields['Invite count breakdown']
    assert 'Excluded after ticket/event end: **1**' in fields['Invite count breakdown']
    assert w.channel.send.call_args.kwargs['embed'].title=='No verified invites in this event window'


async def test_rules_fetch_failure_reports_pending_not_ineligible(setup):
    bot,w=setup;c=bot.get_cog('Events')
    eid=await active_event(bot,w);cl=await claim(bot,w,eid)
    await add_invites(bot,w,['2026-02-01T00:00:00+00:00']*2)
    w.guild.fetch_member.side_effect=discord.HTTPException(NS(status=503,reason='Unavailable'),'Try again')
    await c.present_rewards(w.guild,w.channel,cl)
    assert w.channel.send.call_args.kwargs['embed'].title=='Invite verification pending'
    # The report must reuse the same evaluation, not repeat API requests.
    assert w.guild.fetch_member.await_count==2


async def test_rules_still_apply_deductions_and_guild_avatar_is_a_picture(setup):
    bot,w=setup;c=bot.get_cog('Events')
    eid=await active_event(bot,w);cl=await claim(bot,w,eid,selected=1)
    await add_invites(bot,w,['2026-02-01T00:00:00+00:00']*6)
    w.staff.avatar=None;w.staff.guild_avatar=NS()
    w.guild.fetch_member.side_effect=None;w.guild.fetch_member.return_value=w.staff
    _,_,result=await c.evaluate(w.guild,cl)
    assert result['eligible']==6 and result['reward']=='$1.80' and not result['blocked']
    cl['promo']='server'
    _,_,result=await c.evaluate(w.guild,cl)
    assert result['eligible']==2 and result['reward']=='$0.60'
    assert ('Server promotion',4) in result['deductions']
