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
    game.set_fog(True)
    far = next(k for k, t in game.world["tiles"].items() if t["owner"] == 3)
    assert "fogged" in mapnav.tile_card(game.world, game.rules, 0, far)["text"]


def test_render_options(game):
    w, rules = game.world, game.rules
    cap = w["nations"][0]["capital"]
    free = sorted(mapnav.claimable(w, 0))[0]
    for kw in ({}, {"icons": True}, {"icons": False, "mark": free}):
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
    i = SimpleNamespace(user=SimpleNamespace(id=uid), response=MagicMock(), edit_original_response=AsyncMock())
    i.response.defer, i.response.send_message = AsyncMock(), AsyncMock()
    return i


def find(view, label=None, kind=None):
    for c in view.children:
        if label is not None and getattr(c, "label", None) == label:
            return c
        if kind is not None and type(c).__name__ == kind and label is None:
            return c


def test_view_full_walkthrough(bot, game):
    async def go():
        view = bot.MapView(100, 0, 3)
        assert find(view, "Icons: off") is not None and find(view, "Claim") is None
        # pan, zoom, icons all redraw the picture
        for label in ("▶", "-", "Icons: off", "Center"):
            i = click(100)
            await find(view, label).callback(i)
            i.edit_original_response.assert_awaited()
            kwargs = i.edit_original_response.await_args.kwargs
            assert kwargs["attachments"][0].filename == "map.png"
        assert view.icons is True and view.radius == 4
        # pick a claimable tile, claim it
        menu = find(view, kind="Select")
        free = mapnav.pick_options(game.world, game.rules, 0, view.center, view.radius)[0][0]
        menu._values = [free]
        i = click(100)
        await view.picker(menu)(i)
        assert view.selected == free and find(view, "Claim") is not None
        i = click(100)
        await find(view, "Claim").callback(i)
        assert "queued" in i.response.send_message.await_args.args[0]
        assert any(o["type"] == "claim" and o["tile"] == free for o in game.world["orders"])
        # pick own empty tile, build
        mine = next(k for k in game.world["tiles"] if game.world["tiles"][k]["owner"] == 0
                    and game.world["tiles"][k]["building"] is None)
        menu = [c for c in view.children if type(c).__name__ == "Select"][0]
        menu._values = [mine]
        await view.picker(menu)(click(100))
        i = click(100)
        await find(view, "Home").callback(i)
        assert "Build" in i.response.send_message.await_args.args[0]
        # send an army to a tile
        army_menu = [c for c in view.children if type(c).__name__ == "Select"][-1]
        army_menu._values = [str(game.world["armies"][0]["id"])]
        i = click(100)
        await view.send_army(army_menu)(i)
        assert "march" in i.response.send_message.await_args.args[0]
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
