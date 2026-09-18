import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from fortune.dashboard import Dashboard, json_safe
from fortune import settings
from test_core import world, service


async def webclient(tmp_path, monkeypatch, login=True):
    bot = await service(tmp_path)
    w = world()
    bot.is_ready = lambda: True
    bot.get_guild = lambda gid: w.guild if gid == 100 else None
    dashboard = Dashboard(bot)
    client = TestClient(TestServer(dashboard.app), cookie_jar=CookieJar(unsafe=True))
    await client.start_server()
    origin = str(client.make_url("/")).rstrip("/")
    monkeypatch.setattr(settings, "DASHBOARD_URL", origin)
    if login:
        dashboard.sessions["test"] = {
            "user": {"id": "1", "name": "Admin"},
            "csrf": "csrf",
            "token": "fake",
            "expires": time.time() + 3600,
            "requests": [],
        }
        client.session.cookie_jar.update_cookies(
            {"fm_session": "test"}, response_url=client.make_url("/")
        )
    return client, dashboard, w, {"Origin": origin, "X-CSRF-Token": "csrf"}


@pytest.mark.asyncio
async def test_api_requires_login(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch, False)
    async with client:
        response = await client.get("/api/guilds/100/config")
        assert response.status == 401
        response = await client.get("/")
        assert response.status == 200
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.asyncio
async def test_csrf_and_origin_required_for_writes(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch)
    async with client:
        for headers in (
            {},
            {"Origin": h["Origin"]},
            {"Origin": "https://evil.example", "X-CSRF-Token": "csrf"},
        ):
            r = await client.put("/api/guilds/100/config", json={}, headers=headers)
            assert r.status == 403
        assert (await d.bot.store.config(100))[1] == 0


@pytest.mark.asyncio
async def test_config_api_save_roundtrip_and_conflict(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch)
    async with client:
        r = await client.get("/api/guilds/100/config")
        assert r.status == 200
        body = await r.json()
        body["config"]["prefix"] = "!"
        r = await client.put(
            "/api/guilds/100/config",
            json={"config": body["config"], "version": body["version"]},
            headers=h,
        )
        assert r.status == 200
        r = await client.get("/api/guilds/100/config")
        assert (await r.json())["config"]["prefix"] == "!"
        r = await client.put(
            "/api/guilds/100/config",
            json={"config": body["config"], "version": body["version"]},
            headers=h,
        )
        assert r.status == 400


@pytest.mark.asyncio
async def test_cross_guild_and_revoked_manager_access_denied(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch)
    async with client:
        r = await client.get("/api/guilds/999/config")
        assert r.status == 404
        d.sessions["test"]["user"]["id"] = "3"
        r = await client.get("/api/guilds/100/config")
        assert r.status == 403
        r = await client.get("/api/guilds/100/transcripts/1")
        assert r.status == 403


@pytest.mark.asyncio
async def test_manage_guild_without_admin_cannot_assign_staff(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch)
    w.staff.guild_permissions.manage_guild = True
    d.sessions["test"]["user"]["id"] = "2"
    async with client:
        r = await client.get("/api/guilds/100/config")
        assert r.status == 200
        r = await client.put(
            "/api/guilds/100/staff/3", json={"permissions": ["kick"]}, headers=h
        )
        assert r.status == 403


@pytest.mark.asyncio
async def test_oauth_state_cookie_binding_and_single_use(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch, False)
    d.states["known"] = {"binding": "correct", "expires": time.time() + 600}
    async with client:
        r = await client.get(
            "/auth/callback?state=known&code=test", allow_redirects=False
        )
        assert r.status == 403
        assert "known" not in d.states
        r = await client.get(
            "/auth/callback?state=missing&code=test", allow_redirects=False
        )
        assert r.status == 403


@pytest.mark.asyncio
async def test_oauth_success_rotates_session_and_uses_httponly_cookie(
    tmp_path, monkeypatch
):
    client, d, w, h = await webclient(tmp_path, monkeypatch, False)
    d.states["known"] = {"binding": "correct", "expires": time.time() + 600}
    client.session.cookie_jar.update_cookies(
        {"fm_oauth": "correct"}, response_url=client.make_url("/")
    )
    d.discord_request = AsyncMock(
        side_effect=[
            {"access_token": "fake-access", "expires_in": 3600},
            {"id": "1", "username": "admin"},
        ]
    )
    async with client:
        r = await client.get(
            "/auth/callback?state=known&code=test", allow_redirects=False
        )
        assert r.status == 302 and r.cookies["fm_session"]["httponly"]
        assert r.cookies["fm_session"]["samesite"] == "Lax"
        assert len(d.sessions) == 1 and "known" not in d.states
        assert "fake-access" not in str(r.headers)


@pytest.mark.asyncio
async def test_logout_invalidates_session(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch)
    async with client:
        r = await client.post("/auth/logout", json={}, headers=h)
        assert r.status == 200
        r = await client.get("/api/me")
        assert r.status == 401
        assert not d.sessions


@pytest.mark.asyncio
async def test_transcript_cannot_cross_guild(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch)
    await d.bot.store.execute(
        "INSERT INTO tickets(guild_id,owner_id,panel_id,option_id,created_at) VALUES(?,?,?,?,?)",
        (101, 1, "p", "o", "now"),
    )
    async with client:
        r = await client.get("/api/guilds/100/transcripts/1")
        assert r.status == 404


def test_snowflakes_are_serialized_without_precision_loss():
    data = json_safe(
        {"user_id": 1549018905916215469, "permissions": ["kick"], "count": 3}
    )
    assert data["user_id"] == "1549018905916215469" and data["count"] == 3


@pytest.mark.asyncio
async def test_failed_support_overwrite_does_not_commit_config(tmp_path, monkeypatch):
    import discord

    client, d, w, h = await webclient(tmp_path, monkeypatch)
    config, version = await d.bot.store.config(100)
    config["ticket"]["support_role_ids"] = ["40"]
    await d.bot.store.save_config(100, config, version)
    await d.bot.store.execute(
        "INSERT INTO tickets(guild_id,owner_id,channel_id,panel_id,option_id,status,created_at) VALUES(100,3,300,'p','o','open','now')"
    )
    config["ticket"]["support_role_ids"] = []
    w.channel.set_permissions.side_effect = discord.Forbidden(
        NS(status=403, reason="Forbidden"), "Missing permission"
    )
    async with client:
        r = await client.put(
            "/api/guilds/100/config", json={"config": config, "version": 1}, headers=h
        )
        assert r.status == 403
    assert (await d.bot.store.config(100))[0]["ticket"]["support_role_ids"] == ["40"]
