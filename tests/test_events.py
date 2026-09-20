import asyncio
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import discord
import pytest
from fortune.bot import FortuneManager
from fortune.events import Events, Builder, EventPanel, PromoView, RewardView, EmbedForm, RewardForm, PROOF_GUILD, PROOF_CHANNEL
from fortune.event_rules import parse_rewards, reward_for, calculate, four_months_before, RULES
from fortune.tracking import Tracking, attribute
from fortune.store import Store
from test_core import world

AT=datetime(2026,9,17,tzinfo=timezone.utc)
REWARDS=parse_rewards('1 invite = 0.3$\n6 invites = 1$\n10 invites = nitro booster\n30 invites = nitro basic')

def invite(uid=1,**changes):
    return dict(member_id=uid,account_created_at='2020-01-01T00:00:00+00:00',**changes)

@pytest.mark.parametrize('n,selected,eligible,reward',[
    (0,1,0,'No reward'),(1,1,0,'No reward'),(5,1,4,'$1.20'),
    (6,1,6,'$1.80'),(6,6,6,'$1.00'),(8,1,8,'$2.40'),(8,6,8,'$1.00'),
    (10,1,10,'$3.00'),(10,6,10,'$1.00'),(10,10,10,'nitro booster'),
    (29,10,28,'nitro booster'),(30,30,30,'nitro basic'),
    (10,None,10,'Choose a reward'),(10,30,10,'No reward'),(30,999,30,'No reward')])
def test_reward_examples(n,selected,eligible,reward):
    r=calculate([invite(i) for i in range(n)],REWARDS,rules=True,promo='dm',at=AT,selected_threshold=selected)
    assert (r['eligible'],r['reward'])==(eligible,reward)


@pytest.mark.parametrize('text',['','0 invites = $1','1 = $-2','1 = $0','1 = $0.001','1 = x\n1 = y','broken','2 = '+('a'*101)])
def test_invalid_rewards(text):
    with pytest.raises(ValueError): parse_rewards(text)


def test_deductions_even_rounding_and_no_rules():
    rows=[invite(i) for i in range(10)]
    rows[0].update(left=True,rejoined=True)
    dm=calculate(rows,REWARDS,rules=True,promo='dm',at=AT)
    server=calculate(rows,REWARDS,rules=True,promo='server',at=AT)
    assert dm['eligible']==6
    assert server['eligible']==4  # -4 server, -2 left; no rejoin penalty
    assert calculate(rows,REWARDS,rules=False,promo='server',at=AT)['eligible']==10
    assert calculate(rows[:5],REWARDS,rules=False,promo='dm',at=AT,selected_threshold=1)['reward']=='$1.50'


def test_profile_onboarding_age_findings_and_proof():
    rows=[invite(i) for i in range(12)]
    rows[0].update(no_avatar=True,onboarding_missing=True,account_created_at=AT.isoformat())
    assert calculate(rows,REWARDS,rules=True,promo='dm',at=AT)['blocked']
    assert calculate(rows,REWARDS,rules=True,promo='server',at=AT)['eligible']==4
    assert calculate(rows,REWARDS,rules=True,promo='dm',at=AT,proof_overdue=True)['reward']=='No reward'
    findings=[dict(code='invalid',quantity=2),dict(code='no_bio',quantity=1)]
    r=calculate([invite(i) for i in range(12)],REWARDS,rules=True,promo='server',at=AT,findings=findings)
    assert r['eligible']==4
    assert calculate(rows,REWARDS,rules=False,promo='dm',at=AT,findings=[dict(code='alt',quantity=1)],proof_overdue=True)['blocked']==[]
    assert four_months_before(datetime(2026,6,30,tzinfo=timezone.utc)).day==28


@pytest.mark.parametrize('before,after,vb,va,expected',[
    ({'a':(1,7)},{'a':(2,7)},0,0,(7,'a','invite')),
    ({},{'new':(1,7)},0,0,(7,'new','invite')),
    (None,{'a':(2,7)},0,0,(None,None,'unknown')),
    ({'a':(0,7)},{'a':(2,7)},0,0,(None,None,'unknown')),
    ({'a':(0,7),'b':(0,8)},{'a':(1,7),'b':(1,8)},0,0,(None,None,'unknown')),
    ({},{},3,4,(None,None,'vanity')),
    ({'a':(0,7)},{'a':(1,7)},3,4,(None,None,'unknown')),
    ({'a':(0,7)},{},0,0,(None,None,'unknown')),
])
def test_conservative_attribution(before,after,vb,va,expected):
    assert attribute(before,after,vb,va)==expected


@pytest.fixture
async def setup(tmp_path):
    async with FortuneManager(database=tmp_path/'test.db',start_dashboard=False,load_legacy=False) as bot:
        await bot.setup_hook()
        w=world()
        w.guild.features=[]
        for member in w.guild.members:
            member.created_at=datetime(2020,1,1,tzinfo=timezone.utc)
            member.joined_at=discord.utils.utcnow()
            member.avatar=NS()
        w.channel.category_id=w.category.id
        yield bot,w


async def active_event(bot,w,rules=True):
    return await bot.store.execute('INSERT INTO events(guild_id,owner_id,channel_id,message_id,category_id,started_at,status,data) VALUES(?,?,?,?,?,?,?,?)',
        (w.guild.id,w.owner.id,w.channel.id,500,w.category.id,'2026-01-01T00:00:00+00:00','active',json.dumps(dict(title='Test',description='Event',color='63d6ac',rewards=REWARDS,rules=rules,owner_ids=[w.owner.id]))))


async def claim(bot,w,eid,promo='dm',selected=None):
    cid=await bot.store.execute('INSERT INTO event_claims(event_id,guild_id,user_id,channel_id,cutoff,promo) VALUES(?,?,?,?,?,?)',
        (eid,w.guild.id,w.target.id,w.channel.id,discord.utils.utcnow().isoformat(),promo))
    await bot.store.execute('UPDATE event_claims SET selected_reward=? WHERE id=?',(selected,cid))
    return await bot.store.one('SELECT * FROM event_claims WHERE id=?',(cid,))


def context(bot,w,author=None):
    return NS(bot=bot,guild=w.guild,channel=w.channel,author=author or w.owner,clean_prefix=".",send=AsyncMock(),message=NS(attachments=[]))


def interaction(w):
    i=MagicMock(spec=discord.Interaction)
    i.user=w.owner;i.guild=w.guild;i.guild_id=w.guild.id;i.channel=w.channel;i.channel_id=w.channel.id
    i.response=NS(send_message=AsyncMock(),edit_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock(),is_done=lambda:False)
    i.followup=NS(send=AsyncMock());i.message=NS(id=500,edit=AsyncMock());i.edit_original_response=AsyncMock()
    return i


async def test_commands_and_views_restart(setup):
    bot,w=setup
    for name in ['event','event end','event owners','eventticket','check','proof','invites','messages','leaderboard','eventrule','eventunflag','eventreview','trackingstatus']:
        assert bot.get_command(name),name
    assert any(isinstance(v,EventPanel) and v.is_persistent() for v in bot.persistent_views)
    # Persist an actual count and verify via a separately opened store.
    await bot.store.execute('INSERT INTO message_counts VALUES(?,?,?)',(w.guild.id,w.target.id,17))
    reopened=Store(bot.store.path)
    await reopened.init()
    assert (await reopened.one('SELECT total FROM message_counts'))['total']==17


async def test_message_tracking_isolated_excludes_bots_and_webhooks(setup):
    bot,w=setup;t=bot.get_cog('Tracking')
    message=NS(guild=w.guild,author=w.target,webhook_id=None)
    await asyncio.gather(*[t.on_message(message) for _ in range(20)])
    assert (await bot.store.one('SELECT total FROM message_counts'))['total']==20
    w.target.bot=True
    await t.on_message(message)
    w.target.bot=False;message.webhook_id=8
    await t.on_message(message)
    message.webhook_id=None;message.guild=None
    await t.on_message(message)
    assert (await bot.store.one('SELECT total FROM message_counts'))['total']==20


async def test_join_leave_rejoin_single_credit_and_duplicate_gateway(setup):
    bot,w=setup;t=bot.get_cog('Tracking')
    t.cache[w.guild.id]={'code':(0,w.owner.id)};t.vanity[w.guild.id]=0
    t.snapshot=AsyncMock(return_value=({'code':(1,w.owner.id)},0))
    await t.on_member_join(w.target)
    await t.on_member_join(w.target)
    await t.on_member_remove(w.target)
    await t.on_member_remove(w.target)
    t.snapshot.return_value=({'code':(2,w.staff.id)},0)
    await t.on_member_join(w.target)
    rows=await bot.store.rows('SELECT * FROM invite_members')
    assert len(rows)==1 and rows[0]['inviter_id']==w.owner.id
    assert [r['kind'] for r in await bot.store.rows('SELECT kind FROM member_movements ORDER BY id')]==['join','leave','rejoin']


async def test_preexisting_member_rejoin_not_credited(setup):
    bot,w=setup;t=bot.get_cog('Tracking')
    await t.on_member_remove(w.target)
    t.cache[w.guild.id]={'x':(0,1)}
    t.snapshot=AsyncMock(return_value=({'x':(1,1)},0))
    await t.on_member_join(w.target)
    row=await bot.store.one('SELECT * FROM invite_members')
    assert row['inviter_id'] is None and row['source']=='preexisting'


async def test_builder_authorization_and_shared_embed(setup):
    bot,w=setup;v=Builder(bot.get_cog('Events'),w.owner.id);i=interaction(w)
    assert await v.interaction_check(i)
    i.user=w.target
    assert not await v.interaction_check(i)
    v.data.update(rewards=REWARDS,rules=True)
    embeds=v.preview()
    assert len(embeds)==2 and embeds[1].description==RULES
    assert 'author' not in embeds[0].to_dict() and 'image' not in embeds[0].to_dict()
    assert 'Rewards' in [f.name for f in embeds[0].fields]


async def test_publish_duplicate_and_event_end(setup):
    bot,w=setup;c=bot.get_cog('Events');v=Builder(c,w.owner.id);i=interaction(w)
    v.data.update(rewards=REWARDS,rules=True);v.category_id=w.category.id
    await c.publish(i,v)
    assert (await c.active(w.guild.id))['message_id']==500
    sent=w.channel.send.await_count
    await c.publish(i,v)
    assert w.channel.send.await_count==sent
    await Events.end_event.callback(c,context(bot,w))
    assert await c.active(w.guild.id) is None


async def test_event_ticket_concurrent_creation_and_cutoff(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w)
    e=await c.active(w.guild.id)
    results=await asyncio.gather(c.open_ticket(w.guild,w.target,e),c.open_ticket(w.guild,w.target,e))
    assert len(results)==2
    w.guild.create_text_channel.assert_awaited_once()
    rows=await bot.store.rows('SELECT * FROM event_claims')
    assert len(rows)==1 and rows[0]['channel_id']==w.channel.id
    assert rows[0]['cutoff']<=discord.utils.utcnow().isoformat()


async def test_claim_access_control(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);await claim(bot,w,eid)
    with pytest.raises(ValueError,match='Only the ticket owner'):
        await c.claim_for(context(bot,w,w.staff))
    assert (await c.claim_for(context(bot,w,w.target)))['user_id']==w.target.id


async def test_existing_ticket_cutoff_and_wrong_category(setup):
    bot,w=setup;c=bot.get_cog('Events');await active_event(bot,w)
    opened=discord.utils.utcnow().isoformat()
    await bot.store.execute("INSERT INTO tickets(guild_id,owner_id,channel_id,panel_id,option_id,status,created_at) VALUES(?,?,?,?,?,'open',?)",(w.guild.id,w.target.id,w.channel.id,'p','o',opened))
    w.channel.category_id=999
    with pytest.raises(ValueError,match='inside your event ticket'):
        await c.claim_for(context(bot,w,w.target))
    w.channel.category_id=w.category.id
    assert (await c.claim_for(context(bot,w,w.target)))['cutoff']==opened


async def test_evaluation_cutoff_guild_isolation_and_unknown(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid,selected=1)
    before=(datetime.fromisoformat(cl['cutoff'])-timedelta(seconds=1)).isoformat()
    after=(datetime.fromisoformat(cl['cutoff'])+timedelta(seconds=1)).isoformat()
    for uid,gid,source,when in [(10,w.guild.id,'invite',before),(11,w.guild.id,'invite',before),(12,w.guild.id,'invite',after),(13,w.guild.id,'unknown',before),(14,999,'invite',before)]:
        await bot.store.execute('INSERT INTO invite_members VALUES(?,?,?,?,?,?,?)',(gid,uid,w.target.id,'c',source,when,'2020-01-01T00:00:00+00:00'))
    w.guild.fetch_member.side_effect=None;w.guild.fetch_member.return_value=w.staff
    _,_,r=await c.evaluate(w.guild,cl)
    assert r['raw']==2 and r['reward']=='$0.60'
    await c.report(w.guild,w.channel,cl)
    w.channel.edit.assert_awaited_once()
    sent=w.channel.send.call_args.kwargs
    assert sent['content']=='<@1>'
    assert sent['allowed_mentions'].users[0].id==1


async def test_manual_findings_and_review_no_duplicate(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid)
    ctx=context(bot,w)
    await Events.eventrule.callback(c,ctx,'invalid',2,reason='Two duplicate accounts verified by staff')
    finding=await bot.store.one('SELECT * FROM event_findings')
    assert finding['quantity']==2
    await Events.eventunflag.callback(c,ctx,finding['id'],reason='Evidence corrected')
    assert (await bot.store.one('SELECT active FROM event_findings'))['active']==0
    await Events.eventreview.callback(c,ctx,'reject',reason='No eligible invitations')
    with pytest.raises(ValueError,match='already reviewed'):
        await Events.eventreview.callback(c,ctx,'approve',reason='attempt')
    await Events.eventreview.callback(c,ctx,'reopen',reason='New evidence')
    assert (await bot.store.one('SELECT status FROM event_claims'))['status']=='pending'


async def test_approval_recalculates_and_requires_proof(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid,selected=1)
    c.evaluate=AsyncMock(return_value=({},dict(rules=True),dict(blocked=[],reward='$1.20',proof=False,tier=1)))
    with pytest.raises(ValueError,match='Screenshot proof'):
        await Events.eventreview.callback(c,context(bot,w),'approve',reason='Reviewed')
    c.evaluate.return_value=({},dict(rules=True),dict(blocked=[],reward='$1.20',proof=True,tier=1))
    await Events.eventreview.callback(c,context(bot,w),'approve',reason='All manual rules and evidence checked')
    row=await bot.store.one('SELECT * FROM event_claims')
    assert row['status']=='approved' and json.loads(row['result'])['reward']=='$1.20'


async def test_promo_choice_locked_unless_admin(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid)
    c.present_rewards=AsyncMock();i=interaction(w);i.user=w.target
    v=PromoView(c,cl,w.target.id)
    await v.choose(i,'server')
    c.present_rewards.assert_not_awaited()
    i.user=w.owner
    await v.choose(i,'server')
    c.present_rewards.assert_awaited_once()


async def test_proof_attachment_required_and_destination(setup):
    bot,w=setup;c=bot.get_cog('Events');ctx=context(bot,w,w.target)
    w.guild.id=PROOF_GUILD
    with pytest.raises(ValueError,match='Attach an image'):
        await Events.proof.callback(c,ctx,title='My proof')
    ctx.message.attachments=[NS(size=5,read=AsyncMock(return_value=b'not an image'))]
    with pytest.raises(ValueError,match='PNG'):
        await Events.proof.callback(c,ctx,title='My proof')
    ctx.message.attachments=[NS(size=40,read=AsyncMock(return_value=b'\x89PNG\r\n\x1a\n'+b'example'))]
    bot.get_channel=lambda cid: w.channel if cid==PROOF_CHANNEL else None
    await Events.proof.callback(c,ctx,title='Payment proof')
    sent=w.channel.send.call_args.kwargs
    assert sent['embed'].title=='Payment proof'
    assert sent['embed'].image.url=='attachment://proof.png'
    assert sent['file'].filename=='proof.png'
    assert (await bot.store.one('SELECT title FROM event_proofs'))['title']=='Payment proof'
    w.guild.id=123
    with pytest.raises(ValueError,match='configured only'):
        await Events.proof.callback(c,ctx,title='Must not forward across servers')


async def test_configured_prefix_replaces_default_prefix(setup):
    bot,w=setup
    config,version=await bot.store.config(w.guild.id)
    config['prefix']='!'
    await bot.store.save_config(w.guild.id,config,version)
    prefixes=await bot.resolve_prefix(bot,NS(guild=w.guild))
    assert prefixes == '!'

async def test_chat_check_reset_only_outside_allowed_channels(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w)
    ctx=context(bot,w,w.target)
    assert await c.record_chat_check(ctx)
    assert await c.record_chat_check(ctx)
    assert len(await bot.store.rows('SELECT * FROM event_findings'))==1
    await Events.check_channel.callback(c,context(bot,w),w.channel)
    assert not await c.record_chat_check(ctx)
    assert not await c.record_chat_check(context(bot,w,w.owner))


async def test_reward_modal_to_rules_to_category_and_back(setup):
    bot,w=setup;c=bot.get_cog('Events');b=Builder(c,w.owner.id);i=interaction(w)
    f=RewardForm(b);f.rewards._value='1 invite = $0.30\n6 invites = $1'
    await f.on_submit(i)
    assert b.stage=='rules' and len(b.children)==3
    await b.children[0].callback(i)
    assert b.stage=='category'
    selector=b.children[0]
    selector._values=[NS(id=w.category.id)]
    await selector.callback(i)
    assert b.category_id==w.category.id
    assert b.children[0].label=='Publish event'
    await b.children[1].callback(i)
    replacement=i.response.edit_message.call_args.kwargs['view']
    assert replacement.data['rewards']==parse_rewards('1 = $0.30\n6 = $1')
    assert len(replacement.children)==3


async def test_no_rules_choice_and_failed_permission_publish(setup):
    bot,w=setup;c=bot.get_cog('Events');b=Builder(c,w.owner.id);i=interaction(w)
    f=RewardForm(b);f.rewards._value='1 = $0.30'
    await f.on_submit(i)
    await b.children[1].callback(i)
    assert b.data['rules'] is False
    b.category_id=w.category.id
    w.category.permissions_for.return_value=discord.Permissions.none()
    await c.publish(i,b)
    assert await c.active(w.guild.id) is None
    w.channel.send.assert_not_awaited()


async def test_fetch_failure_blocks_reward(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid)
    await bot.store.execute('INSERT INTO invite_members VALUES(?,?,?,?,?,?,?)',(w.guild.id,77,w.target.id,'code','invite','2026-02-01T00:00:00+00:00','2020-01-01T00:00:00+00:00'))
    w.guild.fetch_member.side_effect=discord.HTTPException(NS(status=503,reason='Unavailable'), 'Try again')
    _,_,result=await c.evaluate(w.guild,cl)
    assert result['unavailable']==[77] and result['blocked']
    assert result['reward']=='Verification pending'


async def test_proof_delivery_failure_not_recorded(setup):
    bot,w=setup;c=bot.get_cog('Events');w.guild.id=PROOF_GUILD
    ctx=context(bot,w,w.target)
    ctx.message.attachments=[NS(size=20,read=AsyncMock(return_value=b'\x89PNG\r\n\x1a\nexample'))]
    bot.get_channel=lambda _:w.channel
    w.channel.send.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'Missing permissions')
    with pytest.raises(discord.Forbidden):
        await Events.proof.callback(c,ctx,title='Proof')
    assert await bot.store.one('SELECT * FROM event_proofs') is None


async def test_event_admin_command_checks(setup):
    bot,w=setup
    for name in ['event','event end','event owners','event cmds','eventrule','eventunflag','eventreview']:
        command=bot.get_command(name)
        for check in command.checks:
            with pytest.raises(Exception):
                await check(context(bot,w,w.target))
            assert await check(context(bot,w,w.owner))

async def test_reward_picker_offers_all_eligible_options_without_default(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid)
    view=RewardView(c,cl,REWARDS,10)
    options=view.children[0].options
    assert [o.value for o in options]==['1','6','10']
    assert not any(o.default for o in options)
    assert '$3.00' in options[0].label
    assert cl['selected_reward'] is None
    c.evaluate=AsyncMock(return_value=({},dict(rewards=REWARDS),dict(eligible=10,blocked=[],raw=10,deductions=[],rounded=0)))
    await c.present_rewards(w.guild,w.channel,cl)
    assert isinstance(w.channel.send.call_args.kwargs['view'],RewardView)
    assert (await bot.store.one('SELECT selected_reward FROM event_claims'))['selected_reward'] is None


async def test_only_claimant_can_select_and_saved_choice_survives_restart(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid)
    c.evaluate=AsyncMock(return_value=({}, {},dict(blocked=[],tier=1,reward='$3.00')))
    c.report=AsyncMock()
    view=RewardView(c,cl,REWARDS,10);i=interaction(w)
    await view.choose(i,1)  # Even the administrator cannot pick for the user.
    c.evaluate.assert_not_awaited()
    i.user=w.target
    await view.choose(i,1)
    c.report.assert_awaited_once()
    stored=await Store(bot.store.path).one('SELECT * FROM event_claims')
    assert stored['selected_reward']==1
    assert stored['cutoff']==cl['cutoff']
    assert stored['status']=='pending'


@pytest.mark.parametrize('kind',['lost_eligibility','blocked','reviewed'])
async def test_stale_reward_picker_cannot_override_eligibility_or_approval(setup,kind):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);cl=await claim(bot,w,eid)
    c.evaluate=AsyncMock(return_value=({}, {},dict(blocked=['RESET'] if kind=='blocked' else [],tier=None,reward='No reward')))
    c.report=AsyncMock()
    if kind=='reviewed':
        await bot.store.execute("UPDATE event_claims SET status='approved' WHERE id=?",(cl['id'],))
    view=RewardView(c,cl,REWARDS,10);i=interaction(w);i.user=w.target
    await view.choose(i,10)
    assert (await bot.store.one('SELECT selected_reward FROM event_claims'))['selected_reward'] is None
    c.report.assert_not_awaited()


async def test_approval_requires_explicit_user_choice(setup):
    bot,w=setup;c=bot.get_cog('Events');eid=await active_event(bot,w);await claim(bot,w,eid)
    with pytest.raises(ValueError,match='ticket owner must choose'):
        await Events.eventreview.callback(c,context(bot,w),'approve',reason='Review done')


async def test_old_database_migration_does_not_select_a_reward(tmp_path):
    store=Store(tmp_path/'old.db');await store.init()
    from fortune.events import SCHEMA
    await store._run(lambda c:c.executescript(SCHEMA))
    await store.execute("INSERT INTO event_claims(event_id,guild_id,user_id,channel_id,cutoff,promo,result) VALUES(1,2,3,4,?,'dm',?)",(AT.isoformat(),json.dumps({'reward':'nitro booster','tier':10})))
    bot=NS(store=store,add_view=lambda v:None)
    cog=Events(bot)
    await cog.cog_load()
    await cog.cog_load()  # Migration is safe to run again.
    row=await store.one('SELECT * FROM event_claims')
    assert row['selected_reward'] is None
    assert row['user_id']==3 and row['cutoff']==AT.isoformat()
    assert json.loads(row['result'])['reward']=='nitro booster'
