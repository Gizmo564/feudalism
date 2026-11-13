from population.hexes import distance
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from population import infopage, mapnav, render
from population.game import Game
from population.games import current_server
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
    mine = [k for k, t in game.world["tiles"].items() if t["owner"] == 0]
    far = next(k for k, t in game.world["tiles"].items()
               if t["owner"] == 3 and all(distance(k, m) > game.rules["vision_radius"] for m in mine))
    assert "fogged" in mapnav.tile_card(game.world, game.rules, 0, far)["text"]


def test_render_options(game):
    w, rules = game.world, game.rules
    cap = w["nations"][0]["capital"]
    free = sorted(mapnav.claimable(w, 0))[0]
    for kw in ({}, {"icons": True}, {"icons": False, "mark": free}, {"buildings": False}, {"people": True}):
        data = render.render(w, rules, nid=0, center=cap, radius=3, tile=48, legend=False, coords="claim",
                             tint=True, claim=True, tidy=True, **kw)
        assert data[:4] == b"\x89PNG"


SERVER = 777        # the pretend Discord server the bot tests run in


# ---- the Discord view, driven with pretend clicks ----

@pytest.fixture
def bot(tmp_path, monkeypatch, game):
    monkeypatch.setenv("GAMES_DIR", str(tmp_path / "games"))
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("discord_bot")
    module = importlib.reload(module)
    module.games.loaded[SERVER] = game          # the test game is this server's game
    token = current_server.set(SERVER)
    yield module
    current_server.reset(token)


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
        text = text.strip("\u2007") if text else text                # the controls are padded to one width
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
        assert [c.label for c in view.children][:4] == ["Overview", "Economy", "Land", "Military"]
        assert view.embed().fields[0].name == "People"
        i = click(100)
        await find(view, "Economy").callback(i)
        i.response.edit_message.assert_awaited()
        assert view.tab == "economy" and len(view.children) == 4 + 1 + 5 + 1
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
        other = next(r for r in infopage.available(game.world, game.rules, 0) if r != "food")     # the map decides what is near
        menu._values = [other]
        await view.pick(menu)(click(100))
        want = "%d/5" % game.world["nations"][0]["priorities"][other]
        assert view.resource == other and find(view, want) is not None
        sizes = {len(c.label) for c in view.children if getattr(c, "row", None) == 2}
        assert len(sizes) == 1                                          # the five controls always have the same width
        assert view.embed().title.startswith("Aldmere")
        for tab in ("Land", "Military", "Overview"):
            await find(view, tab).callback(click(100))
        assert await view.interaction_check(click(999)) is False
    asyncio.run(go())


def test_info_view_advanced_counts_mode_and_train(bot, game):
    async def go():
        view = bot.InfoView(100, game)
        assert [c.label for c in view.children][-3:] == ["Train 1", "Train 5", "Train 10"]
        await find(view, "Economy").callback(click(100))
        await find(view, "Switch to advanced mode").callback(click(100))
        assert game.world["nations"][0]["mode"] == "advanced"
        assert find(view, "Switch to basic mode") and find(view, "Auto").disabled
        await find(view, "+").callback(click(100))
        fixed = game.world["nations"][0]["assign"]["food"]
        assert fixed >= 1 and not find(view, "Auto").disabled
        await find(view, "-").callback(click(100))
        assert game.world["nations"][0]["assign"]["food"] == fixed - 1
        await find(view, "Auto").callback(click(100))
        assert game.world["nations"][0]["assign"].get("food") is None
        await find(view, "Switch to basic mode").callback(click(100))
        assert game.world["nations"][0]["mode"] == "basic"
        await find(view, "Overview").callback(click(100))
        i = click(100)
        await find(view, "Train 1").callback(i)
        assert i.response.edit_message.await_count + i.response.send_message.await_count >= 1
    asyncio.run(go())


def test_help_view_has_every_page(bot, game):
    async def go():
        view = bot.HelpView(100, game)
        assert len(view.children) == len(bot.guide.HELP_TABS) == 7
        for button in view.children:
            i = click(100)
            await button.callback(i)
            text = i.response.edit_message.await_args.kwargs["content"]
            assert 0 < len(text) < 2000
    asyncio.run(go())


def test_practice_game_comes_back_after_a_bot_restart(bot, game):
    async def go():
        uid = 5151
        await bot.tutorial_start.callback(click(uid))
        await bot.tutorial_night.callback(click(uid))
        bot.tutorials[uid].game.store.db.close()
        bot.tutorials.clear()                              # what a restart does to memory
        bot.load_practice_games()
        back = bot.tutorials[uid]
        assert back.nights == 1 and back.game.world["tick"] == 1
        await bot.tutorial_stop.callback(click(uid))
        bot.tutorials.clear()
        bot.load_practice_games()
        assert uid not in bot.tutorials                    # stopping really removes it
    asyncio.run(go())


def test_practice_game_takes_over_commands_until_stopped(bot, game):
    from population import advice

    async def go():
        uid = 4242
        i = click(uid)
        await bot.tutorial_start.callback(i)
        assert "Practice game started" in i.response.send_message.await_args.args[0]
        practice = bot.game_for(uid)
        assert practice is not bot.game and practice.world["tick"] == 0
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
        assert bot.game_for(uid) is bot.game
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
        bot.games.loaded[SERVER] = g
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

    async def fake_dm(uid, text, view=None):
        sent_dms.append((uid, text))

    monkeypatch.setattr(bot, "dm", fake_dm)
    bot.store.set("announce_channel", 1)
    bot.store.set("log_channel", 2)

    async def go():
        await bot.do_tick()
    asyncio.run(go())
    assert not sent_dms                      # nobody has opted in, so nobody is messaged
    for uid in ("100", "101", "102", "103"):
        game.set_dm(uid, True)
    announce.sent.clear(), log.sent.clear()
    game.world["tick"] = 0
    asyncio.run(go())
    assert "Tick 1" in announce.sent[0] and "/report" in announce.sent[0] and "To win" in announce.sent[0]
    assert log.sent and log.sent[0].startswith("**Night 1**")
    assert sorted(uid for uid, _ in sent_dms) == ["100", "101", "102", "103"]
    assert all("night 1" in text for _, text in sent_dms)
    # with DMs switched off nobody is messaged
    sent_dms.clear()
    bot.store.set("settings", {"dm_reports": "0"})
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
        view.advanced = True
        view.layout()
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
        view.advanced = True
        view.layout()
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


def test_global_sync_keeps_the_activity_launch_command(bot):
    sent = []

    async def fake_put(app_id, payload):
        sent.append(payload)
        return payload

    async def fake_get(app_id):
        return [{"id": "9", "name": "Launch", "description": "", "type": 4, "handler": 2, "version": "1"},
                {"id": "1", "name": "info", "type": 1}]

    bot.client.http.bulk_upsert_global_commands = fake_put
    bot.client.http.get_global_commands = fake_get
    bot.keep_launch_command()

    async def go():
        await bot.client.http.bulk_upsert_global_commands("5", payload=[{"name": "map", "type": 1}])
        await bot.client.http.bulk_upsert_global_commands("5", payload=[{"name": "x", "type": 1}, {"name": "L", "type": 4}])
    asyncio.run(go())
    assert [c["name"] for c in sent[0]] == ["map", "Launch"]
    assert "version" not in sent[0][1]
    assert [c["name"] for c in sent[1]] == ["x", "L"]          # already there: left alone


def test_play_launches_the_activity(bot, monkeypatch):
    async def go():
        i = click(7)
        i.response.launch_activity = AsyncMock()
        monkeypatch.setattr(bot, "ACTIVITY_PORT", "8080")
        await bot.play.callback(i)
        i.response.launch_activity.assert_awaited_once()
        j = click(7)
        j.response.launch_activity = AsyncMock()
        monkeypatch.setattr(bot, "ACTIVITY_PORT", "")
        await bot.play.callback(j)
        j.response.launch_activity.assert_not_awaited()
        assert "not turned on" in j.response.send_message.await_args.args[0]
    asyncio.run(go())


# ---- many servers ----

def admin_click(uid, server):
    i = click(uid)
    i.guild_id, i.guild, i.channel_id = server, SimpleNamespace(name="Server %d" % server), 5
    i.permissions = SimpleNamespace(administrator=True)
    return i


def test_admin_open_makes_a_servers_first_game_and_respects_the_cap(bot):
    from population.game import GameError
    from population.games import NoGame

    async def go():
        current_server.set(999)
        with pytest.raises(NoGame):                                   # a server with no game yet
            bot.game.phase
        i = click(1)
        await bot.tell_failure(i, NoGame("there is no game in this server yet"), NoGame("there is no game in this server yet"))
        assert "Can't do that: there is no game" in i.followup.send.await_args.args[0]
        i = admin_click(1, 999)
        await bot.admin_open.callback(i, SimpleNamespace(value="feudalism"))
        assert bot.games.get(999).phase == "signup" and bot.store.get("server_name") == "Server 999"
        assert bot.games.get(SERVER).phase == "running"              # the other server's game is untouched
        bot.games.max_games = 1                                       # server 999's game already fills it
        current_server.set(1000)
        i = admin_click(1, 1000)
        await bot.admin_open.callback(i, SimpleNamespace(value="feudalism"))
        assert "limit of 1" in i.response.send_message.await_args.args[0] and bot.games.get(1000) is None
    asyncio.run(go())


def test_settings_are_per_server_and_checked(bot):
    async def go():
        i = admin_click(1, SERVER)
        await bot.admin_settings.callback(i, SimpleNamespace(value="tick_time"), "04:30")
        assert bot.setting("tick_time") == "04:30" and "`04:30`" in i.response.send_message.await_args.args[0]
        i = admin_click(1, SERVER)
        await bot.admin_settings.callback(i, SimpleNamespace(value="tick_time"), "99:99")
        assert "Can't do that" in i.response.send_message.await_args.args[0] and bot.setting("tick_time") == "04:30"
        i = admin_click(1, SERVER)
        await bot.admin_settings.callback(i, SimpleNamespace(value="tick_minutes"), "off")
        assert bot.setting("tick_minutes") == ""
    asyncio.run(go())


def test_botstats_are_for_the_bot_owner_only(bot, monkeypatch):
    monkeypatch.setattr(bot.client, "application_info", AsyncMock(return_value=SimpleNamespace(team=None, owner=SimpleNamespace(id=5))))

    async def go():
        i = admin_click(1, SERVER)
        await bot.admin_botstats.callback(i)
        assert "Only the bot's owner" in i.response.send_message.await_args.args[0]
        i = admin_click(5, SERVER)
        await bot.admin_botstats.callback(i)
        assert "of 20 allowed" in i.response.send_message.await_args.args[0]
    asyncio.run(go())


def test_every_slash_command_fits_discords_limits(bot):
    "Discord refuses the whole command sync if any description is over 100 characters, so no new command would appear."
    def check(x, path):
        assert len(x["description"]) <= 100, (path, len(x["description"]))
        for o in x.get("options", []):
            assert len(o["description"]) <= 100, (path, o["name"], len(o["description"]))
            if o.get("type") in (1, 2):
                check(o, path + " " + o["name"])
    for c in bot.tree.get_commands():
        check(c.to_dict(bot.tree), c.name)


def test_admin_wipe_asks_first_then_wipes_unpinned_messages(bot, game, monkeypatch):
    class Channel:
        id, name = 9, "announcements"
        def __init__(self):
            self.kept = None

        async def purge(self, limit=None, check=None):
            msgs = [SimpleNamespace(pinned=False), SimpleNamespace(pinned=True), SimpleNamespace(pinned=False)]
            gone = [m for m in msgs if check(m)]
            self.kept = len(msgs) - len(gone)
            return gone

    channel = Channel()

    async def go():
        i = click(100)
        i.permissions = SimpleNamespace(administrator=True)
        await bot.admin_wipe.callback(i, channel)
        view = i.response.send_message.await_args.kwargs["view"]
        assert "can't be undone" in i.response.send_message.await_args.args[0] and channel.kept is None   # nothing yet
        press = click(100)
        await view.yes.callback(press)
        assert channel.kept == 1 and "2 messages deleted" in press.edit_original_response.await_args.kwargs["content"]
        other = click(555)
        assert not await view.interaction_check(other)                   # only the admin who asked can confirm
        stop = click(100)
        await view.no.callback(stop)
        assert "Nothing was deleted" in stop.response.edit_message.await_args.kwargs["content"]
    asyncio.run(go())


def test_night_message_lists_what_needs_an_answer_with_buttons(bot, game):
    game.pact_propose(101, "Aldmere", "no attacks until night 30")
    text, view = bot.night_message(game, 100, 7)
    assert "Pact proposals" in text and "proposes pact 1" in text
    labels = [c.item.label for c in view.children]
    assert labels == ["Accept pact 1", "Decline pact 1"]
    assert view.children[0].item.custom_id == "night:7:pact:accept:1"
    text, view = bot.night_message(game, 102, 7)          # nothing waiting for Corvain
    assert view is None and "Pact proposals" not in text
