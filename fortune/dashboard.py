"""Same-process dashboard: opaque sessions, OAuth state, CSRF, live guild checks."""

import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse
import aiohttp
from aiohttp import web
import discord
from discord.ext import commands
from . import settings
from .branding import embed
from .configuration import validate_config
from .community import greet_message
from .permissions import admin, PERMISSIONS
from .staff import set_staff, remove_staff

log = logging.getLogger(__name__)
API = "https://discord.com/api/v10"
WEB = Path(__file__).parent / "web"
SESSION = web.RequestKey("session", dict)


def json_safe(value):
    # Discord snowflakes must never be passed through a JavaScript Number.
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        return str(value)
    return value


class Dashboard:
    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}
        self.states = {}
        self.runner = None
        self.app = web.Application(
            middlewares=[self.security, self.authenticate], client_max_size=128 * 1024
        )
        self.app.add_routes(
            [
                web.get("/", self.index),
                web.get("/app.js", self.static),
                web.get("/style.css", self.static),
                web.get("/health", self.health),
                web.get("/auth/login", self.login),
                web.get("/auth/callback", self.callback),
                web.post("/auth/logout", self.logout),
                web.get("/api/me", self.me),
                web.get("/api/guilds", self.guilds),
                web.get("/api/guilds/{guild_id}/config", self.get_config),
                web.put("/api/guilds/{guild_id}/config", self.save_config),
                web.get("/api/guilds/{guild_id}/members", self.members),
                web.get("/api/guilds/{guild_id}/staff", self.staff_list),
                web.put("/api/guilds/{guild_id}/staff/{user_id}", self.staff_save),
                web.delete("/api/guilds/{guild_id}/staff/{user_id}", self.staff_remove),
                web.post("/api/guilds/{guild_id}/embeds/send", self.send_embed),
                web.post("/api/guilds/{guild_id}/greet/test", self.greet_test),
                web.post("/api/guilds/{guild_id}/panels/publish", self.publish_panel),
                web.post(
                    "/api/guilds/{guild_id}/reactions/publish", self.publish_reactions
                ),
                web.post("/api/guilds/{guild_id}/reactions/seed", self.seed_reactions),
                web.get("/api/guilds/{guild_id}/tickets", self.tickets),
                web.get(
                    "/api/guilds/{guild_id}/transcripts/{ticket_id}", self.transcript
                ),
                web.get("/api/guilds/{guild_id}/audit", self.audit),
            ]
        )

    @web.middleware
    async def security(self, request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc
        except (ValueError, commands.CommandError) as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        except discord.Forbidden:
            response = web.json_response(
                {
                    "error": "Discord denied this action. Check bot permissions and role order."
                },
                status=403,
            )
        except discord.NotFound:
            response = web.json_response(
                {
                    "error": "The Discord member, role, channel, or message no longer exists."
                },
                status=404,
            )
        except discord.HTTPException:
            response = web.json_response(
                {"error": "Discord could not complete the request. Please retry."},
                status=502,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            response = web.json_response(
                {"error": "Discord is temporarily unreachable. Please retry."},
                status=502,
            )
        except Exception:
            log.exception("Dashboard request failed")
            response = web.json_response(
                {"error": "An internal error occurred. Check the server logs."},
                status=500,
            )
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if isinstance(response, web.HTTPException):
            raise response
        return response

    def prune(self):
        now = time.time()
        self.sessions = {
            key: value for key, value in self.sessions.items() if value["expires"] > now
        }
        self.states = {
            key: value for key, value in self.states.items() if value["expires"] > now
        }

    @web.middleware
    async def authenticate(self, request, handler):
        self.prune()
        if request.path.startswith("/api/") or request.path == "/auth/logout":
            session = self.sessions.get(request.cookies.get("fm_session", ""))
            if not session:
                raise web.HTTPUnauthorized(text="Sign in with Discord.")
            request[SESSION] = session
            session["requests"] = [
                t for t in session["requests"] if t > time.time() - 60
            ]
            if len(session["requests"]) >= 120:
                raise web.HTTPTooManyRequests(
                    text="Please wait before making more requests."
                )
            session["requests"].append(time.time())
            if request.method not in ("GET", "HEAD"):
                if request.headers.get("Origin") != settings.DASHBOARD_URL:
                    raise web.HTTPForbidden(text="Invalid request origin.")
                if not secrets.compare_digest(
                    request.headers.get("X-CSRF-Token", ""), session["csrf"]
                ):
                    raise web.HTTPForbidden(text="Invalid CSRF token. Reload the page.")
                if request.content_type != "application/json":
                    raise web.HTTPUnsupportedMediaType(text="Use application/json.")
        return await handler(request)

    async def discord_request(self, method, path, token=None, data=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with self.bot.session.request(
            method, API + path, headers=headers, data=data
        ) as response:
            if response.status == 401:
                raise web.HTTPUnauthorized(text="Discord login expired. Sign in again.")
            if response.status == 429:
                raise web.HTTPTooManyRequests(
                    text="Discord rate limit reached. Try again shortly."
                )
            if response.status >= 400:
                raise web.HTTPBadGateway(text="Discord rejected the login request.")
            return await response.json()

    async def index(self, request):
        return web.FileResponse(WEB / "index.html")

    async def static(self, request):
        return web.FileResponse(WEB / request.path.lstrip("/"))

    async def health(self, request):
        return web.json_response(
            {"service": "FortuneManager", "ready": self.bot.is_ready()},
            status=200 if self.bot.is_ready() else 503,
        )

    async def login(self, request):
        if not settings.CLIENT_ID or not settings.CLIENT_SECRET:
            raise web.HTTPServiceUnavailable(
                text="Configure DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET in .env to enable login."
            )
        if len(self.states) > 5000:
            raise web.HTTPTooManyRequests()
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        self.states[state] = {"binding": binding, "expires": time.time() + 600}
        params = {
            "client_id": settings.CLIENT_ID,
            "redirect_uri": settings.DASHBOARD_URL + "/auth/callback",
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
            "prompt": "none",
        }
        response = web.HTTPFound(
            "https://discord.com/oauth2/authorize?" + urlencode(params)
        )
        response.set_cookie(
            "fm_oauth",
            binding,
            httponly=True,
            secure=settings.DASHBOARD_URL.startswith("https:"),
            samesite="Lax",
            max_age=600,
            path="/auth",
        )
        return response

    async def callback(self, request):
        pending = self.states.pop(request.query.get("state", ""), None)
        if not pending or not secrets.compare_digest(
            pending["binding"], request.cookies.get("fm_oauth", "")
        ):
            raise web.HTTPForbidden(
                text="Invalid or expired login state. Start login again."
            )
        if request.query.get("error") or not request.query.get("code"):
            raise web.HTTPBadRequest(text="Discord login was cancelled.")
        token = await self.discord_request(
            "POST",
            "/oauth2/token",
            data={
                "client_id": settings.CLIENT_ID,
                "client_secret": settings.CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": request.query["code"],
                "redirect_uri": settings.DASHBOARD_URL + "/auth/callback",
            },
        )
        user = await self.discord_request("GET", "/users/@me", token["access_token"])
        sid = secrets.token_urlsafe(48)
        self.sessions.pop(request.cookies.get("fm_session", ""), None)
        self.sessions[sid] = {
            "user": {
                "id": user["id"],
                "name": user.get("global_name") or user["username"],
            },
            "token": token["access_token"],
            "expires": time.time() + min(int(token.get("expires_in", 3600)), 8 * 3600),
            "csrf": secrets.token_urlsafe(32),
            "requests": [],
        }
        response = web.HTTPFound("/")
        response.set_cookie(
            "fm_session",
            sid,
            httponly=True,
            secure=settings.DASHBOARD_URL.startswith("https:"),
            samesite="Lax",
            max_age=8 * 3600,
            path="/",
        )
        response.del_cookie("fm_oauth", path="/auth")
        return response

    async def logout(self, request):
        self.sessions.pop(request.cookies.get("fm_session", ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie("fm_session", path="/")
        return response

    async def me(self, request):
        s = request[SESSION]
        return web.json_response(
            {
                "user": s["user"],
                "csrf": s["csrf"],
                "ready": self.bot.is_ready(),
                "logo": settings.LOGO_URL,
                "banner": settings.BANNER_URL,
            }
        )

    async def guilds(self, request):
        result = []
        after = "0"
        while True:
            guilds = await self.discord_request(
                "GET",
                f"/users/@me/guilds?limit=200&after={after}",
                request[SESSION]["token"],
            )
            for g in guilds:
                if g.get("owner") or int(g["permissions"]) & (0x8 | 0x20):
                    result.append(
                        {
                            "id": g["id"],
                            "name": g["name"],
                            "installed": self.bot.get_guild(int(g["id"])) is not None,
                            "icon": f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png"
                            if g.get("icon")
                            else None,
                        }
                    )
            if len(guilds) < 200:
                break
            after = max((g["id"] for g in guilds), key=int)
        permissions = discord.Permissions(
            kick_members=True,
            ban_members=True,
            manage_channels=True,
            manage_roles=True,
            moderate_members=True,
            manage_messages=True,
            manage_nicknames=True,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            add_reactions=True,
        )
        return web.json_response(
            {
                "guilds": result,
                "invite": f"https://discord.com/oauth2/authorize?client_id={settings.CLIENT_ID}&scope=bot&permissions={permissions.value}",
            }
        )

    async def context(self, request, administrator=False):
        if not self.bot.is_ready():
            raise web.HTTPServiceUnavailable(
                text="The bot is connecting to Discord. Retry shortly."
            )
        try:
            gid = int(request.match_info["guild_id"])
        except ValueError:
            raise web.HTTPNotFound()
        guild = self.bot.get_guild(gid)
        if not guild:
            raise web.HTTPNotFound(text="The bot is not in this server.")
        try:
            member = await guild.fetch_member(int(request[SESSION]["user"]["id"]))
        except discord.NotFound:
            raise web.HTTPForbidden(text="You are not a member of this server.")
        if administrator and not admin(member):
            raise web.HTTPForbidden(text="Administrator permission required.")
        if not admin(member) and not member.guild_permissions.manage_guild:
            raise web.HTTPForbidden(text="Manage Server permission required.")
        return guild, member

    async def body(self, request):
        try:
            value = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Invalid JSON body.")
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    async def get_config(self, request):
        guild, member = await self.context(request)
        config, version = await self.bot.store.config(guild.id)
        channels = [
            {
                "id": str(c.id),
                "name": c.name,
                "type": "category"
                if isinstance(c, discord.CategoryChannel)
                else "text",
            }
            for c in guild.channels
            if isinstance(c, (discord.TextChannel, discord.CategoryChannel))
            and c.permissions_for(member).view_channel
        ]
        roles = [
            {"id": str(r.id), "name": r.name}
            for r in reversed(guild.roles)
            if not r.is_default() and not r.managed
        ]
        return web.json_response(
            {
                "config": config,
                "version": version,
                "channels": channels,
                "roles": roles,
                "admin": admin(member),
                "guild": {
                    "id": str(guild.id),
                    "name": guild.name,
                    "members": guild.member_count,
                },
                "permissions": {k: v[0] for k, v in PERMISSIONS.items()},
            }
        )

    async def save_config(self, request):
        guild, member = await self.context(request)
        body = await self.body(request)
        if type(body.get("version")) is not int:
            raise ValueError("Missing settings version. Reload the page.")
        async with self.bot.guild_locks[guild.id]:
            previous, current_version = await self.bot.store.config(guild.id)
            if body["version"] != current_version:
                raise ValueError(
                    "Settings changed in another tab. Reload before saving."
                )
            config = validate_config(body.get("config"), guild, member, previous)
            old_roles = set(previous["ticket"]["support_role_ids"])
            new_roles = set(config["ticket"]["support_role_ids"])
            changed = []
            try:
                if old_roles != new_roles:
                    for ticket in await self.bot.store.rows(
                        "SELECT channel_id FROM tickets WHERE guild_id=? AND status IN ('open','closed')",
                        (guild.id,),
                    ):
                        channel = guild.get_channel(ticket["channel_id"])
                        if not channel:
                            continue
                        for rid in old_roles ^ new_roles:
                            role = guild.get_role(int(rid))
                            if not role:
                                continue
                            previous_overwrite = channel.overwrites_for(role)
                            overwrite = (
                                discord.PermissionOverwrite(
                                    view_channel=True,
                                    send_messages=True,
                                    read_message_history=True,
                                )
                                if rid in new_roles
                                else None
                            )
                            await channel.set_permissions(
                                role,
                                overwrite=overwrite,
                                reason="Ticket support settings updated",
                            )
                            changed.append((channel, role, previous_overwrite))
                version = await self.bot.store.save_config(
                    guild.id, config, body["version"]
                )
            except Exception:
                failures = []
                for channel, role, overwrite in reversed(changed):
                    try:
                        await channel.set_permissions(
                            role,
                            overwrite=None if overwrite.is_empty() else overwrite,
                            reason="Ticket settings rollback",
                        )
                    except discord.HTTPException:
                        failures.append(channel.name)
                if failures:
                    raise ValueError(
                        "Settings were not saved. Restore support-role access manually in: "
                        + ", ".join(failures)
                    )
                raise
            await self.bot.store.audit(
                guild.id, member.id, "settings.save", f"Version {version}"
            )
        return web.json_response(
            {"ok": True, "version": version, "config": config, "warnings": []}
        )

    async def members(self, request):
        guild, member = await self.context(request, True)
        q = request.query.get("q", "").strip()[:100]
        if q.isdigit():
            try:
                members = [await guild.fetch_member(int(q))]
            except discord.NotFound:
                members = []
        else:
            members = [
                m
                for m in guild.members
                if q.casefold() in m.display_name.casefold()
                or q.casefold() in m.name.casefold()
            ][:25]
        return web.json_response(
            {
                "members": [
                    {"id": str(m.id), "name": m.display_name}
                    for m in members
                    if not m.bot
                ]
            }
        )

    async def staff_list(self, request):
        guild, member = await self.context(request)
        rows = await self.bot.store.rows(
            "SELECT * FROM staff WHERE guild_id=?", (guild.id,)
        )
        for row in rows:
            row["permissions"] = json.loads(row["permissions"])
            target = guild.get_member(row["user_id"])
            row["name"] = target.display_name if target else str(row["user_id"])
        return web.json_response(json_safe({"staff": rows}))

    async def staff_save(self, request):
        guild, actor = await self.context(request, True)
        body = await self.body(request)
        if not isinstance(body.get("permissions"), list) or not all(
            isinstance(p, str) for p in body["permissions"]
        ):
            raise ValueError("Select permissions.")
        target = await guild.fetch_member(int(request.match_info["user_id"]))
        await set_staff(self.bot, guild, actor, target, set(body["permissions"]))
        return web.json_response({"ok": True})

    async def staff_remove(self, request):
        guild, actor = await self.context(request, True)
        target = await guild.fetch_member(int(request.match_info["user_id"]))
        await remove_staff(self.bot, guild, actor, target)
        return web.json_response({"ok": True})

    def destination(self, guild, member, value):
        if not isinstance(value, str) or not value.isdigit():
            raise ValueError("Select a channel.")
        channel = guild.get_channel(int(value))
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Select a text channel in this server.")
        if not channel.permissions_for(member).view_channel:
            raise web.HTTPForbidden(text="You cannot access this channel.")
        if not channel.permissions_for(guild.me).send_messages:
            raise ValueError("The bot cannot send messages in that channel.")
        return channel

    async def greet_test(self, request):
        guild, member = await self.context(request)
        config, _ = await self.bot.store.config(guild.id)
        channel = self.destination(guild, member, config["greet"]["channel_id"])
        message = await greet_message(channel, member, config["greet"])
        return web.json_response({"ok": True, "url": message.jump_url})

    async def send_embed(self, request):
        guild, member = await self.context(request)
        config, _ = await self.bot.store.config(guild.id)
        draft = config["embeds"]
        channel = self.destination(guild, member, draft["channel_id"])
        message = embed(
            draft["title"] or None,
            draft["description"] or None,
            color=int(draft["color"][1:], 16),
        )
        if draft["image"]:
            message.set_image(url=draft["image"])
        for field in draft["fields"]:
            message.add_field(**field)
        sent = await channel.send(
            content=draft["content"] or None,
            embed=message,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.bot.store.audit(guild.id, member.id, "embed.send", sent.id)
        return web.json_response({"ok": True, "url": sent.jump_url})

    async def publish_panel(self, request):
        guild, member = await self.context(request)
        body = await self.body(request)
        config, _ = await self.bot.store.config(guild.id)
        if not config["ticket"]["enabled"]:
            raise ValueError("Enable tickets and save settings first.")
        panel = next(
            (p for p in config["ticket"]["panels"] if p["id"] == body.get("panel_id")),
            None,
        )
        if not panel:
            raise ValueError("Save the ticket panel before publishing.")
        channel = self.destination(guild, member, body.get("channel_id"))
        message = await self.bot.get_cog("Tickets").publish_panel(guild, channel, panel)
        await self.bot.store.audit(
            guild.id, member.id, "ticket.panel.publish", message.id
        )
        return web.json_response({"ok": True, "url": message.jump_url})

    async def publish_reactions(self, request):
        guild, member = await self.context(request)
        body = await self.body(request)
        channel = self.destination(guild, member, body.get("channel_id"))
        async with self.bot.guild_locks[guild.id]:
            config, version = await self.bot.store.config(guild.id)
            config = validate_config(config, guild, member, config)
            mappings = [
                r
                for r in config["reaction_roles"]
                if r["channel_id"] == str(channel.id) and not r["message_id"]
            ]
            if not 1 <= len(mappings) <= 20:
                raise ValueError(
                    "Save 1–20 mappings with an empty message ID for the chosen channel."
                )
            message = await channel.send(
                embed=embed(
                    "Choose your roles",
                    "React to receive a role. Remove your reaction to remove it.\n\n"
                    + "\n".join(f"{r['emoji']}  <@&{r['role_id']}>" for r in mappings),
                )
            )
            try:
                for row in mappings:
                    await message.add_reaction(row["emoji"])
                    row["message_id"] = str(message.id)
                version = await self.bot.store.save_config(guild.id, config, version)
            except Exception:
                await message.delete()
                raise
        return web.json_response(
            {"ok": True, "url": message.jump_url, "config": config, "version": version}
        )

    async def seed_reactions(self, request):
        guild, member = await self.context(request)
        config, _ = await self.bot.store.config(guild.id)
        config = validate_config(config, guild, member, config)
        done = 0
        for row in config["reaction_roles"]:
            if not row["message_id"]:
                continue
            channel = self.destination(guild, member, row["channel_id"])
            message = await channel.fetch_message(int(row["message_id"]))
            await message.add_reaction(row["emoji"])
            done += 1
        return web.json_response({"ok": True, "count": done})

    async def tickets(self, request):
        guild, member = await self.context(request)
        rows = await self.bot.store.rows(
            "SELECT * FROM tickets WHERE guild_id=? ORDER BY id DESC LIMIT 100",
            (guild.id,),
        )
        return web.json_response(json_safe({"tickets": rows}))

    async def transcript(self, request):
        guild, member = await self.context(request)
        tid = int(request.match_info["ticket_id"])
        row = await self.bot.store.one(
            "SELECT id FROM tickets WHERE id=? AND guild_id=?", (tid, guild.id)
        )
        if not row:
            raise web.HTTPNotFound()
        path = settings.DATA_DIR / "transcripts" / str(guild.id) / f"ticket-{tid}.txt"
        if not path.is_file():
            raise web.HTTPNotFound(
                text="No saved transcript yet. Close the ticket or use its Transcript button."
            )
        return web.FileResponse(
            path,
            headers={"Content-Disposition": f'attachment; filename="ticket-{tid}.txt"'},
        )

    async def audit(self, request):
        guild, member = await self.context(request)
        rows = await self.bot.store.rows(
            "SELECT * FROM audit WHERE guild_id=? ORDER BY id DESC LIMIT 100",
            (guild.id,),
        )
        return web.json_response(json_safe({"events": rows}))

    async def start(self):
        parsed = urlparse(settings.DASHBOARD_URL)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "DASHBOARD_URL must be an origin, e.g. https://bot.example.com"
            )
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError("Public dashboard URLs must use HTTPS.")
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(
            self.runner, settings.DASHBOARD_HOST, settings.DASHBOARD_PORT
        ).start()
        log.info(
            "Dashboard listening on %s:%s",
            settings.DASHBOARD_HOST,
            settings.DASHBOARD_PORT,
        )

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
