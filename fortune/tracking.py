"""Conservative invite attribution and persistent message counters."""
import asyncio
import logging
from collections import defaultdict
import discord
from discord.ext import commands
from .branding import embed
from .store import now

log = logging.getLogger(__name__)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS invite_members (
 guild_id INTEGER, member_id INTEGER, inviter_id INTEGER, code TEXT,
 source TEXT NOT NULL, joined_at TEXT NOT NULL, account_created_at TEXT NOT NULL,
 PRIMARY KEY(guild_id,member_id));
CREATE INDEX IF NOT EXISTS inviter_lookup ON invite_members(guild_id,inviter_id,joined_at);
CREATE TABLE IF NOT EXISTS member_movements (
 id INTEGER PRIMARY KEY, guild_id INTEGER, member_id INTEGER, kind TEXT, at TEXT);
CREATE INDEX IF NOT EXISTS movement_lookup ON member_movements(guild_id,member_id,at);
CREATE TABLE IF NOT EXISTS message_counts (
 guild_id INTEGER, user_id INTEGER, total INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS tracking_status (guild_id INTEGER PRIMARY KEY, baseline_at TEXT, error TEXT);
'''


def attribute(before, after, vanity_before, vanity_after):
    """A batch delta cannot identify which member used which code; leave unknown."""
    if before is None:
        return None, None, 'unknown'
    changes = [(code, n-before.get(code, (0, None))[0], owner)
               for code, (n, owner) in after.items() if n > before.get(code, (0, None))[0]]
    vanity_delta = max(0, (vanity_after or 0) - (vanity_before or 0))
    if vanity_delta == 1 and not changes:
        return None, None, 'vanity'
    if len(changes) == 1 and changes[0][1] == 1 and vanity_delta == 0:
        code, _, owner = changes[0]
        return (owner, code, 'invite') if owner else (None, code, 'unknown')
    return None, None, 'unknown'


class Tracking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}
        self.vanity = {}
        self.locks = defaultdict(asyncio.Lock)
        self.connected = False

    async def cog_load(self):
        await self.bot.store._run(lambda c: c.executescript(SCHEMA))

    async def snapshot(self, guild):
        invites = await guild.invites()
        vanity = await guild.vanity_invite() if 'VANITY_URL' in guild.features else None
        return {i.code: (i.uses or 0, i.inviter.id if i.inviter else None) for i in invites}, vanity.uses if vanity else 0

    async def baseline(self, guild):
        async with self.locks[guild.id]:
            try:
                self.cache[guild.id], self.vanity[guild.id] = await self.snapshot(guild)
                error = None
            except discord.HTTPException:
                self.cache.pop(guild.id, None)
                error = 'Cannot read invites. Grant Manage Server; attribution unavailable.'
                log.warning('Invite baseline unavailable for guild %s', guild.id)
            await self.bot.store.execute(
                'INSERT INTO tracking_status VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET baseline_at=excluded.baseline_at,error=excluded.error',
                (guild.id, now(), error))

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self.baseline(guild)
        self.connected = True

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.connected = False
        self.cache.clear()
        self.vanity.clear()

    @commands.Cog.listener()
    async def on_resumed(self):
        # A disconnected interval cannot safely attribute joins from old counters.
        await self.on_ready()

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self.baseline(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        async with self.locks[invite.guild.id]:
            if invite.guild.id in self.cache:
                self.cache[invite.guild.id][invite.code] = (invite.uses or 0, invite.inviter.id if invite.inviter else None)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        joined = (member.joined_at or discord.utils.utcnow()).isoformat()
        async with self.locks[guild.id]:
            try:
                after, vanity = await self.snapshot(guild)
                owner, code, source = attribute(self.cache.get(guild.id), after, self.vanity.get(guild.id), vanity)
                self.cache[guild.id], self.vanity[guild.id] = after, vanity
            except discord.HTTPException:
                self.cache.pop(guild.id, None)
                owner, code, source = None, None, 'unknown'
            if member.bot:
                return
            if owner == member.id:
                owner, source = None, 'unknown'
            def record(c):
                c.execute('BEGIN IMMEDIATE')
                previous = c.execute('SELECT 1 FROM invite_members WHERE guild_id=? AND member_id=?', (guild.id,member.id)).fetchone()
                c.execute('INSERT OR IGNORE INTO invite_members VALUES(?,?,?,?,?,?,?)',
                          (guild.id, member.id, owner, code, source, joined, member.created_at.isoformat()))
                # A repeated gateway delivery must not manufacture a rejoin penalty.
                last = c.execute('SELECT kind FROM member_movements WHERE guild_id=? AND member_id=? ORDER BY id DESC LIMIT 1', (guild.id,member.id)).fetchone()
                if not last or last['kind'] == 'leave':
                    c.execute('INSERT INTO member_movements(guild_id,member_id,kind,at) VALUES(?,?,?,?)',
                              (guild.id,member.id,'rejoin' if previous else 'join',joined))
            await self.bot.store._run(record)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.bot:
            return
        def record(c):
            c.execute('INSERT OR IGNORE INTO invite_members VALUES(?,?,?,?,?,?,?)',
                      (member.guild.id, member.id, None, None, 'preexisting',
                       (member.joined_at or discord.utils.utcnow()).isoformat(), member.created_at.isoformat()))
            last = c.execute('SELECT kind FROM member_movements WHERE guild_id=? AND member_id=? ORDER BY id DESC LIMIT 1', (member.guild.id,member.id)).fetchone()
            if not last or last['kind'] != 'leave':
                c.execute('INSERT INTO member_movements(guild_id,member_id,kind,at) VALUES(?,?,?,?)', (member.guild.id,member.id,'leave',now()))
        await self.bot.store._run(record)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild and not message.author.bot and not message.webhook_id:
            await self.bot.store.execute(
                'INSERT INTO message_counts VALUES(?,?,1) ON CONFLICT(guild_id,user_id) DO UPDATE SET total=total+1',
                (message.guild.id,message.author.id))

    @commands.command(aliases=['inv'])
    @commands.guild_only()
    async def invites(self, ctx, member: discord.Member = None):
        """Show verified, retained, left, and rejoined invites since tracking started."""
        events=self.bot.get_cog('Events')
        if events and await events.record_chat_check(ctx):
            return await ctx.send(embed=embed('Event rule: RESET', 'Invite checking outside Cmds or your event ticket violates this event’s rules. A RESET finding was recorded for staff review.'))
        member = member or ctx.author
        row = await self.bot.store.one('''SELECT COUNT(*) AS total,
          SUM(EXISTS(SELECT 1 FROM member_movements m WHERE m.guild_id=i.guild_id AND m.member_id=i.member_id AND kind='leave')) AS left,
          SUM(EXISTS(SELECT 1 FROM member_movements m WHERE m.guild_id=i.guild_id AND m.member_id=i.member_id AND kind='rejoin')) AS rejoined
          FROM invite_members i WHERE guild_id=? AND inviter_id=? AND source='invite' ''', (ctx.guild.id,member.id))
        # Public checking is recorded for staff, who can distinguish Cmds from prohibited chat.
        await ctx.send(embed=embed(f'Invites · {member.display_name}',
            f'Verified joins: **{row["total"]}**\nEver left: **{row["left"] or 0}**\nRejoined: **{row["rejoined"] or 0}**\nEvent eligibility is calculated inside your event ticket with `.check`.\nUnattributed and vanity joins are excluded.'))

    @commands.command(aliases=['msgs'])
    @commands.guild_only()
    async def messages(self, ctx, member: discord.Member = None):
        """Show a member's message total since tracking started."""
        member = member or ctx.author
        row = await self.bot.store.one('SELECT total FROM message_counts WHERE guild_id=? AND user_id=?', (ctx.guild.id,member.id))
        await ctx.send(embed=embed(f'Messages · {member.display_name}', f'**{row["total"] if row else 0:,}** messages'))

    @commands.command(aliases=['lb'])
    @commands.guild_only()
    async def leaderboard(self, ctx, kind: str = 'invites'):
        """leaderboard invites | messages — top 20 server members."""
        if kind in ('messages','msgs'):
            rows = await self.bot.store.rows('SELECT user_id,total FROM message_counts WHERE guild_id=? ORDER BY total DESC,user_id LIMIT 20', (ctx.guild.id,))
        elif kind in ('invites','inv'):
            rows = await self.bot.store.rows("SELECT inviter_id AS user_id,COUNT(*) AS total FROM invite_members WHERE guild_id=? AND source='invite' AND inviter_id IS NOT NULL GROUP BY inviter_id ORDER BY total DESC,inviter_id LIMIT 20", (ctx.guild.id,))
        else:
            raise commands.BadArgument('Choose invites or messages.')
        await ctx.send(embed=embed(f'{kind.title()} leaderboard', '\n'.join(f'**{i}.** <@{r["user_id"]}> — **{r["total"]:,}**' for i,r in enumerate(rows,1)) or 'No activity recorded yet.'))

    @commands.command()
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def trackingstatus(self, ctx):
        """Show tracking health; joins while offline or ambiguous are never fabricated."""
        state = await self.bot.store.one('SELECT * FROM tracking_status WHERE guild_id=?', (ctx.guild.id,))
        rows = await self.bot.store.rows('SELECT source,COUNT(*) AS n FROM invite_members WHERE guild_id=? GROUP BY source', (ctx.guild.id,))
        await ctx.send(embed=embed('Tracking status', f'Baseline: {state["baseline_at"] if state else "not ready"}\n{state["error"] or "Invite snapshot ready" if state else "Waiting for connection"}\n'+'\n'.join(f'{r["source"]}: {r["n"]}' for r in rows)))
