import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from population import mapnav, render
from population.game import Game
from population.store import Store


@pytest.fixture
def game(tmp_path):
    g = Game(Store(str(tmp_path / "g.db")))
    g.open_signup("feudalism")
    for i, name in enumerate(["Aldmere", "Brask", "Corvain", "Dunmore"]):
        g.join(100 + i, name)
    g.start(6, seed=5)
    return g


def test_shift_moves_and_stops_at_the_edge(game):
    w = game.world
    cap = w["nations"][0]["capital"]
    assert mapnav.shift(w, cap, "e", 1) != cap
    assert mapnav.shift(w, "0,0", "e", 50) == "6,0"       # too far: goes as far as the map allows
    assert mapnav.shift(w, "6,0", "e") == "6,0"           # right at the edge


def test_options_lead_with_claimable_tiles(game):
    w, rules = game.world, game.rules
    cap = w["nations"][0]["capital"]
    opts = mapnav.pick_options(w, rules, 0, cap, 3)
    assert 0 < len(opts) <= 25
    first_key, first_text = opts[0]
    assert first_key in mapnav.claimable(w, 0) and "claim" in first_text
    assert all(w["tiles"][k]["terrain"] != "water" for k, _ in opts)


def test_card_for_claimable_own_and_water(game):
    w, rules = game.world, game.rules
    free = sorted(mapnav.claimable(w, 0))[0]
    card = mapnav.tile_card(w, rules, 0, free)
    assert card["can_claim"] and "Claim cost" in card["text"] and card["builds"] == []
    mine = next(k for k in w["tiles"] if w["tiles"][k]["owner"] == 0 and w["tiles"][k]["building"] is None)
    card = mapnav.tile_card(w, rules, 0, mine)
    assert set(card["builds"]) == set(rules["buildings"]) and not card["can_claim"]
    cap = mapnav.tile_card(w, rules, 0, w["nations"][0]["capital"])
    assert cap["builds"] == []


def test_fog_hides_tiles_in_card(game):
    game.world["settings"]["fog"] = True
    far = next(k for k, t in game.world["tiles"].items() if t["owner"] == 3)
    assert "fogged" in mapnav.tile_card(game.world, game.rules, 0, far)["text"]


def test_render_options(game):
    w, rules = game.world, game.rules
    cap = w["nations"][0]["capital"]
    free = sorted(mapnav.claimable(w, 0))[0]
    for kw in ({}, {"icons": True}, {"icons": False, "mark": free}, {"buildings": False}, {"people": True}):
        data = render.render(w, rules, nid=0, center=cap, radius=3, tile=48, legend=False, coords="claim",
                             tint=True, claim=True, tidy=True, **kw)
        assert data[:4] == b"\x89PNG"


# ---- the Discord view, driven with pretend clicks ----

@pytest.fixture
def bot(tmp_path, monkeypatch, game):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("discord_bot")
    module = importlib.reload(module)
    module.game = game
    return module


def click(uid):
    i = SimpleNamespace(user=SimpleNamespace(id=uid), response=MagicMock(), edit_original_response=AsyncMock(),
                        command=None, followup=MagicMock())
    i.response.defer, i.response.send_message, i.response.edit_message = AsyncMock(), AsyncMock(), AsyncMock()
    i.followup.send = AsyncMock()
    return i


def find(view, label=None, kind=None):
    """A button by its label, or by the start of it (action buttons carry their cost)."""
    for c in view.children:
        text = getattr(c, "label", None)
        if label is not None and text is not None and (text == label or text.startswith(label + " (")
                                                      or text.startswith(label + ":")):
            return c
        if kind is not None and type(c).__name__ == kind and label is None:
            return c


def selects(view):
    return [c for c in view.children if type(c).__name__ == "Select"]


def test_view_full_walkthrough(bot, game):
    async def go():
        view = bot.MapView(100, 0, 3)
        assert find(view, "Claim") is None and len(view.children) == 11   # 9 buttons + layers + tile menus
        # pan, zoom, recentre all redraw the picture
        for label in ("\u2192", "Zoom out", "Center"):
            i = click(100)
            await find(view, label).callback(i)
            i.edit_original_response.assert_awaited()
            assert i.edit_original_response.await_args.kwargs["attachments"][0].filename == "map.png"
        assert view.radius == 4
        # layers: turn on icons and housing numbers, hide buildings
        layers, tiles = selects(view)
        layers._values = ["icons", "people"]
        i = click(100)
        await view.set_layers(layers)(i)
        layers._values = ["production"]
        await view.set_layers(layers)(click(100))
        assert view.production() and all(isinstance(v, str) for v in view.production().values())
        assert "workers" in view.text()
        layers._values = ["icons", "people"]
        await view.set_layers(layers)(click(100))
        assert view.layers == {"icons": True, "buildings": False, "people": True, "production": False, "coords": False}
        assert "Housing:" in i.edit_original_response.await_args.kwargs["content"]
        layers._values = ["coords"]
        await view.set_layers(layers)(click(100))
        assert view.layers["coords"] and view.image()
        layers._values = ["icons", "buildings", "people"]
        await view.set_layers(layers)(click(100))
        # pick a claimable tile, claim it
        free = mapnav.pick_options(game.world, game.rules, 0, view.center, view.radius)[0][0]
        tiles._values = [free]
        await view.picker(tiles)(click(100))
        assert view.selected == free and find(view, "Claim") is not None
        i = click(100)
        await find(view, "Claim").callback(i)
        assert "queued" in i.response.send_message.await_args.args[0]
        assert any(o["type"] == "claim" and o["tile"] == free for o in game.world["orders"])
        # pick one of your empty tiles, build
        mine = next(k for k in game.world["tiles"] if game.world["tiles"][k]["owner"] == 0
                    and game.world["tiles"][k]["building"] is None)
        tiles = selects(view)[1]
        tiles._values = [mine]
        await view.picker(tiles)(click(100))
        i = click(100)
        await find(view, "Home").callback(i)
        assert "Build" in i.response.send_message.await_args.args[0]
        # send an army: the button swaps the action row for an army menu
        await find(view, "Send army").callback(click(100))
        army_menu = selects(view)[-1]
        assert army_menu.placeholder.startswith("Which army")
        army_menu._values = [str(game.world["armies"][0]["id"])]
        i = click(100)
        await view.send_army(army_menu)(i)
        assert "march" in i.response.send_message.await_args.args[0]
        assert view.choosing_army is False
        # someone else can't use it
        assert await view.interaction_check(click(999)) is False

    asyncio.run(go())


def test_failed_order_is_reported_not_raised(bot, game):
    async def go():
        view = bot.MapView(100, 0, 3)
        view.selected = "99,99"
        i = click(999)        # not a player
        await view.claim_here(i)
        assert "Can't do that" in i.response.send_message.await_args.args[0]
    asyncio.run(go())


def test_info_view_tabs_and_priority_buttons(bot, game):
    async def go():
        view = bot.InfoView(100, game)
        assert [c.label for c in view.children] == ["Overview", "Economy", "Land", "Military"]
        assert view.embed().fields[0].name == "People"
        i = click(100)
        await find(view, "Economy").callback(i)
        i.response.edit_message.assert_awaited()
        assert view.tab == "economy" and len(view.children) == 4 + 1 + 5
        start = game.world["nations"][0]["priorities"]["food"]            # 5 by default: "+" is greyed out
        assert find(view, "+").disabled and not find(view, "-").disabled
        await find(view, "-").callback(click(100))
        assert game.world["nations"][0]["priorities"]["food"] == start - 1
        assert not find(view, "+").disabled
        await find(view, "Off").callback(click(100))
        assert game.world["nations"][0]["priorities"]["food"] == 0 and find(view, "Off").disabled
        await find(view, "Max").callback(click(100))
        assert game.world["nations"][0]["priorities"]["food"] == 5 and find(view, "Max").disabled
        await find(view, "-").callback(click(100))
        menu = selects(view)[0]
        menu._values = ["wood"]
        await view.pick(menu)(click(100))
        assert view.resource == "wood" and any(getattr(c, "label", None) == "wood: 3/5" for c in view.children)
        assert view.embed().title.startswith("Aldmere")
        for tab in ("Land", "Military", "Overview"):
            await find(view, tab).callback(click(100))
        assert await view.interaction_check(click(999)) is False
    asyncio.run(go())


def test_help_view_has_every_page(bot, game):
    async def go():
        view = bot.HelpView(100, game)
        assert len(view.children) == 5
        for button in view.children:
            i = click(100)
            await button.callback(i)
            text = i.response.edit_message.await_args.kwargs["content"]
            assert 0 < len(text) < 2000
    asyncio.run(go())


def test_practice_game_takes_over_commands_until_stopped(bot, game):
    from population import advice

    async def go():
        uid = 4242
        i = click(uid)
        await bot.tutorial_start.callback(i)
        assert "Practice game started" in i.response.send_message.await_args.args[0]
        practice = bot.game_for(uid)
        assert practice is not game and practice.world["tick"] == 0
        i = click(uid)
        await bot.status.callback(i)
        sent = i.response.send_message.await_args
        assert sent.args[0].startswith("**[Practice game]**") and sent.kwargs["ephemeral"] is True
        tile = sorted(advice.free_frontier(practice.world, 0))[0]
        i = click(uid)
        await bot.claim.callback(i, tile)
        assert "queued" in i.response.send_message.await_args.args[0]
        assert not any(o["type"] == "claim" for o in game.world["orders"])      # the real game is untouched
        i = click(uid)
        await bot.tutorial_night.callback(i)
        assert practice.world["tick"] == 1 and "night 1" in i.response.send_message.await_args.args[0]
        i = click(uid)
        await bot.tutorial_stop.callback(i)
        assert bot.game_for(uid) is game
        i = click(uid)
        await bot.tutorial_night.callback(i)
        assert "no practice game" in i.response.send_message.await_args.args[0]
    asyncio.run(go())


def test_join_is_public_and_welcome_is_private(bot, tmp_path):
    from population.game import Game
    from population.store import Store

    async def go():
        g = Game(Store(str(tmp_path / "j.db")))
        g.open_signup("feudalism")
        bot.game = g
        i = click(7)
        await bot.join.callback(i, "Newcomer")
        public = i.response.send_message.await_args
        assert "Newcomer" in public.args[0] and "ephemeral" not in public.kwargs
        assert "ephemeral" in i.followup.send.await_args.kwargs and i.followup.send.await_args.kwargs["ephemeral"] is True
    asyncio.run(go())


def test_nightly_tick_posts_report_log_and_private_reports(bot, game, monkeypatch):
    class Channel:
        def __init__(self):
            self.sent, self.guild = [], None

        async def send(self, text, **kwargs):
            self.sent.append(text)

    announce, log = Channel(), Channel()
    channels = {1: announce, 2: log}
    monkeypatch.setattr(bot.client, "get_channel", lambda i: channels.get(i))
    monkeypatch.setattr(bot, "sync_roles", AsyncMock())
    sent_dms = []

    async def fake_dm(uid, text):
        sent_dms.append((uid, text))

    monkeypatch.setattr(bot, "dm", fake_dm)
    bot.store.set("announce_channel", 1)
    bot.store.set("log_channel", 2)

    async def go():
        await bot.do_tick()
    asyncio.run(go())
    assert "Tick 1" in announce.sent[0] and "/report" in announce.sent[0] and "To win" in announce.sent[0]
    assert log.sent and log.sent[0].startswith("**Night 1**")
    assert sorted(uid for uid, _ in sent_dms) == ["100", "101", "102", "103"]
    assert all("night 1" in text for _, text in sent_dms)
    # with DMs switched off nobody is messaged
    sent_dms.clear()
    monkeypatch.setattr(bot, "DM_REPORTS", False)
    asyncio.run(go())
    assert sent_dms == []


def test_trade_board_buttons(bot, game):
    async def go():
        for nid in (0, 1):
            t = next(k for k, v in game.world["tiles"].items() if v["owner"] == nid and v["building"] is None)
            game.world["tiles"][t]["building"] = "market"
        tid, _ = game.make_offer(100, "", "wood 5", "stone 2")
        o = game.world["trades"][0]
        assert "Trade board" == bot.offer_embed(game, o).title
        assert bot.TradeButton.__discord_ui_compiled_template__.match("trade:accept:%d" % tid)
        view = bot.offer_view(tid)
        assert [c.item.label for c in view.children] == ["Accept", "Decline"]
        # the sender cannot accept their own offer
        i = click(100)
        await view.children[0].callback(i)
        assert "own offer" in i.response.send_message.await_args.args[0]
        # another market owner can
        i = click(101)
        await view.children[0].callback(i)
        i.response.edit_message.assert_awaited()
        assert o["status"] == "accepted" and o["to"] == 1
        assert "Accepted by" in bot.offer_embed(game, o).description
    asyncio.run(go())


def test_rules_panel(bot, game):
    async def go():
        view = bot.RulesView(100, game)
        menu = selects(view)[0]
        assert len(menu.options) == len(view.rows()) and "Fog of war" in view.text()
        await find(view, "Turn on").callback(click(100))           # fog: queue a switch
        assert game.pending_rules == {"fog": True} and "next tick" in view.text()
        await find(view, "Undo change").callback(click(100))
        assert game.pending_rules == {}
        await find(view, "Land").callback(click(100))
        menu = selects(view)[0]
        menu._values = ["scarcity"]
        await view.pick(menu)(click(100))
        await find(view, "+50").callback(click(100))
        assert game.pending_rules == {"scarcity": game.rules["claim"]["scarcity_percent"] + 50}
        assert await view.interaction_check(click(999)) is False
    asyncio.run(go())


def test_rules_panel_before_the_game_starts(bot, tmp_path):
    from population.game import Game
    from population.store import Store

    async def go():
        g = Game(Store(str(tmp_path / "p.db")))
        try:
            bot.RulesView(100, g)
            assert False, "no game set up yet"
        except bot.GameError:
            pass
        g.open_signup("feudalism")
        view = bot.RulesView(100, g)
        assert "hasn't started" in view.text()
        await find(view, "Turn on").callback(click(100))
        assert g.rules["fog_of_war"] is True and g.pending_rules == {} and find(view, "Turn off") is not None
    asyncio.run(go())


def test_rules_panel_number_entry_and_reset(bot, game):
    async def go():
        view = bot.RulesView(100, game)
        menu = selects(view)[0]
        await find(view, "Land").callback(click(100))
        menu = selects(view)[0]
        menu._values = ["scarcity"]
        await view.pick(menu)(click(100))
        i = click(100)
        i.response.send_modal = AsyncMock()
        await find(view, "Type a number").callback(i)
        modal = i.response.send_modal.await_args.args[0]
        modal.number._value = "350"
        j = click(100)
        await modal.on_submit(j)
        assert game.pending_rules == {"scarcity": 350} and "350" in view.text()
        modal.number._value = "abc"
        j = click(100)
        await modal.on_submit(j)
        assert "whole number" in j.response.send_message.await_args.args[0]
        modal.number._value = "99999"
        j = click(100)
        await modal.on_submit(j)
        assert "Can't do that" in j.response.send_message.await_args.args[0]
        assert game.pending_rules == {"scarcity": 350}
    asyncio.run(go())


def test_errors_are_recorded_and_listed(bot, game, monkeypatch):
    async def go():
        monkeypatch.setattr(bot, "is_admin", lambda i: True)
        try:
            raise ValueError("boom")
        except ValueError as e:
            err = e
        i = click(7)
        i.response.is_done = lambda: False
        i.command = SimpleNamespace(qualified_name="admin announcements")
        await bot.command_failed(i, err)
        assert "boom" in i.response.send_message.await_args.args[0]
        j = click(7)
        j.response.is_done = lambda: False
        await bot.view_failed(None, j, err, None)                 # a button crash is recorded the same way
        k = click(7)
        await bot.admin_errorlog.callback(k)
        text = k.response.send_message.await_args.args[0]
        assert "/admin announcements" in text and "a button or menu" in text and "ValueError: boom" in text
        assert len(bot.store.get("error_log")) == 2
        for _ in range(25):
            bot.record_error("/x", click(7), err)
        assert len(bot.store.get("error_log")) == 20              # capped
    asyncio.run(go())


def test_unseen_channel_gets_a_clear_message(bot):
    import discord
    from discord import app_commands

    async def go():
        i = click(7)
        i.command = None
        err = app_commands.TransformerError("announcements", discord.AppCommandOptionType.channel,
                                            app_commands.transformers.BaseChannelTransformer(discord.TextChannel))
        await bot.command_failed(i, err)
        assert "View Channel" in i.response.send_message.await_args.args[0]
        assert bot.store.get("error_log", []) == []              # a user-side problem, not a bot crash
    asyncio.run(go())
