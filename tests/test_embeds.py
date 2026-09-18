import copy
import pytest
from fortune.configuration import validate_config
from fortune.store import DEFAULT_CONFIG
from test_core import world
from test_dashboard import webclient


def test_embed_character_budget_rejected():
    w = world()
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["embeds"]["description"] = "x" * 4000
    config["embeds"]["fields"] = [
        {"name": "Field", "value": "x" * 1000, "inline": False} for _ in range(2)
    ]
    with pytest.raises(ValueError, match="5,900"):
        validate_config(config, w.guild, w.owner, DEFAULT_CONFIG)


@pytest.mark.asyncio
async def test_saved_embed_is_sent_with_fields_and_no_mentions(tmp_path, monkeypatch):
    client, d, w, h = await webclient(tmp_path, monkeypatch)
    config, v = await d.bot.store.config(100)
    config["embeds"].update(
        channel_id="300",
        title="Announcement",
        fields=[{"name": "Event", "value": "Tomorrow", "inline": True}],
    )
    await d.bot.store.save_config(100, config, v)
    async with client:
        r = await client.post("/api/guilds/100/embeds/send", json={}, headers=h)
        assert r.status == 200
    kwargs = w.channel.send.call_args.kwargs
    assert (
        kwargs["embed"].title == "Announcement"
        and kwargs["embed"].fields[0].value == "Tomorrow"
    )
    assert (
        kwargs["allowed_mentions"].everyone is False
        and kwargs["allowed_mentions"].users is False
    )
