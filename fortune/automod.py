"""One message pipeline for automod, with explicit limits and consistent actions."""

import asyncio
from collections import defaultdict, deque
import copy
import json
import logging
import re
import time
import unicodedata
from urllib.parse import urlsplit
from datetime import timedelta
import discord
from discord.ext import commands
from .branding import embed

log = logging.getLogger(__name__)
URL = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.I)
INVITE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[\w-]+",
    re.I,
)
CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
ACTIONS = ("delete", "warn", "timeout", "kick", "ban")
RULE_NAMES = (
    "spam",
    "duplicates",
    "repetition",
    "mentions",
    "everyone",
    "invites",
    "links",
    "caps",
    "emoji",
    "words",
    "domains",
    "attachments",
)


def normal(text):
    return " ".join(ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text)).casefold().split())


def contains_word(content, words):
    text = normal(content)
    # Underscores are Markdown delimiters as well as username characters.
    return any(re.search(r"(?<![^\W_])" + re.escape(word) + r"(?![^\W_])", text) for word in words)


def repeated_text(content, count=4, characters=12):
    """Detect consecutive words/phrases and long character runs in one message."""
    text = normal(content)
    if re.search(r"([^\W_])\1{" + str(characters - 1) + r",}", text):
        return True
    tokens = re.findall(r"[^\W_]+", text)
    # Bound phrase length and runtime even for Nitro-sized messages.
    for width in range(1, min(12, len(tokens) // count) + 1):
        for start in range(len(tokens) - width * count + 1):
            phrase = tokens[start : start + width]
            if all(tokens[start + n * width : start + (n + 1) * width] == phrase
                   for n in range(1, count)):
                return True
    return False


def host(value):
    value = value.strip().lower().rstrip(".")
    parsed = urlsplit(value if "://" in value else "https://" + value)
    name = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
    if (
        not name
        or "." not in name
        or parsed.username
        or parsed.password
        or any(c.isspace() for c in name)
    ):
        raise ValueError(
            "Use a domain such as example.com, without a path or credentials."
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Enter a domain without a path.")
    return name


def matches_host(value, domain):
    return value == domain or value.endswith("." + domain)


def default_config():
    return dict(
        enabled=False,
        mode="enforce",
        log_channel_id=None,
        ignored_roles=[],
        ignored_channels=[],
        allowed_domains=[],
        blocked_domains=[],
        words=[],
        ban_words=[],
        timeout_seconds=600,
        spam_count=6,
        spam_seconds=5,
        duplicate_count=3,
        repeat_count=4,
        repeat_characters=12,
        mention_count=5,
        caps_percent=75,
        caps_min=12,
        emoji_count=12,
        attachment_count=4,
        blocked_extensions=["exe", "scr", "bat", "cmd", "com", "ps1"],
        escalate_after=5,
        raid=dict(enabled=False, count=12, seconds=10, min_age_hours=24),
        rules={
            name: dict(
                enabled=name
                in (
                    "spam",
                    "duplicates",
                    "repetition",
                    "mentions",
                    "everyone",
                    "invites",
                    "words",
                    "domains",
                ),
                action=(
                    "timeout"
                    if name in ("spam", "duplicates", "repetition", "mentions", "everyone")
                    else "delete"
                ),
            )
            for name in RULE_NAMES
        },
    )


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("Automod settings must be an object.")
    p = default_config()
    p.update(copy.deepcopy(raw))
    # Older saved configurations have no repetition rule yet.
    if isinstance(p["rules"], dict):
        p["rules"] = {**default_config()["rules"], **p["rules"]}
    if type(p["enabled"]) is not bool or p["mode"] not in ("observe", "enforce"):
        raise ValueError("Use an enabled boolean and observe/enforce mode.")
    for key, lo, hi in [
        ("timeout_seconds", 1, 2419200),
        ("spam_count", 2, 50),
        ("spam_seconds", 1, 60),
        ("duplicate_count", 2, 20),
        ("repeat_count", 3, 20),
        ("repeat_characters", 8, 100),
        ("mention_count", 1, 50),
        ("caps_percent", 30, 100),
        ("caps_min", 5, 1000),
        ("emoji_count", 3, 100),
        ("attachment_count", 1, 10),
        ("escalate_after", 2, 100),
    ]:
        if type(p[key]) is not int or not lo <= p[key] <= hi:
            raise ValueError(f"{key} must be {lo}–{hi}.")
    for key in ("ignored_roles", "ignored_channels"):
        if (
            not isinstance(p[key], list)
            or len(p[key]) > 100
            or any(not str(i).isdigit() for i in p[key])
        ):
            raise ValueError(f"{key}: select at most 100 IDs.")
        p[key] = list(dict.fromkeys(int(i) for i in p[key]))
    if p["log_channel_id"] is not None:
        if not str(p["log_channel_id"]).isdigit():
            raise ValueError("Invalid log channel.")
        p["log_channel_id"] = int(p["log_channel_id"])
    for key in ("allowed_domains", "blocked_domains", "words", "ban_words", "blocked_extensions"):
        if (
            not isinstance(p[key], list)
            or len(p[key]) > 200
            or any(not isinstance(x, str) or not 1 <= len(x) <= 100 for x in p[key])
        ):
            raise ValueError(f"{key}: use at most 200 entries, each 1–100 characters.")
        p[key] = list(
            dict.fromkeys(
                host(x) if key.endswith("domains") else normal(x) for x in p[key]
            )
        )
        if any(not x for x in p[key]):
            raise ValueError(f"{key}: entries must contain visible characters.")
    if not isinstance(p["rules"], dict) or set(p["rules"]) != set(RULE_NAMES):
        raise ValueError("Invalid automod rules.")
    for r in p["rules"].values():
        if (
            not isinstance(r, dict)
            or type(r.get("enabled")) is not bool
            or r.get("action") not in ACTIONS
        ):
            raise ValueError("Each rule needs enabled and action settings.")
    r = p["raid"]
    if not isinstance(r, dict) or type(r.get("enabled")) is not bool:
        raise ValueError("Invalid raid settings.")
    for key, lo, hi in [
        ("count", 3, 100),
        ("seconds", 3, 60),
        ("min_age_hours", 1, 720),
    ]:
        if type(r.get(key)) is not int or not lo <= r[key] <= hi:
            raise ValueError(f"Raid {key} must be {lo}–{hi}.")
    return p


def content_matches(content, mentions, everyone, attachments, p):
    text = normal(content)
    found = []
    domains = []
    for url in URL.findall(text):
        try:
            name = urlsplit(url if "://" in url else "https://" + url).hostname
            if name:
                domains.append(name.rstrip(".").encode("idna").decode("ascii"))
        except (ValueError, UnicodeError):
            continue
    if mentions >= p["mention_count"]:
        found.append("mentions")
    if everyone:
        found.append("everyone")
    if INVITE.search(text):
        found.append("invites")
    if any(not any(matches_host(d, a) for a in p["allowed_domains"]) for d in domains):
        found.append("links")
    if any(matches_host(d, b) for d in domains for b in p["blocked_domains"]):
        found.append("domains")
    letters = [
        c
        for c in unicodedata.normalize("NFKC", content)
        if c.isalpha() and c.lower() != c.upper()
    ]
    if (
        len(letters) >= p["caps_min"]
        and sum(c.isupper() for c in letters) * 100 / len(letters) >= p["caps_percent"]
    ):
        found.append("caps")
    emoji = len(CUSTOM_EMOJI.findall(content)) + sum(
        0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF
        for c in CUSTOM_EMOJI.sub("", content)
    )
    if emoji >= p["emoji_count"]:
        found.append("emoji")
    if contains_word(text, p["words"]):
        found.append("words")
    if p["rules"]["repetition"]["enabled"] and repeated_text(
        text, p["repeat_count"], p["repeat_characters"]
    ):
        found.append("repetition")
    if len(attachments) >= p["attachment_count"] or any(
        a.filename.rsplit(".", 1)[-1].lower() in p["blocked_extensions"]
        for a in attachments
    ):
        found.append("attachments")
    return [name for name in found if p["rules"][name]["enabled"]]


class Automod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}
        self.messages = defaultdict(lambda: deque(maxlen=100))
        self.strikes = defaultdict(lambda: deque(maxlen=100))
        self.joins = defaultdict(lambda: deque(maxlen=200))
        self.locks = defaultdict(asyncio.Lock)
        self.last_action = {}
        self.last_action_kind = {}
        self.seen = {}
        self.deleted = {}
        self.cleanup_at = 0

    async def cog_check(self, ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage("Use this command in a server.")
        if (
            ctx.author.id != ctx.guild.owner_id
            and not ctx.author.guild_permissions.manage_guild
        ):
            raise commands.MissingPermissions(["manage_guild"])
        return True

    async def config_for(self, gid):
        if gid not in self.cache:
            row = await self.bot.store.one(
                "SELECT * FROM automod_config WHERE guild_id=?", (gid,)
            )
            self.cache[gid] = (
                validate_config(json.loads(row["data"])) if row else default_config(),
                row["version"] if row else 0,
            )
        p, v = self.cache[gid]
        return copy.deepcopy(p), v

    async def save(self, gid, p, version, actor_id):
        clean = validate_config(p)

        def write(c):
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT version FROM automod_config WHERE guild_id=?", (gid,)
            ).fetchone()
            if (row["version"] if row else 0) != version:
                raise ValueError("Automod settings changed. Reload and retry.")
            c.execute(
                "INSERT INTO automod_config VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data,version=excluded.version",
                (gid, json.dumps(clean), version + 1),
            )

        await self.bot.store._run(write)
        self.cache[gid] = (clean, version + 1)
        await self.bot.store.audit(
            gid, actor_id, "automod.config", f"Version {version+1}"
        )
        return version + 1

    async def change(self, ctx, edit):
        p, v = await self.config_for(ctx.guild.id)
        edit(p)
        await self.save(ctx.guild.id, p, v, ctx.author.id)

    async def say(self, ctx, title, body):
        await ctx.send(embed=embed(title, body))

    async def log_case(self, guild, user_id, description, p):
        ident = await self.bot.store.execute(
            "INSERT INTO audit(guild_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?)",
            (
                guild.id,
                user_id,
                "automod.action",
                description[:2000],
                discord.utils.utcnow().isoformat(),
            ),
        )
        channel = (
            guild.get_channel(p["log_channel_id"]) if p["log_channel_id"] else None
        )
        if channel:
            try:
                await channel.send(
                    embed=embed(
                        f"Automod case #{ident}", f"<@{user_id}>\n{description[:3000]}"
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                log.warning("Automod log channel unavailable: %s", channel.id)
        return ident

    async def handle(self, message, edited=False):
        if not message.guild or message.author.bot or message.webhook_id:
            return False
        p, _ = await self.config_for(message.guild.id)
        if not p["enabled"] or not isinstance(message.author, discord.Member):
            return False
        member = message.author
        if member.id == message.guild.owner_id:
            return False
        banned = contains_word(message.content, p["ban_words"])
        if banned and (member.guild_permissions.manage_guild or member.guild_permissions.administrator):
            # Administrators must be able to manage/test their own word lists.
            ctx = await self.bot.get_context(message)
            if ctx.command and ctx.command.cog is self and ctx.command.name in ("banwords", "automod"):
                return False
        exempt = (
            member.guild_permissions.administrator
            or message.channel.id in p["ignored_channels"]
            or getattr(message.channel, "parent_id", None) in p["ignored_channels"]
            or any(r.id in p["ignored_roles"] for r in member.roles)
        )
        # Explicit instant-ban words cannot be bypassed with ordinary exemptions.
        if exempt and not banned:
            return False
        now = time.time()
        key = (message.guild.id, member.id)
        revision = (message.id, message.content, tuple((a.id, a.filename) for a in message.attachments))
        if now - self.cleanup_at > 60:
            self.cleanup_at = now
            self.seen = {k: v for k, v in self.seen.items() if v[0] > now - 120}
            self.deleted = {k: v for k, v in self.deleted.items() if v > now - 120}
            for cache in (self.messages, self.strikes, self.joins):
                for k, values in list(cache.items()):
                    if not values or values[-1][0] < now - 600:
                        cache.pop(k, None)
            self.last_action = {k: v for k, v in self.last_action.items() if v > now - 600}
            self.last_action_kind = {k: v for k, v in self.last_action_kind.items() if k in self.last_action}
        async with self.locks[key]:
            if revision in self.seen:
                return self.seen[revision][1]
            self.seen[revision] = (now, False)
            mentions = len({m.id for m in message.mentions}) + len({r.id for r in message.role_mentions})
            found = [] if exempt else content_matches(
                message.content, mentions, message.mention_everyone, message.attachments, p,
            )
            if banned:
                found.append("banwords")
            history = self.messages[key]
            # Edits replace that message's fingerprint, never its original time.
            # Thus editing several earlier messages into identical text is covered
            # without counting a single message repeatedly as a new message.
            fingerprint = normal(message.content)
            if edited:
                for i, (sent_at, _, previous) in enumerate(history):
                    if previous.id == message.id:
                        history[i] = (sent_at, fingerprint, message)
                        break
            else:
                history.append((now, fingerprint, message))
            spam = [m for t, _, m in history if t >= now - p["spam_seconds"]]
            duplicates = [m for t, txt, m in history if t >= now - 30 and txt == fingerprint]
            if not exempt:
                if not edited and p["rules"]["spam"]["enabled"] and len(spam) >= p["spam_count"]:
                    found.append("spam")
                # Include uncached edits in detection, but not in the message rate.
                duplicate_total = len(duplicates) + int(edited and all(m.id != message.id for m in duplicates))
                if fingerprint and p["rules"]["duplicates"]["enabled"] and duplicate_total >= p["duplicate_count"]:
                    found.append("duplicates")
            if not found:
                return False
            if p["mode"] == "observe":
                await self.log_case(message.guild, member.id,
                    "Observed " + ", ".join(found) + f" in #{message.channel.name}; message {message.id}", p)
                return False
            self.seen[revision] = (now, True)
            errors = []
            # Remove the triggering message and the messages forming its spam
            # burst. Never delete unrelated messages from other members.
            targets = [message]
            if "duplicates" in found:
                targets.extend(duplicates)
            if "spam" in found:
                targets.extend(spam)
            for target in {m.id: m for m in targets}.values():
                if target.id in self.deleted:
                    continue
                try:
                    await target.delete()
                    self.deleted[target.id] = now
                except discord.NotFound:
                    self.deleted[target.id] = now
                except discord.HTTPException as error:
                    errors.append(f"Delete {target.id} failed: " + str(error)[:180])
            self.strikes[key].append((now, message.id))
            actions = ["ban" if name == "banwords" else p["rules"][name]["action"] for name in found]
            action = max(actions, key=ACTIONS.index)
            if action in ("delete", "warn") and sum(t > now - 600 for t, _ in self.strikes[key]) >= p["escalate_after"]:
                action = "timeout"
            # A previous timeout/delete must never suppress a new instant ban.
            stronger = ACTIONS.index(action) > ACTIONS.index(self.last_action_kind.get(key, "delete"))
            if now - self.last_action.get(key, 0) >= 5 or stronger:
                self.last_action[key] = now
                self.last_action_kind[key] = action
                applied = "delete" if message.id in self.deleted else "none"
                try:
                    if action in ("timeout", "kick", "ban") and member.top_role >= message.guild.me.top_role:
                        raise ValueError("Member is above the bot role; message deletion only.")
                    reason = "Automod: " + ", ".join(sorted(set(found)))
                    if action == "timeout":
                        await member.timeout(timedelta(seconds=p["timeout_seconds"]), reason=reason)
                    elif action == "kick":
                        await member.kick(reason=reason)
                    elif action == "ban":
                        await member.ban(reason=reason, delete_message_seconds=0)
                    elif action == "warn":
                        await self.bot.store.execute(
                            "INSERT INTO warnings(guild_id,user_id,actor_id,reason,created_at) VALUES(?,?,?,?,?)",
                            (message.guild.id, member.id, self.bot.user.id, reason, discord.utils.utcnow().isoformat()),
                        )
                    if action != "delete":
                        applied = action
                except (discord.HTTPException, ValueError) as error:
                    errors.append(str(error)[:300])
                await self.log_case(message.guild, member.id,
                    f'{", ".join(sorted(set(found)))} · action: {applied}\nChannel: <#{message.channel.id}> · message: {message.id}'
                    + ("\n" + "; ".join(errors) if errors else ""), p)
            elif errors:
                await self.log_case(message.guild, member.id,
                    "Action cooldown; " + "; ".join(errors), p)
            return True

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.content != after.content or before.attachments != after.attachments:
            await self.handle(after, edited=True)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        if payload.cached_message or not ({"content", "attachments"} & payload.data.keys()):
            return
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild:
            return
        p, _ = await self.config_for(guild.id)
        if not p["enabled"]:
            return
        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel:
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            await self.handle(message, edited=True)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        p, _ = await self.config_for(member.guild.id)
        r = p["raid"]
        if not p["enabled"] or not r["enabled"] or member.bot:
            return
        now = time.time()
        history = self.joins[member.guild.id]
        history.append((now, member.id))
        burst = [uid for t, uid in history if t >= now - r["seconds"]]
        if len(burst) < r["count"]:
            return
        for uid in burst:
            target = member.guild.get_member(uid)
            if (
                not target
                or target.bot
                or target.guild_permissions.administrator
                or target.id == member.guild.owner_id
            ):
                continue
            if (discord.utils.utcnow() - target.created_at).total_seconds() >= r[
                "min_age_hours"
            ] * 3600:
                continue
            key = (member.guild.id, uid)
            if now - self.last_action.get(key, 0) < 60:
                continue
            self.last_action[key] = now
            result = "Observed join burst"
            if p["mode"] == "enforce":
                try:
                    if target.top_role >= member.guild.me.top_role:
                        raise ValueError("Member is above the bot role.")
                    await target.timeout(
                        timedelta(seconds=p["timeout_seconds"]),
                        reason="Automod: young account in join burst",
                    )
                    result = "Timed out young account in join burst"
                except (discord.HTTPException, ValueError) as error:
                    result = "Join-burst action failed: " + str(error)[:200]
            await self.log_case(member.guild, uid, result, p)

    @commands.hybrid_group(name="automod", aliases=["am"], invoke_without_command=True)
    async def automod(self, ctx):
        """Show automod status and rules. Manage Server permission required."""
        await self.config.callback(self, ctx)

    @automod.command(name="config")
    async def config(self, ctx):
        """Show enabled rules, their actions, exemptions, and detection thresholds."""
        p, _ = await self.config_for(ctx.guild.id)
        page = embed(
            "Automod",
            f'**Status:** {"Enabled" if p["enabled"] else "Disabled"} · **Mode:** {p["mode"]}\n'
            f'Timeout: **{p["timeout_seconds"]}s** · Raid guard: **{p["raid"]["enabled"]}**',
        )
        page.add_field(
            name="Rules",
            value="\n".join(
                f'`{n}` · {"on" if r["enabled"] else "off"} · {r["action"]}'
                for n, r in p["rules"].items()
            ),
            inline=False,
        )
        page.add_field(
            name="Limits",
            value=f'Spam: {p["spam_count"]}/{p["spam_seconds"]}s · Duplicates: {p["duplicate_count"]}/30s\n'
            f'Same-message repetitions: {p["repeat_count"]} · Character run: {p["repeat_characters"]}\n'
            f'Mentions: {p["mention_count"]} · Emoji: {p["emoji_count"]}\nCaps: {p["caps_percent"]}% of {p["caps_min"]}+ letters\n'
            f'Repeat escalation: {p["escalate_after"]} violations/10min → timeout',
            inline=False,
        )
        page.add_field(name="Instant-ban words", value=f'{len(p["ban_words"])} configured · use `{ctx.clean_prefix}banwords list`.', inline=False)
        page.set_footer(text=f"{ctx.clean_prefix}help automod")
        await ctx.send(embed=page)

    @automod.command(name="enable")
    async def enable(self, ctx):
        """Enable the configured rules. Existing choices are preserved."""
        missing = [
            p
            for p in ("manage_messages", "moderate_members")
            if not getattr(ctx.guild.me.guild_permissions, p)
        ]
        if missing:
            raise commands.BotMissingPermissions(missing)
        await self.change(ctx, lambda p: p.update(enabled=True))
        await self.say(
            ctx,
            "Automod enabled",
            "Run `automod config` to review active rules and actions.",
        )

    @automod.command(name="disable")
    async def disable(self, ctx):
        """Disable the bot's automod pipeline; retain settings. Native Discord rules remain until removed."""
        await self.change(ctx, lambda p: p.update(enabled=False))
        await self.say(
            ctx,
            "Automod disabled",
            "Your settings were kept. Separately installed native rules can be removed with `automod native off`.",
        )

    @automod.command(name="hard", aliases=["strict"])
    @commands.bot_has_guild_permissions(manage_messages=True, moderate_members=True, ban_members=True)
    async def hard(self, ctx):
        """Enable strict anti-spam: 4 messages/5s, 3 duplicates/30s, repeated text, and 1h timeouts."""
        def update(p):
            p.update(enabled=True, mode="enforce", spam_count=4, spam_seconds=5,
                     duplicate_count=3, repeat_count=4, repeat_characters=12,
                     timeout_seconds=3600, mention_count=4, emoji_count=10,
                     escalate_after=3)
            for name in ("spam", "duplicates", "repetition", "mentions", "everyone", "emoji"):
                p["rules"][name].update(enabled=True, action="timeout")
            for name in ("invites", "domains", "attachments"):
                p["rules"][name]["enabled"] = True
        await self.change(ctx, update)
        await self.say(ctx, "Hard automod enabled",
            "Spam: **4 messages / 5s**. Duplicates: **3 / 30s**. "
            "Repeated words/phrases: **4 consecutive copies**; character runs: **12**. "
            "Violating messages are deleted; spam earns a **1-hour timeout**. "
            "Instant-ban words remain active. Use `automod config` and `automod ignore` to review settings.")

    @automod.command(name="mode")
    async def mode(self, ctx, mode: str):
        """Choose observe (log only) or enforce (delete and apply the configured action)."""
        await self.change(ctx, lambda p: p.update(mode=mode.lower()))
        await self.say(ctx, "Automod mode", f"Mode: **{mode.lower()}**.")

    @automod.command(name="rule")
    async def rule(self, ctx, rule: str, enabled: bool, action: str = "delete"):
        """Set one rule. Rules: spam, duplicates, repetition, mentions, everyone, invites, links, caps, emoji, words, domains, attachments."""
        if rule not in RULE_NAMES:
            raise ValueError("Unknown rule. Use `automod config` for names.")
        await self.change(
            ctx, lambda p: p["rules"][rule].update(enabled=enabled, action=action)
        )
        await self.say(
            ctx,
            "Rule updated",
            f'`{rule}` · {"on" if enabled else "off"} · **{action}**.',
        )

    @automod.command(name="punishment", aliases=["action"])
    async def punishment(self, ctx, rule: str, action: str):
        """Set delete, warn, timeout, kick, or ban for one rule without changing whether it is enabled."""
        if rule not in RULE_NAMES:
            raise ValueError("Unknown rule. Use `automod config`.")
        await self.change(ctx, lambda p: p["rules"][rule].update(action=action))
        await self.say(ctx, "Action updated", f"`{rule}` uses **{action}**.")

    @automod.command(name="limit")
    async def limit(self, ctx, setting: str, value: int):
        """Set spam_count, spam_seconds, duplicate_count, repeat_count, repeat_characters, mention_count, caps_percent, caps_min, emoji_count, attachment_count, timeout_seconds, or escalate_after."""
        allowed = {
            "spam_count",
            "spam_seconds",
            "duplicate_count",
            "repeat_count",
            "repeat_characters",
            "mention_count",
            "caps_percent",
            "caps_min",
            "emoji_count",
            "attachment_count",
            "timeout_seconds",
            "escalate_after",
        }
        if setting not in allowed:
            raise ValueError("Unknown setting. Use `help automod limit`.")
        await self.change(ctx, lambda p: p.update({setting: value}))
        await self.say(ctx, "Limit updated", f"`{setting}` = **{value}**.")

    @automod.command(name="logging", aliases=["log"])
    async def logging(self, ctx, channel: discord.TextChannel):
        """Set the channel for automod actions and failures."""
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.send_messages or not perms.embed_links:
            raise ValueError("I need Send Messages and Embed Links in that channel.")
        await self.change(ctx, lambda p: p.update(log_channel_id=channel.id))
        await self.say(ctx, "Automod logging", f"Logs will go to {channel.mention}.")

    @automod.group(name="ignore", invoke_without_command=True)
    async def ignore(self, ctx):
        """Show automod exemptions; use ignore channel or ignore role to add one."""
        p, _ = await self.config_for(ctx.guild.id)
        await self.say(
            ctx,
            "Automod exemptions",
            "Channels: "
            + (", ".join(f"<#{i}>" for i in p["ignored_channels"]) or "None")
            + "\nRoles: "
            + (", ".join(f"<@&{i}>" for i in p["ignored_roles"]) or "None")
            + "\nServer owner, administrators, bots, and webhooks are exempt from message automod.",
        )

    @ignore.command(name="channel")
    async def ignore_channel(self, ctx, channel: discord.TextChannel):
        """Exempt a text channel and its threads from message automod."""
        await self.change(ctx, lambda p: p["ignored_channels"].append(channel.id))
        await self.say(ctx, "Channel exempted", channel.mention)

    @ignore.command(name="role")
    async def ignore_role(self, ctx, role: discord.Role):
        """Exempt members with this role from message automod."""
        if role.is_default():
            raise ValueError(
                "The everyone role cannot be exempted. Disable automod instead."
            )
        await self.change(ctx, lambda p: p["ignored_roles"].append(role.id))
        await self.say(ctx, "Role exempted", role.mention)

    @ignore.command(name="show")
    async def ignore_show(self, ctx):
        """List all message automod exemptions."""
        await self.ignore.callback(self, ctx)

    @ignore.command(name="reset")
    async def ignore_reset(self, ctx):
        """Remove all configured channel and role exemptions."""
        await self.change(
            ctx, lambda p: p.update(ignored_roles=[], ignored_channels=[])
        )
        await self.say(
            ctx,
            "Exemptions reset",
            "All configured role and channel exemptions were removed.",
        )

    @automod.group(name="unignore", invoke_without_command=True)
    async def unignore(self, ctx):
        """Remove an exemption using unignore channel or unignore role."""
        await ctx.send_help(ctx.command)

    @unignore.command(name="channel")
    async def unignore_channel(self, ctx, channel: discord.TextChannel):
        """Remove a channel exemption."""
        await self.change(
            ctx,
            lambda p: p.update(
                ignored_channels=[i for i in p["ignored_channels"] if i != channel.id]
            ),
        )
        await self.say(ctx, "Exemption removed", channel.mention)

    @unignore.command(name="role")
    async def unignore_role(self, ctx, role: discord.Role):
        """Remove a role exemption."""
        await self.change(
            ctx,
            lambda p: p.update(
                ignored_roles=[i for i in p["ignored_roles"] if i != role.id]
            ),
        )
        await self.say(ctx, "Exemption removed", role.mention)

    @automod.command(name="words")
    async def words(self, ctx, operation: str = "list", *, word: str = ""):
        """Manage literal blocked words/phrases: automod words add phrase, remove phrase, or list."""
        if operation == "list":
            p, _ = await self.config_for(ctx.guild.id)
            return await self.say(
                ctx,
                "Blocked words",
                discord.utils.escape_markdown(", ".join(p["words"]))[:3500]
                or "No words configured.",
            )
        if operation not in ("add", "remove") or not word.strip():
            raise ValueError(
                "Use words add <phrase>, words remove <phrase>, or words list."
            )

        def update(p):
            value = normal(word.strip())
            if operation == "add":
                p["words"].append(value)
            else:
                p["words"] = [w for w in p["words"] if w != value]

        await self.change(ctx, update)
        await self.say(
            ctx,
            "Word filter updated",
            f'Phrase {"added" if operation=="add" else "removed"}.',
        )

    @commands.hybrid_group(name="banwords", aliases=["banword"], invoke_without_command=True)
    async def banwords(self, ctx):
        """Manage instant-ban words/phrases: banwords add, remove, or list. Matching uses whole words."""
        await self.banwords_list.callback(self, ctx)

    @banwords.command(name="add")
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True, manage_messages=True)
    async def banwords_add(self, ctx, *, word: str):
        """Add a literal word or phrase; enable enforce mode. Matches are deleted and the sender is banned."""
        value = normal(word)
        if not 1 <= len(value) <= 100:
            raise ValueError("Use a visible word or phrase of 1–100 characters.")
        def update(p):
            if value not in p["ban_words"]:
                p["ban_words"].append(value)
            p.update(enabled=True, mode="enforce")
        await self.change(ctx, update)
        await self.say(ctx, "Instant-ban word added",
            "The word/phrase is active. Matching messages are deleted and their sender is banned. "
            "Ordinary role/channel exemptions do not bypass this list. "
            "Discord permissions and role hierarchy still apply; the server owner and bots are exempt.")

    @banwords.command(name="remove", aliases=["delete"])
    @commands.has_guild_permissions(ban_members=True)
    async def banwords_remove(self, ctx, *, word: str):
        """Remove an instant-ban word or phrase."""
        value = normal(word)
        await self.change(ctx, lambda p: p.update(ban_words=[w for w in p["ban_words"] if w != value]))
        await self.say(ctx, "Instant-ban word removed", "The entry was removed if it was present.")

    @banwords.command(name="list")
    async def banwords_list(self, ctx):
        """List instant-ban words and the current enforcement status."""
        p, _ = await self.config_for(ctx.guild.id)
        words = p["ban_words"]
        status = "enforcing" if p["enabled"] and p["mode"] == "enforce" else "paused (automod disabled or observing)"
        for start in range(0, max(1, len(words)), 20):
            text = "\n".join(discord.utils.escape_markdown(w) for w in words[start:start + 20]) or "No instant-ban words configured."
            await self.say(ctx, "Instant-ban words", f"Status: **{status}**\n{text}")

    @automod.command(name="domain")
    async def domain(
        self, ctx, list_type: str, operation: str = "list", domain: str = ""
    ):
        """Manage allowed/blocked link domains. Example: automod domain blocked add bad.example."""
        if list_type not in ("allowed", "blocked"):
            raise ValueError("Choose allowed or blocked.")
        key = list_type + "_domains"
        if operation == "list":
            p, _ = await self.config_for(ctx.guild.id)
            return await self.say(
                ctx,
                list_type.title() + " domains",
                "\n".join(p[key])[:3500] or "No domains configured.",
            )
        if operation not in ("add", "remove"):
            raise ValueError("Choose add, remove, or list.")
        value = host(domain)

        def update(p):
            if operation == "add":
                p[key].append(value)
            else:
                p[key] = [x for x in p[key] if x != value]

        await self.change(ctx, update)
        await self.say(
            ctx,
            "Domain list updated",
            f'`{value}` {"added" if operation=="add" else "removed"}. Subdomains are included.',
        )

    @automod.command(name="raid")
    async def raid(
        self,
        ctx,
        enabled: bool,
        count: int = 12,
        seconds: int = 10,
        min_age_hours: int = 24,
    ):
        """Timeout young accounts in a join burst. Example: automod raid on 12 10 24. Existing members are not targeted."""
        await self.change(
            ctx,
            lambda p: p.update(
                raid=dict(
                    enabled=enabled,
                    count=count,
                    seconds=seconds,
                    min_age_hours=min_age_hours,
                )
            ),
        )
        await self.say(
            ctx,
            "Raid guard updated",
            f'{"Enabled" if enabled else "Disabled"}: **{count} joins / {seconds}s**, accounts younger than **{min_age_hours} hours**.',
        )

    @automod.command(name="test")
    async def test(self, ctx, *, text: str):
        """Preview content filters without punishing anyone. Does not simulate message rate or native rules."""
        p, _ = await self.config_for(ctx.guild.id)
        found = content_matches(
            text,
            len(set(re.findall(r"<@!?\d+>|<@&\d+>", text))),
            "@everyone" in text or "@here" in text,
            [],
            p,
        )
        await self.say(
            ctx,
            "Automod test",
            (
                ("Matched: " + ", ".join(f"`{x}`" for x in found))
                if found
                else "No enabled content rule matched."
            ),
        )

    @automod.command(name="native")
    async def native(self, ctx, enabled: bool):
        """Install/remove FortuneManager's native Discord mention and spam rules. Native rules also work while the bot is offline."""
        if ctx.author.id != ctx.guild.owner_id:
            raise commands.CheckFailure(
                "Only the server owner can manage native Discord rules."
            )
        p, _ = await self.config_for(ctx.guild.id)
        existing = await ctx.guild.fetch_automod_rules()
        ours = {
            r.name: r
            for r in existing
            if r.creator_id == ctx.guild.me.id
            and r.name.startswith("FortuneManager | ")
        }
        desired = [
            (
                "Mentions",
                discord.AutoModTrigger(
                    mention_limit=p["mention_count"], mention_raid_protection=True
                ),
            ),
            ("Spam", discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.spam)),
        ]
        for label, trigger in desired:
            name = "FortuneManager | " + label
            rule = ours.get(name)
            if not enabled:
                if rule:
                    await rule.delete(reason="Disabled by server owner")
                continue
            kwargs = dict(
                enabled=True,
                trigger=trigger,
                exempt_roles=[discord.Object(i) for i in p["ignored_roles"]],
                exempt_channels=[discord.Object(i) for i in p["ignored_channels"]],
                actions=[
                    discord.AutoModRuleAction(
                        type=discord.AutoModRuleActionType.block_message
                    )
                ],
                reason="Configured by server owner",
            )
            if rule:
                await rule.edit(**kwargs)
            else:
                await ctx.guild.create_automod_rule(
                    name=name,
                    event_type=discord.AutoModRuleEventType.message_send,
                    **kwargs,
                )
        await self.bot.store.audit(
            ctx.guild.id, ctx.author.id, "automod.native", enabled
        )
        await self.say(
            ctx,
            "Native Discord automod",
            f'FortuneManager mention and spam rules {"installed" if enabled else "removed"}. Discord applies its own rule limits and exemptions.',
        )
