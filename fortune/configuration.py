import copy
import re
import unicodedata
from urllib.parse import urlparse
import discord
from .permissions import admin, check_role
from .store import DEFAULT_CONFIG


def text(value, label, maximum, minimum=0):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} must be {minimum}–{maximum} characters.")
    return value


def snowflake(value, label):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.isdigit() or not 1 <= int(value) < 2**64:
        raise ValueError(f"{label} must be a Discord ID string.")
    return value


def url(value):
    text(value, "Image URL", 2000)
    if value and (urlparse(value).scheme != "https" or not urlparse(value).netloc):
        raise ValueError("Image URL must use HTTPS.")
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be true or false.")
    return value


def integer(value, label, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"{label} must be {lo}–{hi}.")
    return value


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", value):
        raise ValueError(
            f"{label}: use 1–32 letters, numbers, underscores, or hyphens."
        )
    return value


def validate_prefix(value):
    text(value, "Prefix", 8, 1)
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Use a prefix of 1–8 visible characters without spaces.")
    return value


def validate_config(raw, guild, actor, previous):
    if not isinstance(raw, dict):
        raise ValueError("Settings must be an object.")
    result = copy.deepcopy(DEFAULT_CONFIG)

    def channel(value, label, category=False):
        value = snowflake(value, label)
        if value:
            ch = guild.get_channel(int(value))
            if not isinstance(
                ch, discord.CategoryChannel if category else discord.TextChannel
            ):
                raise ValueError(
                    f"{label} must belong to this server and have the correct type."
                )
            if not ch.permissions_for(actor).view_channel:
                raise ValueError(f"You cannot access {label}.")
        return value

    def role(value, safe=False):
        value = snowflake(value, "Role")
        if value:
            check_role(guild.get_role(int(value)), actor, safe=safe)
        return value

    def roles(values, safe=False):
        if not isinstance(values, list) or len(values) > 20:
            raise ValueError("Select up to 20 roles.")
        return list(dict.fromkeys(role(v, safe) for v in values if v))

    result["prefix"] = validate_prefix(raw.get("prefix", DEFAULT_CONFIG["prefix"]))
    result["reply_delete_after"] = integer(
        raw.get("reply_delete_after", previous.get("reply_delete_after", 20)),
        "Reply deletion delay", 0, 3600,
    )
    result["staff_role_id"] = snowflake(raw.get("staff_role_id"), "Staff role")
    if result["staff_role_id"] != previous["staff_role_id"]:
        if not admin(actor):
            raise ValueError("Only administrators can change the staff role.")
        role(result["staff_role_id"], True)
    result["log_channel_id"] = channel(raw.get("log_channel_id"), "Moderation log")
    greet = raw.get("greet", {})
    if not isinstance(greet, dict):
        raise ValueError("Invalid greet settings.")
    result["greet"] = {
        "enabled": boolean(greet.get("enabled", False), "Greeting enabled"),
        "channel_id": channel(greet.get("channel_id"), "Greeting channel"),
        "content": text(greet.get("content", ""), "Greeting message", 1800),
        "use_embed": boolean(greet.get("use_embed", True), "Use embed"),
        "title": text(greet.get("title", ""), "Greeting title", 200),
        "description": text(greet.get("description", ""), "Greeting description", 3500),
        "color": text(greet.get("color", "#63d6ac"), "Color", 7),
        "image": url(greet.get("image", "")),
        "autorole_ids": roles(greet.get("autorole_ids", []), True),
    }
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", result["greet"]["color"]):
        raise ValueError("Choose a valid hex color.")
    if result["staff_role_id"] in result["greet"]["autorole_ids"]:
        raise ValueError("The staff role cannot be assigned automatically.")
    if result["greet"]["enabled"] and not result["greet"]["channel_id"]:
        raise ValueError("Select a greeting channel.")
    if (
        result["greet"]["enabled"]
        and not result["greet"]["content"]
        and not result["greet"]["use_embed"]
    ):
        raise ValueError("A plain greeting needs a message.")
    ticket = raw.get("ticket", {})
    if not isinstance(ticket, dict):
        raise ValueError("Invalid ticket settings.")
    result["ticket"].update(
        enabled=boolean(ticket.get("enabled", False), "Tickets enabled"),
        category_id=channel(ticket.get("category_id"), "Ticket category", True),
        log_channel_id=channel(ticket.get("log_channel_id"), "Ticket log"),
        support_role_ids=roles(ticket.get("support_role_ids", [])),
        max_open=integer(ticket.get("max_open", 1), "Open ticket limit", 1, 5),
    )
    if result["staff_role_id"] in result["ticket"]["support_role_ids"]:
        raise ValueError(
            "Use a separate ticket support role so staff grants remain granular."
        )
    if result["ticket"]["enabled"] and not result["ticket"]["category_id"]:
        raise ValueError("Select a ticket category.")
    panels = ticket.get("panels", [])
    if not isinstance(panels, list) or len(panels) > 10:
        raise ValueError("Create up to 10 ticket panels.")
    seen = set()
    for panel in panels:
        if not isinstance(panel, dict):
            raise ValueError("Invalid panel.")
        pid = identifier(panel.get("id"), "Panel ID")
        if pid in seen:
            raise ValueError("Panel IDs must be unique.")
        seen.add(pid)
        if panel.get("style") not in ("dropdown", "buttons"):
            raise ValueError("Choose dropdown or buttons.")
        options = panel.get("options", [])
        if not isinstance(options, list) or not 1 <= len(options) <= 25:
            raise ValueError("Each panel needs 1–25 categories.")
        clean = []
        oids = set()
        for option in options:
            if not isinstance(option, dict):
                raise ValueError("Invalid ticket category.")
            oid = identifier(option.get("id"), "Option ID")
            if oid in oids:
                raise ValueError("Option IDs must be unique within a panel.")
            oids.add(oid)
            clean.append(
                {
                    "id": oid,
                    "label": text(option.get("label"), "Category label", 80, 1),
                    "description": text(
                        option.get("description", ""), "Category description", 100
                    ),
                    "category_id": channel(
                        option.get("category_id"), "Category override", True
                    ),
                }
            )
        result["ticket"]["panels"].append(
            {
                "id": pid,
                "title": text(panel.get("title"), "Panel title", 256, 1),
                "description": text(
                    panel.get("description", ""), "Panel description", 3500
                ),
                "style": panel["style"],
                "options": clean,
            }
        )
    draft = raw.get("embeds", DEFAULT_CONFIG["embeds"])
    if not isinstance(draft, dict):
        raise ValueError("Invalid embed settings.")
    fields = draft.get("fields", [])
    if not isinstance(fields, list) or len(fields) > 25:
        raise ValueError("Use up to 25 embed fields.")
    clean_fields = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("Invalid embed field.")
        clean_fields.append(
            {
                "name": text(field.get("name"), "Field name", 256, 1),
                "value": text(field.get("value"), "Field value", 1024, 1),
                "inline": boolean(field.get("inline", False), "Inline field"),
            }
        )
    result["embeds"] = {
        "channel_id": channel(draft.get("channel_id"), "Embed destination"),
        "content": text(draft.get("content", ""), "Embed message", 2000),
        "title": text(draft.get("title", ""), "Embed title", 256),
        "description": text(draft.get("description", ""), "Embed description", 4096),
        "color": text(draft.get("color", "#63d6ac"), "Embed color", 7),
        "image": url(draft.get("image", "")),
        "fields": clean_fields,
    }
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", result["embeds"]["color"]):
        raise ValueError("Choose a valid embed color.")
    total = (
        len(result["embeds"]["title"])
        + len(result["embeds"]["description"])
        + sum(len(f["name"]) + len(f["value"]) for f in clean_fields)
    )
    if total > 5900:
        raise ValueError(
            "Embed title, description, and fields must total at most 5,900 characters."
        )
    bindings = raw.get("reaction_roles", [])
    if not isinstance(bindings, list) or len(bindings) > 100:
        raise ValueError("Use up to 100 reaction role mappings.")
    seen = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError("Invalid reaction role.")
        ch = channel(binding.get("channel_id"), "Reaction role channel")
        msg = snowflake(binding.get("message_id"), "Message")
        rid = role(binding.get("role_id"), True)
        if not ch or not rid:
            raise ValueError("Each reaction role needs a channel and role.")
        if rid == result["staff_role_id"]:
            raise ValueError("The staff role cannot be self-assigned.")
        emoji = text(binding.get("emoji"), "Emoji", 100, 1).strip()
        # Accept one Unicode grapheme (variation selectors/ZWJ allowed), or a Discord custom emoji.
        if " " in emoji or (
            not emoji.startswith("<") and all(ord(c) < 128 for c in emoji)
        ):
            raise ValueError("Use a Unicode emoji or <:name:id>.")
        if emoji.startswith("<") and not re.fullmatch(r"<a?:[A-Za-z0-9_]+:\d+>", emoji):
            raise ValueError("Invalid custom emoji.")
        key = (ch, msg, emoji)
        if key in seen:
            raise ValueError("Duplicate reaction mapping for this message and emoji.")
        seen.add(key)
        result["reaction_roles"].append(
            {"channel_id": ch, "message_id": msg, "emoji": emoji, "role_id": rid}
        )
    return result
