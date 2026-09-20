"""Security policy and evidence rules. No network calls or hidden exemptions."""

from collections import defaultdict, deque
from dataclasses import dataclass
import copy
import time
import math
import discord

DANGEROUS = discord.Permissions(
    administrator=True,
    manage_guild=True,
    manage_roles=True,
    manage_channels=True,
    manage_webhooks=True,
    ban_members=True,
    kick_members=True,
    mention_everyone=True,
    manage_messages=True,
    moderate_members=True,
    manage_expressions=True,
    manage_events=True,
    manage_threads=True,
    move_members=True,
).value

# count is inclusive: a limit of 3 fires on the third action in the window.
RULES = {
    "ban": (3, 15, 4),
    "kick": (5, 15, 3),
    "prune": (1, 60, 12),
    "bot_add": (1, 60, 8),
    "channel_create": (5, 15, 2),
    "channel_delete": (1, 60, 8),
    "channel_update": (5, 15, 2),
    "role_create": (5, 15, 2),
    "role_delete": (1, 60, 8),
    "role_update": (5, 15, 2),
    "permission_grant": (1, 60, 12),
    "webhook_create": (2, 30, 5),
    "webhook_delete": (3, 30, 3),
    "webhook_update": (3, 30, 3),
    "guild_update": (3, 30, 4),
    "overwrite_create": (3, 30, 4),
    "overwrite_update": (3, 30, 4),
    "overwrite_delete": (3, 30, 4),
    "integration_create": (1, 60, 8),
    "integration_delete": (2, 30, 4),
    "integration_update": (3, 30, 3),
    "emoji_delete": (5, 30, 2),
    "sticker_delete": (3, 30, 3),
    "emoji_create": (5, 30, 2),
    "emoji_update": (5, 30, 2),
    "sticker_create": (3, 30, 3),
    "sticker_update": (3, 30, 3),
    "unban": (5, 30, 2),
    "member_move": (5, 15, 3),
    "member_disconnect": (3, 15, 4),
    "automod_rule_create": (2, 30, 5),
    "automod_rule_delete": (1, 60, 10),
    "automod_rule_update": (2, 30, 5),
}
ALIASES = {
    "botadd": "bot_add",
    "chcr": "channel_create",
    "chdl": "channel_delete",
    "chup": "channel_update",
    "rlcr": "role_create",
    "rldl": "role_delete",
    "rlup": "role_update",
    "memup": "permission_grant",
    "serverup": "guild_update",
    "antiban": "ban",
    "antikick": "kick",
}


def rule_name(value):
    name = value.lower().replace("-", "_")
    name = ALIASES.get(name, name)
    if name not in RULES:
        raise ValueError("Unknown rule. Use `antinuke rules` for the rule names.")
    return name


def default_policy():
    return dict(
        enabled=False,
        mode="enforce",
        punishment="strip",
        log_channel_id=None,
        auto_lockdown=False,
        actor_score=18,
        guild_score=60,
        rules={
            name: dict(enabled=True, count=n, seconds=s, hourly=max(10, n * 10))
            for name, (n, s, _) in RULES.items()
        },
        trust={},
    )


def validate_policy(data):
    result = default_policy()
    if not isinstance(data, dict):
        raise ValueError("Security settings must be an object.")
    for key in ("enabled", "auto_lockdown"):
        if type(data.get(key, result[key])) is not bool:
            raise ValueError(f"{key} must be true or false.")
        result[key] = data.get(key, result[key])
    for key, choices in [
        ("mode", ("observe", "enforce")),
        ("punishment", ("strip", "ban", "kick")),
    ]:
        result[key] = data.get(key, result[key])
        if result[key] not in choices:
            raise ValueError(f"{key}: choose " + ", ".join(choices))
    result["log_channel_id"] = data.get("log_channel_id")
    if result["log_channel_id"] is not None:
        value = str(result["log_channel_id"])
        if not value.isdigit() or not 0 < int(value) < 2**64:
            raise ValueError("Invalid security log channel ID.")
        result["log_channel_id"] = int(value)
    for key in ("actor_score", "guild_score"):
        value = data.get(key, result[key])
        if type(value) is not int or not 5 <= value <= 500:
            raise ValueError(f"{key} must be 5–500.")
        result[key] = value
    if not isinstance(data.get("rules", {}), dict):
        raise ValueError("Rules must be an object.")
    for name, raw in data.get("rules", {}).items():
        if name not in RULES or not isinstance(raw, dict):
            raise ValueError("Invalid security rule.")
        for key, low, high in [
            ("count", 1, 100),
            ("seconds", 1, 300),
            ("hourly", 1, 1000),
        ]:
            value = raw.get(key, result["rules"][name][key])
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} {key} must be {low}–{high}.")
            result["rules"][name][key] = value
        enabled = raw.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("Rule enabled must be true or false.")
        result["rules"][name]["enabled"] = enabled
    trust = data.get("trust", {})
    if not isinstance(trust, dict) or len(trust) > 200:
        raise ValueError("Use at most 200 scoped trust entries.")
    for uid, grants in trust.items():
        if not str(uid).isdigit() or not isinstance(grants, dict):
            raise ValueError("Invalid trusted member.")
        for name, grant in grants.items():
            if name not in RULES or not isinstance(grant, dict):
                raise ValueError("Trust must name an individual rule.")
            if type(grant.get("budget")) is not int or not 1 <= grant["budget"] <= 1000:
                raise ValueError("Trust allowance must be 1–1000 actions per hour.")
            if (
                type(grant.get("expires")) not in (int, float)
                or not math.isfinite(grant["expires"])
                or grant["expires"] < 0
            ):
                raise ValueError("Invalid trust expiry.")
        result["trust"][str(uid)] = copy.deepcopy(grants)
    return result


@dataclass(frozen=True)
class Evidence:
    id: int
    guild_id: int
    actor_id: int
    target_id: int | None
    rule: str
    timestamp: float
    detail: dict


def normalize(entry):
    """A gateway entry already identifies its actor; never infer from latest log."""
    action = entry.action.name
    before, after = entry.before, entry.after
    if action == "member_role_update":
        added = getattr(after, "roles", [])
        if not any(
            getattr(getattr(r, "permissions", None), "value", 0) & DANGEROUS
            for r in added
        ):
            return None
        action = "permission_grant"
    elif action in ("role_update", "role_create"):
        old = getattr(getattr(before, "permissions", None), "value", 0)
        new = getattr(getattr(after, "permissions", None), "value", 0)
        if (new & ~old) & DANGEROUS:
            action = "permission_grant"
    if action not in RULES:
        return None
    uid = getattr(entry, "user_id", None) or getattr(
        getattr(entry, "user", None), "id", None
    )
    if not uid:
        return None
    detail = {"action": entry.action.name}
    for key in ("name", "topic", "vanity_url_code"):
        old, new = getattr(before, key, None), getattr(after, key, None)
        if isinstance(old, (str, int, bool)) or isinstance(new, (str, int, bool)):
            detail[key] = {"before": old, "after": new}
    if action == "permission_grant":
        detail["role_ids"] = [
            r.id
            for r in getattr(after, "roles", [])
            if getattr(getattr(r, "permissions", None), "value", 0) & DANGEROUS
        ]
        detail["before_permissions"] = getattr(
            getattr(before, "permissions", None), "value", None
        )
        detail["after_permissions"] = getattr(
            getattr(after, "permissions", None), "value", None
        )
    return Evidence(
        entry.id,
        entry.guild.id,
        int(uid),
        getattr(entry.target, "id", None),
        action,
        entry.created_at.timestamp(),
        detail,
    )


class Detector:
    def __init__(self):
        self.history = defaultdict(lambda: deque(maxlen=10000))
        self.seen = set()

    def remember(self, event):
        key = (event.guild_id, event.id)
        if key in self.seen:
            return
        history = self.history[event.guild_id]
        if len(history) == history.maxlen:
            self.seen.discard((event.guild_id, history[0].id))
        history.append(event)
        self.seen.add(key)
        if len(history) > 2000:
            expired = [e for e in history if e.timestamp < time.time() - 3600]
            for old in expired:
                self.seen.discard((event.guild_id, old.id))
            self.history[event.guild_id] = deque(
                (e for e in history if (e.guild_id, e.id) in self.seen), maxlen=10000
            )

    def decide(self, event, policy, now=None):
        now = time.time() if now is None else now
        self.remember(event)
        active = [
            e
            for e in self.history[event.guild_id]
            if now - 3600 <= e.timestamp <= now + 5
        ]
        same = [
            e for e in active if e.actor_id == event.actor_id and e.rule == event.rule
        ]
        if not policy["rules"][event.rule]["enabled"]:
            return None
        grant = policy["trust"].get(str(event.actor_id), {}).get(event.rule)
        if grant and (grant["expires"] == 0 or grant["expires"] > now):
            if len(same) <= grant["budget"]:
                return None
            return f'{event.rule}: trust allowance exceeded ({len(same)}/{grant["budget"]} in 1 hour)'
        rule = policy["rules"][event.rule]
        if not rule["enabled"]:
            return None
        count = sum(e.timestamp >= now - rule["seconds"] for e in same)
        if count >= rule["count"]:
            return f'{event.rule}: {count} actions in {rule["seconds"]}s (limit {rule["count"]})'
        if len(same) >= rule["hourly"]:
            return f'{event.rule}: hourly limit reached ({len(same)}/{rule["hourly"]})'

        def scored(e):
            if not policy["rules"][e.rule]["enabled"]:
                return False
            g = policy["trust"].get(str(e.actor_id), {}).get(e.rule)
            return not g or (g["expires"] != 0 and g["expires"] <= now)

        recent = [e for e in active if e.timestamp >= now - 60 and scored(e)]
        own = [e for e in recent if e.actor_id == event.actor_id]
        if sum(RULES[e.rule][2] for e in own) >= policy["actor_score"]:
            return "Combined action limit reached across security rules in 60s"
        if (
            len({e.actor_id for e in recent}) >= 2
            and sum(RULES[e.rule][2] for e in recent) >= policy["guild_score"]
        ):
            return "Coordinated activity: server action limit reached in 60s"
        return None
