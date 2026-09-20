"""Event builder, persistent ticket claims, staff review, and proof forwarding."""
import asyncio
import io
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
import discord
from discord.ext import commands
from .branding import embed
from .event_rules import RULES, MANUAL_RULES, parse_rewards, calculate, reward_for, invite_window, utc_time
from .permissions import admin, require_admin
from .store import now
from .tickets import SafeView, TicketControls, reply

log = logging.getLogger(__name__)
PROOF_GUILD = 1545431325995958314
PROOF_CHANNEL = 1545431327850102878
SCHEMA = '''
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, guild_id INTEGER, owner_id INTEGER, channel_id INTEGER,
 message_id INTEGER, category_id INTEGER, started_at TEXT, ended_at TEXT,
 status TEXT NOT NULL, data TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_event ON events(guild_id) WHERE status='active';
CREATE TABLE IF NOT EXISTS event_claims (
 id INTEGER PRIMARY KEY, event_id INTEGER, guild_id INTEGER, user_id INTEGER,
 channel_id INTEGER UNIQUE, cutoff TEXT NOT NULL, promo TEXT, result TEXT,
 status TEXT NOT NULL DEFAULT 'pending', reviewer_id INTEGER, review_note TEXT,
 UNIQUE(event_id,user_id));
CREATE TABLE IF NOT EXISTS event_findings (
 id INTEGER PRIMARY KEY, event_id INTEGER, user_id INTEGER, code TEXT, quantity INTEGER,
 reason TEXT, actor_id INTEGER, created_at TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS event_proofs (
 id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, source_channel_id INTEGER,
 target_message_id INTEGER, title TEXT, created_at TEXT);
'''


def event_embed(data):
    e = embed(data['title'], data['description'], color=int(data.get('color','63d6ac'),16))
    if data.get('image'):
        e.set_image(url=data['image'])
    if data.get('footer'):
        e.set_footer(text=data['footer'])
    for field in data.get('fields', []):
        e.add_field(name=field['name'], value=field['value'], inline=False)
    return e


class Form(discord.ui.Modal):
    async def on_error(self, interaction, error):
        log.error('Event form failed', exc_info=error)
        await reply(interaction, str(error) if isinstance(error, ValueError) else 'Could not save this form. Please retry.')


class EmbedForm(Form, title='Edit event embed'):
    def __init__(self, builder):
        super().__init__(timeout=600)
        self.builder = builder
        for key,label,length,style in [('title','Title',200,discord.TextStyle.short),('description','Description',2500,discord.TextStyle.paragraph),('color','Hex color',7,discord.TextStyle.short),('image','Image URL (optional)',500,discord.TextStyle.short),('footer','Footer (optional)',200,discord.TextStyle.short)]:
            self.add_item(discord.ui.TextInput(label=label, custom_id=key, default=builder.data.get(key,''), max_length=length, style=style, required=key in ('title','description','color')))

    async def on_submit(self, interaction):
        values = {item.custom_id: str(item) for item in self.children}
        values['color'] = values['color'].lstrip('#')
        if not re.fullmatch('[0-9a-fA-F]{6}',values['color']):
            return await reply(interaction,'Use a six-digit hex color, such as #63d6ac.')
        if values['image'] and (urlparse(values['image']).scheme != 'https' or not urlparse(values['image']).netloc):
            return await reply(interaction,'Use an HTTPS image URL.')
        self.builder.data.update(values)
        await interaction.response.edit_message(embed=event_embed(self.builder.data), view=self.builder)


class FieldsForm(Form, title='Edit extra embed fields'):
    fields = discord.ui.TextInput(label='One field per line: name | value', style=discord.TextStyle.paragraph, required=False,max_length=1800)
    def __init__(self,builder):
        super().__init__()
        self.builder=builder
        self.fields.default='\n'.join(f'{f["name"]} | {f["value"]}' for f in builder.data.get('fields',[]))
    async def on_submit(self,interaction):
        fields=[]
        for line in str(self.fields).splitlines():
            name,sep,value=line.partition('|')
            if not sep or not name.strip() or not value.strip() or len(name.strip())>100 or len(value.strip())>500:
                return await reply(interaction,'Use name | value, with a name up to 100 and value up to 500 characters.')
            fields.append({'name':name.strip(),'value':value.strip()})
        if len(fields)>5:
            return await reply(interaction,'Use up to five extra fields.')
        self.builder.data['fields']=fields
        await interaction.response.edit_message(embed=event_embed(self.builder.data),view=self.builder)


class RewardForm(Form, title='Event rewards'):
    rewards = discord.ui.TextInput(label='One reward per line',style=discord.TextStyle.paragraph,max_length=2500,default='1 invite = $0.30\n6 invites = $1\n10 invites = Nitro Booster\n30 invites = Nitro Basic')
    def __init__(self,builder):
        super().__init__(timeout=600)
        self.builder=builder
    async def on_submit(self,interaction):
        try:
            self.builder.data['rewards']=parse_rewards(str(self.rewards))
            self.builder.data['rules']=True
            self.builder.preview()
        except ValueError as exc:
            return await reply(interaction,str(exc))
        self.builder.stage='rules'
        self.builder.clear_items()
        for value,label in [(True,'Use event rules'),(False,'No rules')]:
            b=discord.ui.Button(label=label,style=discord.ButtonStyle.primary)
            async def select(i, enabled=value):
                self.builder.data['rules']=enabled
                self.builder.stage='category'
                self.builder.clear_items()
                selector=discord.ui.ChannelSelect(channel_types=[discord.ChannelType.category],placeholder='Choose event ticket category')
                async def category(chosen):
                    self.builder.category_id=int(selector.values[0].id)
                    self.builder.clear_items()
                    publish=discord.ui.Button(label='Publish event',style=discord.ButtonStyle.success)
                    async def finish(final):
                        await final.response.defer(ephemeral=True)
                        await self.builder.cog.publish(final,self.builder)
                    publish.callback=finish
                    self.builder.add_item(publish)
                    self.builder.add_back()
                    await chosen.response.edit_message(content=f'Category: <#{self.builder.category_id}>. Ready to publish.',embeds=self.builder.preview(),view=self.builder)
                selector.callback=category
                self.builder.add_item(selector)
                self.builder.add_back()
                await i.response.edit_message(content='Choose the event ticket category.',embeds=self.builder.preview(),view=self.builder)
            b.callback=select
            self.builder.add_item(b)
        self.builder.add_back()
        await interaction.response.edit_message(content='Use the supplied event rules, or run without rule deductions?',view=self.builder)


class Builder(SafeView):
    def __init__(self,cog,user_id):
        super().__init__(timeout=900)
        self.cog,self.user_id=cog,user_id
        self.stage='embed'
        self.category_id=None
        self.data={'title':'Invite Event','description':'Invite members and claim your reward!','color':'63d6ac','image':'','footer':'FortuneManager','fields':[]}
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.user_id or not admin(interaction.user):
            await reply(interaction,'Only the administrator who started this setup can edit it.')
            return False
        return True
    def add_back(self):
        button=discord.ui.Button(label='Back to embed',style=discord.ButtonStyle.secondary)
        async def back(i):
            replacement=Builder(self.cog,self.user_id)
            replacement.data=self.data
            self.stop()
            await i.response.edit_message(content='Edit the embed, then choose rewards again.',embeds=[event_embed(self.data)],view=replacement)
        button.callback=back
        self.add_item(button)

    def preview(self):
        e=event_embed(self.data)
        e.add_field(name='Rewards',value='\n'.join(f'{r["threshold"]} invites = {r["label"]}' for r in self.data['rewards']),inline=False)
        e.add_field(name='How to claim',value='Open an event ticket, then use `.check`. Public `.invites` checks during rules events are allowed only in the configured Cmds channel; elsewhere they trigger RESET. Choose your reward from the options you qualify for; the 1-invite cash option is per invite. Staff review is required.',inline=False)
        embeds = [e]+([embed('Event Rules',RULES)] if self.data.get('rules') else [])
        if sum(len(item) for item in embeds)>5700:
            raise ValueError('The embed, fields, rewards, and rules are too long together. Shorten the description or extra fields.')
        return embeds
    @discord.ui.button(label='Edit embed',style=discord.ButtonStyle.primary)
    async def edit(self,interaction,button):
        await interaction.response.send_modal(EmbedForm(self))
    @discord.ui.button(label='Edit fields',style=discord.ButtonStyle.secondary)
    async def fields(self,interaction,button):
        await interaction.response.send_modal(FieldsForm(self))
    @discord.ui.button(label='Next: rewards',style=discord.ButtonStyle.success)
    async def rewards(self,interaction,button):
        await interaction.response.send_modal(RewardForm(self))


class EventPanel(SafeView):
    def __init__(self,cog):
        super().__init__(timeout=None)
        self.cog=cog
    @discord.ui.button(label='Open event ticket',style=discord.ButtonStyle.primary,custom_id='fm:event:ticket:v1')
    async def ticket(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        event=await self.cog.bot.store.one("SELECT * FROM events WHERE guild_id=? AND message_id=? AND status='active'",(interaction.guild_id,interaction.message.id))
        if not event:
            return await reply(interaction,'This event has ended.')
        try:
            channel=await self.cog.open_ticket(interaction.guild,interaction.user,event)
            await reply(interaction,f'Your event ticket: {channel.mention}. Run `.check` there.')
        except ValueError as exc:
            await reply(interaction,str(exc))


class PromoView(SafeView):
    def __init__(self,cog,claim,actor):
        super().__init__(timeout=120)
        self.cog,self.claim,self.actor=cog,claim,actor
    async def interaction_check(self,i):
        if i.user.id!=self.actor:
            await reply(i,'This check belongs to another member.')
            return False
        return True
    async def choose(self,i,promo):
        await i.response.defer(ephemeral=True)
        async with self.cog.bot.channel_locks[self.claim['channel_id']]:
            claim=await self.cog.bot.store.one('SELECT * FROM event_claims WHERE id=?',(self.claim['id'],))
            if claim['status']!='pending':
                return await reply(i,f'This claim is already {claim["status"]}.')
            if claim['promo'] and claim['promo']!=promo and not admin(i.user):
                return await reply(i,'Promotion type is locked after your first check. Ask an administrator to correct it.')
            await self.cog.bot.store.execute('UPDATE event_claims SET promo=? WHERE id=?',(promo,claim['id']))
            claim['promo']=promo
            await self.cog.present_rewards(i.guild,i.channel,claim)
        self.stop()
        await i.edit_original_response(content='Invite check complete. The ticket owner can choose a reward in this ticket.',view=None)
    @discord.ui.button(label='Server promo',style=discord.ButtonStyle.primary)
    async def server(self,i,b):
        await self.choose(i,'server')
    @discord.ui.button(label='DM promo',style=discord.ButtonStyle.secondary)
    async def dm(self,i,b):
        await self.choose(i,'dm')


class RewardView(SafeView):
    """Only the claimant selects a reward; eligibility is checked again on submit."""
    def __init__(self,cog,claim,rewards,eligible):
        super().__init__(timeout=180)
        self.cog,self.claim=cog,claim
        options=[]
        for reward in rewards:
            if reward['threshold']>eligible:
                continue
            value,_=reward_for(eligible,rewards,reward['threshold'])
            label=f'{reward["label"]} per invite → {value}' if reward['threshold']==1 and reward['amount'] is not None else reward['label']
            options.append(discord.SelectOption(label=label[:100],value=str(reward['threshold']),
                description=f'Requires {reward["threshold"]} eligible invites; you have {eligible}.',
                default=claim.get('selected_reward')==reward['threshold']))
        selector=discord.ui.Select(placeholder='Choose the reward you want to claim',options=options)
        async def select(interaction):
            await self.choose(interaction,int(selector.values[0]))
        selector.callback=select
        self.add_item(selector)

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.claim['user_id']:
            await reply(interaction,'Only the ticket owner can choose their reward.')
            return False
        return True

    async def choose(self,interaction,threshold):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with self.cog.bot.channel_locks[self.claim['channel_id']]:
            claim=await self.cog.bot.store.one('SELECT * FROM event_claims WHERE id=?',(self.claim['id'],))
            if not claim or claim['guild_id']!=interaction.guild_id or claim['channel_id']!=interaction.channel_id:
                return await reply(interaction,'This reward picker no longer belongs to this ticket. Run `.check` again.')
            if claim['status']!='pending':
                return await reply(interaction,f'This claim is already {claim["status"]}.')
            if not claim['promo']:
                return await reply(interaction,'Run `.check` and choose a promotion type first.')
            candidate=dict(claim,selected_reward=threshold)
            _,_,result=await self.cog.evaluate(interaction.guild,candidate)
            if result['blocked'] or result['tier']!=threshold:
                return await reply(interaction,'You do not currently qualify for that reward. Run `.check` to refresh your eligible choices.')
            await self.cog.bot.store.execute('UPDATE event_claims SET selected_reward=?,result=NULL WHERE id=?',(threshold,claim['id']))
            await self.cog.report(interaction.guild,interaction.channel,candidate)
        self.stop()
        await interaction.message.edit(content=f'Reward chosen: **{result["reward"]}**. Run `.check` to change your choice before staff approval.',view=None)
        await reply(interaction,'Your reward choice is saved and waiting for staff review.')


class Events(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
    async def cog_load(self):
        await self.bot.store._run(lambda c:c.executescript(SCHEMA))
        # Additive migration: existing claims have no selected reward until their owner chooses.
        def migrate(c):
            columns={r['name'] for r in c.execute('PRAGMA table_info(event_claims)')}
            if 'selected_reward' not in columns:
                c.execute('ALTER TABLE event_claims ADD COLUMN selected_reward INTEGER')
        await self.bot.store._run(migrate)
        self.bot.add_view(EventPanel(self))
    async def active(self,guild_id):
        return await self.bot.store.one("SELECT * FROM events WHERE guild_id=? AND status='active'",(guild_id,))

    @commands.group(invoke_without_command=True)
    @require_admin()
    async def event(self,ctx):
        """Build an event: edit embed → rewards → rules → category → publish. event end closes it."""
        if await self.active(ctx.guild.id):
            raise ValueError(f'An event is already active. Use `{ctx.clean_prefix}event end` before starting another.')
        view=Builder(self,ctx.author.id)
        await ctx.send(content='Edit the event embed, then select Next: rewards.',embed=event_embed(view.data),view=view)

    async def publish(self,interaction,builder):
        guild=interaction.guild
        try:
            preview=builder.preview()
        except ValueError as exc:
            return await reply(interaction,str(exc))
        category=guild.get_channel(builder.category_id)
        if not isinstance(category,discord.CategoryChannel):
            return await reply(interaction,'Choose a category in this server.')
        perms=category.permissions_for(guild.me)
        if not all((perms.view_channel,perms.manage_channels,perms.send_messages,perms.embed_links)):
            return await reply(interaction,'I need View Channel, Manage Channels, Send Messages, and Embed Links in that category.')
        async with self.bot.guild_locks[guild.id]:
            if await self.active(guild.id):
                return await reply(interaction,'An event is already active.')
            cmds=discord.utils.get(guild.text_channels,name='cmds')
            data=dict(builder.data, owner_ids=sorted({guild.owner_id,interaction.user.id}),check_channel_id=cmds.id if cmds else None)
            eid=await self.bot.store.execute('INSERT INTO events(guild_id,owner_id,channel_id,category_id,started_at,status,data) VALUES(?,?,?,?,?,?,?)',
                (guild.id,interaction.user.id,interaction.channel_id,category.id,now(),'draft',json.dumps(data)))
            message=None
            try:
                message=await interaction.channel.send(embeds=preview,view=EventPanel(self))
                await self.bot.store.execute("UPDATE events SET status='active',message_id=? WHERE id=?",(message.id,eid))
            except Exception:
                if message:
                    await message.delete()
                await self.bot.store.execute("UPDATE events SET status='failed' WHERE id=?",(eid,))
                raise
        builder.stop()
        await interaction.message.edit(content=f'Event #{eid} published.',view=None)
        await reply(interaction,f'Event #{eid} is live.')

    @event.command(name='end')
    @require_admin()
    async def end_event(self,ctx):
        """End invite accrual; existing claims remain reviewable."""
        async with self.bot.guild_locks[ctx.guild.id]:
            event=await self.active(ctx.guild.id)
            if not event:
                raise ValueError('There is no active event.')
            await self.bot.store.execute("UPDATE events SET status='ended',ended_at=? WHERE id=?",(now(),event['id']))
        await ctx.send(embed=embed('Event ended',f'Event #{event["id"]} is closed. Existing tickets can still be checked and reviewed.'))

    @event.command(name='owners')
    @require_admin()
    async def event_owners(self,ctx,members: commands.Greedy[discord.Member]):
        """event owners @owner1 @owner2 — people notified by checks (server owner is always included)."""
        if not members or len(members)>10:
            raise ValueError('Mention between one and ten event owners.')
        async with self.bot.guild_locks[ctx.guild.id]:
            event=await self.active(ctx.guild.id)
            if not event:
                raise ValueError('No active event.')
            data=json.loads(event['data'])
            data['owner_ids']=sorted({ctx.guild.owner_id,*[m.id for m in members]})
            await self.bot.store.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(data),event['id']))
        await ctx.send(embed=embed('Event owners saved','They will be pinged when a ticket is checked.'))

    @event.command(name='cmds')
    @require_admin()
    async def check_channel(self,ctx,channel:discord.TextChannel):
        """event cmds #channel — allowed public invite-check channel for this event."""
        async with self.bot.guild_locks[ctx.guild.id]:
            event=await self.active(ctx.guild.id)
            if not event:
                raise ValueError('No active event.')
            data=json.loads(event['data'])
            data['check_channel_id']=channel.id
            await self.bot.store.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(data),event['id']))
        await ctx.send(embed=embed('Invite check channel saved',channel.mention))

    async def record_chat_check(self,ctx):
        event=await self.active(ctx.guild.id)
        if not event:
            return False
        data=json.loads(event['data'])
        if not data['rules'] or ctx.channel.id==data.get('check_channel_id') or admin(ctx.author):
            return False
        ticket=await self.bot.store.one('SELECT 1 FROM event_claims WHERE event_id=? AND user_id=? AND channel_id=?',
            (event['id'],ctx.author.id,ctx.channel.id))
        if ticket:
            return False
        def record(c):
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT 1 FROM event_findings WHERE event_id=? AND user_id=? AND code='chat_check' AND active=1",(event['id'],ctx.author.id)).fetchone()
            if not row:
                c.execute('INSERT INTO event_findings(event_id,user_id,code,quantity,reason,actor_id,created_at) VALUES(?,?,?,?,?,?,?)',
                    (event['id'],ctx.author.id,'chat_check',1,f'Invite command in channel {ctx.channel.id}',ctx.author.id,now()))
        await self.bot.store._run(record)
        return True

    async def open_ticket(self,guild,member,event):
        async with self.bot.ticket_locks[(guild.id,member.id)]:
            # Recheck active state after waiting for the lock.
            latest=await self.bot.store.one('SELECT status FROM events WHERE id=?',(event['id'],))
            if not latest or latest['status']!='active':
                raise ValueError('This event has ended.')
            previous=await self.bot.store.one('SELECT * FROM event_claims WHERE event_id=? AND user_id=?',(event['id'],member.id))
            if previous:
                channel=guild.get_channel(previous['channel_id'])
                if channel:
                    return channel
                if previous['status']!='pending':
                    raise ValueError('This claim was already reviewed; staff must reopen it before recovering the ticket.')
                # Recover a interrupted Discord channel creation by its exact claim marker.
                channel=next((ch for ch in guild.text_channels if ch.topic and ch.topic.startswith(f"fm-event:{previous['id']} |")),None)
                if channel:
                    await self.bot.store.execute('UPDATE event_claims SET channel_id=? WHERE id=?',(channel.id,previous['id']))
                    return channel
            category=guild.get_channel(event['category_id'])
            if not isinstance(category,discord.CategoryChannel):
                raise ValueError('The configured event category is missing.')
            cutoff=previous['cutoff'] if previous else now()
            cid=previous['id'] if previous else await self.bot.store.execute('INSERT INTO event_claims(event_id,guild_id,user_id,cutoff) VALUES(?,?,?,?)',(event['id'],guild.id,member.id,cutoff))
            overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),member:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True),guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,embed_links=True,attach_files=True,manage_channels=True)}
            for uid in json.loads(event['data'])['owner_ids']:
                owner=guild.get_member(uid)
                if owner:
                    overwrites[owner]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
            channel=None
            try:
                channel=await guild.create_text_channel(f'event-{cid}-{member.id}',category=category,overwrites=overwrites,topic=f'fm-event:{cid} | Owner: {member.id}',reason='Event reward ticket')
                await self.bot.store.execute('UPDATE event_claims SET channel_id=? WHERE id=?',(channel.id,cid))
                ticket_cog=self.bot.get_cog('Tickets')
                if ticket_cog:
                    await self.bot.store.execute("INSERT OR IGNORE INTO tickets(guild_id,owner_id,channel_id,panel_id,option_id,status,created_at) VALUES(?,?,?,?,?,'open',?)",
                        (guild.id,member.id,channel.id,f'event:{event["id"]}','event',cutoff))
                config, _ = await self.bot.store.config(guild.id)
                prefix = config['prefix']
                await channel.send(view=TicketControls(ticket_cog) if ticket_cog else None,embed=embed('Event reward ticket',f'Run `{prefix}check` and select Server promo or DM promo, then choose your reward.\nOnly invites gained before this ticket was opened can count.\nUse `{prefix}proof Your title` with an image attachment for proof. Staff approve rewards after review.'))
            except Exception:
                # Keep a created channel's identity and cutoff for recovery; never create duplicate claims.
                if channel is None and not previous:
                    await self.bot.store.execute('DELETE FROM event_claims WHERE id=?',(cid,))
                raise
            return channel

    @commands.command()
    @commands.guild_only()
    async def eventticket(self,ctx):
        """Open your private claim ticket for the current event."""
        event=await self.active(ctx.guild.id)
        if not event:
            raise ValueError('No active event.')
        channel=await self.open_ticket(ctx.guild,ctx.author,event)
        await ctx.send(embed=embed('Event ticket',channel.mention))

    async def claim_for(self,ctx):
        claim=await self.bot.store.one('SELECT * FROM event_claims WHERE guild_id=? AND channel_id=?',(ctx.guild.id,ctx.channel.id))
        if not claim:
            # Integrate existing FortuneManager tickets, never trust a channel name/topic for ownership.
            ticket=await self.bot.store.one("SELECT * FROM tickets WHERE guild_id=? AND channel_id=? AND status='open'",(ctx.guild.id,ctx.channel.id))
            event=await self.active(ctx.guild.id)
            if not ticket or not event or ctx.channel.category_id!=event['category_id']:
                raise ValueError('Use `.check` inside your event ticket. Open one with `.eventticket`.')
            if ticket['owner_id']!=ctx.author.id and not admin(ctx.author):
                raise ValueError('Only the ticket owner or an administrator can check this ticket.')
            if ticket['created_at'] < event['started_at']:
                raise ValueError('Open a new ticket for this event.')
            previous=await self.bot.store.one('SELECT * FROM event_claims WHERE event_id=? AND user_id=?',(event['id'],ticket['owner_id']))
            if previous:
                raise ValueError('This member already has a claim in another ticket.')
            cid=await self.bot.store.execute('INSERT INTO event_claims(event_id,guild_id,user_id,channel_id,cutoff) VALUES(?,?,?,?,?)',
                (event['id'],ctx.guild.id,ticket['owner_id'],ctx.channel.id,ticket['created_at']))
            claim=await self.bot.store.one('SELECT * FROM event_claims WHERE id=?',(cid,))
        if claim['user_id']!=ctx.author.id and not admin(ctx.author):
            raise ValueError('Only the ticket owner or an administrator can check this ticket.')
        return claim

    @commands.command(name='check')
    @commands.guild_only()
    @commands.cooldown(1,30,commands.BucketType.channel)
    async def check_invites(self,ctx):
        """Check event invites, select server/DM promotion, then let the ticket owner choose a reward."""
        async with self.bot.channel_locks[ctx.channel.id]:
            claim=await self.claim_for(ctx)
        if claim['status']!='pending':
            return await ctx.send(embed=embed('Claim already reviewed',f'Status: **{claim["status"]}**. Staff can use `{ctx.clean_prefix}eventreview reopen reason` if needed.'))
        await ctx.send(embed=embed('Promotion type','Are these Server promo or DM promo invites?'),view=PromoView(self,claim,ctx.author.id))

    async def evaluate(self,guild,claim):
        event=await self.bot.store.one('SELECT * FROM events WHERE id=?',(claim['event_id'],))
        data=json.loads(event['data'])
        cutoff=min(utc_time(claim['cutoff']), utc_time(event['ended_at'] or claim['cutoff'])).isoformat()
        verified=await self.bot.store.rows("SELECT * FROM invite_members WHERE guild_id=? AND inviter_id=? AND source='invite'",(guild.id,claim['user_id']))
        rows,window=invite_window(verified,event['started_at'],cutoff)
        unavailable=[]
        at=discord.utils.utcnow()
        # No-rules events need verified attribution and the time window only;
        # a profile lookup failure must not disqualify an otherwise valid claim.
        for row in rows if data['rules'] else []:
            moves=await self.bot.store.rows('SELECT kind FROM member_movements WHERE guild_id=? AND member_id=? AND at>=?',(guild.id,row['member_id'],row['joined_at']))
            row['left']=any(m['kind']=='leave' for m in moves)
            row['rejoined']=any(m['kind']=='rejoin' for m in moves)
            try:
                member=await guild.fetch_member(row['member_id'])
            except discord.NotFound:
                row['left']=True
                continue
            except discord.HTTPException:
                unavailable.append(row['member_id'])
                continue
            row['no_avatar']=member.avatar is None and getattr(member,'guild_avatar',None) is None
            row['onboarding_missing']='COMMUNITY' in guild.features and 'GUILD_ONBOARDING' in guild.features and not member.flags.completed_onboarding
        findings=await self.bot.store.rows('SELECT * FROM event_findings WHERE event_id=? AND user_id=? AND active=1',(event['id'],claim['user_id']))
        proof=await self.bot.store.one('SELECT created_at FROM event_proofs WHERE guild_id=? AND user_id=? AND source_channel_id=? ORDER BY created_at LIMIT 1',(guild.id,claim['user_id'],claim['channel_id']))
        deadline=utc_time(claim['cutoff'])+timedelta(days=5)
        proof_overdue=at>deadline and (not proof or utc_time(proof['created_at'])>deadline)
        result=calculate(rows,data['rewards'],rules=data['rules'],promo=claim['promo'] or 'dm',at=at,findings=findings,proof_overdue=proof_overdue,selected_threshold=claim.get('selected_reward'))
        result.update(unavailable=unavailable,proof=bool(proof),rules=data['rules'],cutoff=cutoff,started_at=event['started_at'],window=window)
        if unavailable:
            result['blocked'].append('Verification incomplete: Discord member lookup failed; retry later.')
            result['reward']='Verification pending'
        return event,data,result

    async def present_rewards(self,guild,channel,claim):
        evaluation=await self.evaluate(guild,claim)
        _,data,result=evaluation
        available=[r for r in data['rewards'] if r['threshold']<=result['eligible']]
        if result['blocked'] or not available:
            await self.report(guild,channel,claim,evaluation=evaluation)
            if result['unavailable']:
                title='Invite verification pending'
                reason='Discord could not verify some member profiles. Retry `.check` shortly. Your verified invites have not been removed.'
            elif result['blocked']:
                title='Reward claim blocked by event rules'
                reason='\n'.join(result['blocked'])+'\nStaff can review the findings above. An incorrect manual finding can be removed with `.eventunflag ID reason`.'
            elif result['raw']==0:
                title='No verified invites in this event window'
                reason='Only invites earned after this event started and before this ticket opened count. See the invite breakdown above.'
            else:
                threshold=min(r['threshold'] for r in data['rewards'])
                title='Reward threshold not reached'
                reason=f'You have {result["eligible"]} eligible invites after deductions; the first reward requires {threshold}. See the deductions and rounding above.'
            await channel.send(embed=embed(title,reason[:3500]))
            return
        await channel.send(embed=embed('Choose your reward',
            f'<@{claim["user_id"]}>, you have **{result["eligible"]} eligible invites** after deductions.\n'
            f'Verified during this event, before ticket: **{result["raw"]}**. '
            f'Deducted: **{sum(n for _,n in result["deductions"])}**; rounded down: **{result["rounded"]}**.\n'
            'Choose the reward you want from the dropdown. You may choose any eligible option.\nYour choice is saved for staff review; no reward is selected automatically.'),
            view=RewardView(self,claim,data['rewards'],result['eligible']))

    async def report(self,guild,channel,claim,*,evaluation=None):
        event,data,result=evaluation or await self.evaluate(guild,claim)
        await self.bot.store.execute('UPDATE event_claims SET result=? WHERE id=?',(json.dumps(result),claim['id']))
        e=embed(f'Event #{event["id"]} · Claim #{claim["id"]}',f'Member: <@{claim["user_id"]}>\nPromo: **{claim["promo"]}**\nVerified joins before ticket: **{result["raw"]}**\nEligible invites: **{result["eligible"]}**\nCalculated reward: **{result["reward"]}**\nStatus: **PENDING STAFF REVIEW**')
        window=result['window']
        e.add_field(name='Invite count breakdown',value=(
            f'All-time verified: **{window["total_verified"]}**\n'
            f'Excluded before event: **{window["before_event"]}**\n'
            f'Excluded after ticket/event end: **{window["after_cutoff"]}**\n'
            f'Invalid join timestamp: **{window["invalid_timestamp"]}**\n'
            f'Event start (UTC): {utc_time(event["started_at"]).isoformat()}\n'
            f'Invite cutoff (UTC): {result["cutoff"]}'),inline=False)
        chosen=next((r for r in data['rewards'] if r['threshold']==claim.get('selected_reward')),None)
        e.add_field(name='User-selected reward',value=f"{chosen['threshold']} invites = {chosen['label']}" if chosen else 'Not selected — ticket owner must choose via `.check`.',inline=False)
        deductions='\n'.join(f'−{amount}: {reason}' for reason,amount in result['deductions']) or 'None'
        e.add_field(name='Deductions',value=deductions[:1000],inline=False)
        e.add_field(name='Even-number rule',value=f'{result["rounded"]} invite rounded down.' if data['rules'] else 'Disabled for this no-rules event.',inline=False)
        e.add_field(name='Findings',value=('\n'.join(result['blocked']) or 'No automatic disqualification found.')[:1000],inline=False)
        e.add_field(name='Staff review required',value='Verify bio/status, promotion evidence, invalid invites, alts/J4J, conduct, and the “No Limit” rule. Use `.eventrule` to record a finding and `.eventreview approve reason` after checking. No payment or Nitro is sent automatically.',inline=False)
        e.add_field(name='Proof',value='Recorded' if result['proof'] else 'Not yet recorded — attach an image to `.proof Title`.',inline=False)
        users=[discord.Object(id=uid) for uid in data['owner_ids']]
        rename_failed=False
        try:
            await channel.edit(name=f'event-{claim["id"]}-{result["eligible"]}inv-review',reason='Event invite check')
        except discord.HTTPException:
            rename_failed=True
        if rename_failed:
            e.add_field(name='Channel name',value='Could not rename this ticket. Check Manage Channels permission.',inline=False)
        await channel.send(content=' '.join(f'<@{u.id}>' for u in users),embed=e,allowed_mentions=discord.AllowedMentions(users=users))

    @commands.command()
    @require_admin()
    async def eventrule(self,ctx,code: str='list',quantity: int=1,*,reason: str=''):
        """Inside a claim ticket: eventrule <code> <quantity> <evidence/reason>. eventrule list lists codes."""
        if code=='list':
            return await ctx.send(embed=embed('Rule codes',', '.join(f'`{key}`' for key in MANUAL_RULES)+f'\nRecord: `{ctx.clean_prefix}eventrule invalid 1 evidence`\nRemove an incorrect finding: `{ctx.clean_prefix}eventunflag ID reason`'))
        if code not in MANUAL_RULES or not 1<=quantity<=10000 or not reason.strip():
            raise ValueError(f'Use `{ctx.clean_prefix}eventrule <code> <quantity 1–10000> <evidence/reason>`. See `{ctx.clean_prefix}eventrule list`.')
        async with self.bot.channel_locks[ctx.channel.id]:
            claim=await self.claim_for(ctx)
            if claim['status']!='pending':
                raise ValueError('Reopen the claim before changing its findings.')
            fid=await self.bot.store.execute('INSERT INTO event_findings(event_id,user_id,code,quantity,reason,actor_id,created_at) VALUES(?,?,?,?,?,?,?)',
                (claim['event_id'],claim['user_id'],code,quantity,reason[:1500],ctx.author.id,now()))
        await self.bot.store.audit(ctx.guild.id,ctx.author.id,'event.finding',f'{fid}: {code}: {reason}')
        await ctx.send(embed=embed('Finding recorded',f'Finding #{fid}: {code} × {quantity}. Re-run `{ctx.clean_prefix}check` for the updated calculation. Ban findings require a separate staff moderation decision.'))

    @commands.command()
    @require_admin()
    async def eventunflag(self,ctx,finding_id:int,*,reason:str):
        """Remove an incorrect finding in this ticket, retaining its audit record."""
        async with self.bot.channel_locks[ctx.channel.id]:
            claim=await self.claim_for(ctx)
            if claim['status']!='pending':
                raise ValueError('Reopen the claim before changing findings.')
            row=await self.bot.store.one('SELECT * FROM event_findings WHERE id=? AND event_id=? AND user_id=?',(finding_id,claim['event_id'],claim['user_id']))
            if not row:
                raise ValueError('No matching finding in this ticket.')
            await self.bot.store.execute('UPDATE event_findings SET active=0 WHERE id=?',(finding_id,))
        await self.bot.store.audit(ctx.guild.id,ctx.author.id,'event.unflag',f'{finding_id}: {reason}')
        await ctx.send(embed=embed('Finding removed',f'#{finding_id}. Run `{ctx.clean_prefix}check` again.'))

    @commands.command()
    @require_admin()
    async def eventreview(self,ctx,decision:str,*,reason:str):
        """eventreview approve|reject|reset|dq|reopen <reason>; approval requires evidence review."""
        statuses={'approve':'approved','reject':'rejected','reset':'reset','dq':'disqualified','reopen':'pending'}
        if decision not in statuses or not reason.strip():
            raise ValueError('Use approve, reject, reset, dq, or reopen followed by your review reason.')
        async with self.bot.channel_locks[ctx.channel.id]:
            claim=await self.claim_for(ctx)
            if claim['status']!='pending' and decision!='reopen':
                raise ValueError('This claim is already reviewed. Reopen it before changing the decision.')
            if decision=='approve':
                if not claim['promo']:
                    raise ValueError('Run `.check` first.')
                if claim.get('selected_reward') is None:
                    raise ValueError(f'The ticket owner must choose a reward using `{ctx.clean_prefix}check` before approval.')
                _,data,result=await self.evaluate(ctx.guild,claim)
                if result['blocked'] or result['tier']!=claim['selected_reward'] or result['reward']=='No reward':
                    raise ValueError('This claim has blocking findings or no reward. Resolve findings before approving.')
                if data['rules'] and not result['proof']:
                    raise ValueError('Screenshot proof is required for this rules event.')
                await self.bot.store.execute('UPDATE event_claims SET result=? WHERE id=?',(json.dumps(result),claim['id']))
            await self.bot.store.execute('UPDATE event_claims SET status=?,reviewer_id=?,review_note=? WHERE id=?',(statuses[decision],ctx.author.id,reason[:1500],claim['id']))
        await self.bot.store.audit(ctx.guild.id,ctx.author.id,'event.review',f'{claim["id"]}: {decision}: {reason}')
        await ctx.send(embed=embed('Claim reviewed',f'Claim #{claim["id"]}: **{statuses[decision]}**\n{reason[:1500]}\nRewards are fulfilled manually by the event owners.'))

    @commands.command()
    @commands.guild_only()
    @commands.cooldown(1,30,commands.BucketType.user)
    async def proof(self,ctx,*,title:str):
        """proof <title> + an attached PNG/JPEG/GIF/WebP image. Sends a new copy to the proof channel."""
        if ctx.guild.id!=PROOF_GUILD:
            raise ValueError('Proof forwarding is configured only for the server that owns the supplied proof channel.')
        if not 1<=len(title.strip())<=200:
            raise ValueError('Use a title of 1–200 characters.')
        attachments=ctx.message.attachments
        if not attachments:
            raise ValueError('Attach an image to the same message: `.proof Your title`.')
        attachment=attachments[0]
        if attachment.size>8*1024*1024:
            raise ValueError('Use an image smaller than 8 MiB.')
        content=await attachment.read()
        ext = ('png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if content.startswith(b'\xff\xd8\xff') else 'gif' if content.startswith((b'GIF87a',b'GIF89a')) else 'webp' if content.startswith(b'RIFF') and content[8:12]==b'WEBP' else None)
        if ext is None:
            raise ValueError('Attach a PNG, JPEG, GIF, or WebP image; renaming another file is not sufficient.')
        channel=self.bot.get_channel(PROOF_CHANNEL) or await self.bot.fetch_channel(PROOF_CHANNEL)
        if not isinstance(channel,discord.TextChannel) or channel.guild.id!=ctx.guild.id:
            raise ValueError('The proof destination is unavailable in this server.')
        if not ctx.channel.permissions_for(ctx.author).view_channel:
            raise ValueError('You cannot submit from this channel.')
        e=embed(title.strip(),f'Submitted by {ctx.author.mention} · `{ctx.author.id}`')
        e.set_image(url=f'attachment://proof.{ext}')
        message=await channel.send(embed=e,file=discord.File(io.BytesIO(content),filename=f'proof.{ext}'),allowed_mentions=discord.AllowedMentions.none())
        await self.bot.store.execute('INSERT INTO event_proofs(guild_id,user_id,source_channel_id,target_message_id,title,created_at) VALUES(?,?,?,?,?,?)',
            (ctx.guild.id,ctx.author.id,ctx.channel.id,message.id,title.strip(),now()))
        await ctx.send(embed=embed('Proof submitted',f'[View your proof]({message.jump_url})'))
