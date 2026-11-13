import asyncio

from aiohttp.test_utils import TestClient, TestServer

from activity import server
from conftest import RULES_PATH  # noqa: F401  (puts the project on the path)
from population import world as W
from population.game import Game
from population.store import Store


def only(g):
    "The Activity looks a game up by server; these tests have just one."
    return lambda server_id: g


def make_game(tmp_path):
    g = Game(Store(str(tmp_path / "a.db")))
    g.open_signup("feudalism")
    for i, name in enumerate(["Aldmere", "Brask", "Corvain", "Dunmore"]):
        g.join(100 + i, name)
    g.start(6, seed=5)
    return g


def fake_discord(calls):
    async def call(method, path, **kwargs):
        calls.append((method, path))
        if path == "/oauth2/token":
            ok = kwargs["data"]["code"] == "good-code" and kwargs["data"]["client_secret"] == "secret"
            return (200, {"access_token": "tok"}) if ok else (400, {"error": "invalid_grant"})
        if path == "/users/@me":
            if kwargs["headers"]["Authorization"] == "Bearer tok":
                return 200, {"id": "100", "username": "aldy", "global_name": "Alder"}
            return 401, {"message": "401: Unauthorized"}
    return call


def run(tmp_path, steps):
    async def go():
        calls = []
        client = TestClient(TestServer(server.make_app("12345", "secret", only(make_game(tmp_path)), fake_discord(calls))))
        await client.start_server()
        try:
            await steps(client, calls)
        finally:
            await client.close()
    asyncio.run(go())


def test_page_config_and_proxy_prefix(tmp_path):
    async def steps(client, calls):
        assert (await client.get("/")).status == 200
        assert (await (await client.get("/api/config")).json()) == {"clientId": "12345"}
        assert (await (await client.get("/.proxy/api/config")).json()) == {"clientId": "12345"}   # Discord's path
        assert (await client.get("/static/app.js")).status == 200
    run(tmp_path, steps)


def test_login_flow(tmp_path):
    async def steps(client, calls):
        assert (await client.post("/api/token", json={})).status == 400
        assert (await client.post("/api/token", json={"code": "bad"})).status == 401
        got = await client.post("/.proxy/api/token", json={"code": "good-code"})
        assert (await got.json()) == {"access_token": "tok"}
        me = await client.get("/api/me", headers={"Authorization": "Bearer tok"})
        assert (await me.json()) == {"id": "100", "name": "Alder", "nation": "Aldmere"}
        assert (await client.get("/api/me")).status == 401                                       # no token
        assert (await client.get("/api/me", headers={"Authorization": "Bearer nope"})).status == 401
    run(tmp_path, steps)


def test_player_without_a_nation(tmp_path):
    async def go():
        async def visitor(method, path, **kw):
            return 200, {"id": "999", "username": "visitor"}
        c = TestClient(TestServer(server.make_app("1", "s", only(make_game(tmp_path)), visitor)))
        await c.start_server()
        try:
            me = await (await c.get("/api/me", headers={"Authorization": "Bearer x"})).json()
            assert me == {"id": "999", "name": "visitor", "nation": None}
        finally:
            await c.close()
    asyncio.run(go())


def test_map_data_follows_the_players_view(tmp_path):
    async def steps(client, calls):
        assert (await client.get("/api/map")).status == 401
        got = await client.get("/.proxy/api/map", headers={"Authorization": "Bearer tok"})
        data = await got.json()
        assert got.status == 200 and data["me"] == 0
        assert data["capital"] in [t["k"] for t in data["tiles"] if t["owner"] == 0]
        assert {"k", "terrain", "owner", "building", "oil", "visible"} <= set(data["tiles"][0])
        assert any(n["id"] == 0 and n["color"].startswith("#") for n in data["nations"])
        assert data["tick"] == 0
        assert dict(data["resources"])["wood"] >= 0 and [r for r, _ in data["resources"]][:2] == ["food", "wood"]
        assert data["holdings"]["land"] and "capital" in data["holdings"]["buildings"]
        assert sum(data["holdings"]["land"].values()) == len([t for t in data["tiles"] if t["owner"] == 0])
    run(tmp_path, steps)


def test_map_data_hides_fogged_tiles_and_players_not_in_the_game(tmp_path):
    async def go():
        game = make_game(tmp_path)
        game.world["settings"]["fog"] = True
        async def asker(method, path, **kw):
            return 200, {"id": kw["headers"]["Authorization"][7:], "username": "u"}
        c = TestClient(TestServer(server.make_app("1", "s", only(game), asker)))
        await c.start_server()
        try:
            data = await (await c.get("/api/map", headers={"Authorization": "Bearer 100"})).json()
            fogged = [t for t in data["tiles"] if not t["visible"]]
            assert fogged and all(t["owner"] is None and t["building"] is None and not t["oil"] for t in fogged)
            assert (await c.get("/api/map", headers={"Authorization": "Bearer 999"})).status == 404
        finally:
            await c.close()
    asyncio.run(go())


def test_pages_are_never_cached(tmp_path):
    async def steps(client, calls):
        for path in ("/", "/static/app.js", "/api/config"):
            assert (await client.get(path)).headers["Cache-Control"] == "no-store"
    run(tmp_path, steps)


def test_tile_card(tmp_path):
    async def steps(client, calls):
        assert (await client.get("/api/tile?k=0,0")).status == 401
        game_map = await (await client.get("/api/map", headers={"Authorization": "Bearer tok"})).json()
        cap = game_map["capital"]
        got = await client.get("/.proxy/api/tile?k=" + cap, headers={"Authorization": "Bearer tok"})
        card = await got.json()
        assert got.status == 200 and cap in card["text"] and "Aldmere" in card["text"]
        assert {"text", "can_claim", "builds", "can_move", "claim_cost", "claim_missing", "build_cost"} <= set(card)
        nowhere = await (await client.get("/api/tile?k=99,99", headers={"Authorization": "Bearer tok"})).json()
        assert nowhere["text"] == "No such tile."
    run(tmp_path, steps)


def test_map_data_includes_caravans_for_their_traders(tmp_path):
    from population import trade
    async def steps(client, calls):
        pass
    g = make_game(tmp_path)
    trade.send_caravan(g.world, 0, 1, {"wood": 5})

    async def go():
        async def asker(method, path, **kw):
            return 200, {"id": kw["headers"]["Authorization"][7:], "username": "u"}
        c = TestClient(TestServer(server.make_app("1", "s", only(g), asker)))
        await c.start_server()
        try:
            data = await (await c.get("/api/map", headers={"Authorization": "Bearer 100"})).json()
            assert data["caravans"][0]["from"] == 0 and data["caravans"][0]["to"] == 1 and "res" not in data["caravans"][0]
            assert {n["id"] for n in data["nations"]} >= {0, 1}
        finally:
            await c.close()
    asyncio.run(go())


def test_orders_are_refused_when_the_game_says_no(tmp_path):
    async def steps(client, calls):
        h = {"Authorization": "Bearer tok"}
        assert (await client.post("/api/order", json={"action": "cancel"})).status == 401
        data = await (await client.get("/api/map", headers=h)).json()
        assert data["orders"] == [] and data["soldier"]
        bad = await client.post("/.proxy/api/order", json={"action": "claim", "tile": data["capital"]}, headers=h)   # already yours
        assert bad.status == 400 and not (await bad.json())["ok"]
        assert (await client.post("/api/order", json={"action": "fly"}, headers=h)).status == 400
        assert (await client.post("/api/order", json={"action": "move", "army": "x", "tile": data["capital"]}, headers=h)).status == 400
    run(tmp_path, steps)


def test_claim_then_cancel(tmp_path):
    g = make_game(tmp_path)
    g.world["nations"][0]["resources"].update(food=999, wood=999, stone=999, ore=999)

    async def go():
        async def asker(method, path, **kw):
            return 200, {"id": "100", "username": "u"}
        c = TestClient(TestServer(server.make_app("1", "s", only(g), asker)))
        await c.start_server()
        h = {"Authorization": "Bearer x"}
        try:
            data = await (await c.get("/api/map", headers=h)).json()
            mine = {t["k"] for t in data["tiles"] if t["owner"] == 0}
            from population import mapnav
            free = sorted(mapnav.claimable(g.world, 0))[0]
            ok = await (await c.post("/api/order", json={"action": "claim", "tile": free}, headers=h)).json()
            assert ok["ok"] and free in ok["message"]
            assert g.store.get("last_activity")
            data = await (await c.get("/api/map", headers=h)).json()
            assert data["orders"] == ["Claim " + free]
            again = await c.post("/api/order", json={"action": "claim", "tile": free}, headers=h)   # one claim a night
            assert again.status == 400
            tile = await (await c.get("/api/tile?k=" + free, headers=h)).json()
            assert tile["claim_queued"] is True
            built = await (await c.post("/api/order", json={"action": "build", "tile": sorted(mine)[0], "building": "home"}, headers=h)).json()
            assert built["ok"] or "already has" in built["message"]
            cleared = await (await c.post("/api/order", json={"action": "cancel"}, headers=h)).json()
            assert cleared["ok"] and (await (await c.get("/api/map", headers=h)).json())["orders"] == []
            stranger = await c.post("/api/order", json={"action": "cancel"}, headers={"Authorization": "Bearer y"})
            assert stranger.status in (200, 404)
        finally:
            await c.close()
    asyncio.run(go())


def test_each_server_sees_only_its_own_game(tmp_path):
    a, b = make_game(tmp_path), Game(Store(str(tmp_path / "b.db")))      # b has no game running yet
    games = {"1": a, "2": b}

    async def asker(method, path, **kwargs):
        return 200, {"id": "100", "username": "u"}

    async def go():
        c = TestClient(TestServer(server.make_app("1", "s", lambda sid: games.get(sid), asker)))
        await c.start_server()
        auth = {"Authorization": "Bearer tok"}
        ok = await c.get("/api/map", headers=dict(auth, **{"X-Guild-Id": "1"}))
        other = await c.get("/api/map", headers=dict(auth, **{"X-Guild-Id": "2"}))
        nowhere = await c.get("/api/map", headers=dict(auth, **{"X-Guild-Id": "3"}))
        await c.close()
        return ok.status, other.status, nowhere.status
    assert asyncio.run(go()) == (200, 404, 404)


def run_as(g, steps, hooks=None):
    "Run steps against a game, logged in as nation 0 (user 100) unless the bearer token says another user id."
    async def go():
        async def asker(method, path, **kw):
            return 200, {"id": kw["headers"]["Authorization"][7:], "username": "u"}
        c = TestClient(TestServer(server.make_app("1", "s", only(g), asker, hooks=hooks)))
        await c.start_server()
        try:
            await steps(c)
        finally:
            await c.close()
    asyncio.run(go())


def post(c, who, body):
    return c.post("/api/order", json=body, headers={"Authorization": "Bearer %d" % who})


def test_nation_data_and_priority_and_train(tmp_path):
    g = make_game(tmp_path)

    async def steps(c):
        h = {"Authorization": "Bearer 100"}
        assert (await c.get("/api/nation")).status == 401
        data = await (await c.get("/.proxy/api/nation", headers=h)).json()
        assert data["people"]["workers"] > 0 and data["people"]["worker_room"] >= data["people"]["workers"]
        assert {p["resource"] for p in data["priorities"]} == set(g.rules["resources"])
        assert data["others"] == ["Brask", "Corvain", "Dunmore"] and data["dm"] is False
        assert data["report"] and data["train_cost"]
        res = next(p["resource"] for p in data["priorities"] if p["resource"] != "food")
        ok = await (await post(c, 100, {"action": "priority", "resource": res, "value": 4})).json()
        assert ok["ok"] and g.world["nations"][0]["priorities"][res] == 4
        assert (await post(c, 100, {"action": "priority", "resource": res, "value": 9})).status == 400
        g.world["nations"][0]["resources"].update(food=500, ore=500)
        assert (await (await post(c, 100, {"action": "train", "n": 1})).json())["ok"]
        assert (await post(c, 100, {"action": "train", "n": "lots"})).status == 400
    run_as(g, steps)


def test_upgrade_and_dm_from_the_activity(tmp_path):
    g = make_game(tmp_path)
    k = next(k for k in W.owned(g.world, 0) if g.world["tiles"][k]["building"] is None)
    g.world["tiles"][k]["building"] = "home"
    g.world["nations"][0]["resources"].update(wood=500, stone=500)

    async def steps(c):
        h = {"Authorization": "Bearer 100"}
        card = await (await c.get("/api/tile?k=" + k, headers=h)).json()
        assert card["upgrade"]["level"] == 2 and not card["upgrade"]["missing"]
        assert (await (await post(c, 100, {"action": "upgrade", "tile": k})).json())["ok"]
        assert (await (await post(c, 100, {"action": "dm", "on": True})).json())["ok"] and g.wants_dm(100)
        assert (await (await post(c, 100, {"action": "dm", "on": False})).json())["ok"] and not g.wants_dm(100)
    run_as(g, steps)


def test_trades_and_pacts_from_the_activity(tmp_path):
    g = make_game(tmp_path)
    for nid in (0, 1):
        k = next(k for k, v in g.world["tiles"].items() if v["owner"] == nid and v["building"] is None)
        g.world["tiles"][k]["building"] = "market"
    seen = []

    async def offer_hook(server_id, tid):
        seen.append(("offer", server_id, tid))
        return True

    async def resolved_hook(server_id, tid):
        seen.append(("resolved", server_id, tid))

    async def steps(c):
        public = await (await post(c, 100, {"action": "trade_offer", "to": "", "give": "wood 5", "get": "stone 2"})).json()
        assert public["ok"] and "trade board" in public["message"]
        private = await (await post(c, 100, {"action": "trade_offer", "to": "Brask", "give": "wood 3", "get": "stone 1", "private": True})).json()
        assert private["ok"] and "buttons" in private["message"]
        assert (await post(c, 100, {"action": "trade_offer", "to": "", "give": "", "get": "", "private": True})).status == 400
        mine = await (await c.get("/api/nation", headers={"Authorization": "Bearer 100"})).json()
        theirs = await (await c.get("/api/nation", headers={"Authorization": "Bearer 101"})).json()
        assert len(mine["offers"]) == 2 and all(o["mine"] for o in mine["offers"])
        assert len(theirs["offers"]) == 2 and not any(o["mine"] for o in theirs["offers"])
        carol = await (await c.get("/api/nation", headers={"Authorization": "Bearer 102"})).json()
        assert len(carol["offers"]) == 1                      # the private offer is not hers to see
        g.world["nations"][1]["resources"]["stone"] = 50
        tid = theirs["offers"][0]["id"]
        assert (await (await post(c, 101, {"action": "trade_accept", "id": tid})).json())["ok"]
        assert ("resolved", "", tid) in seen
        pact = await (await post(c, 100, {"action": "pact_propose", "to": "Brask", "text": "No raids"})).json()
        assert pact["ok"]
        waiting = (await (await c.get("/api/nation", headers={"Authorization": "Bearer 101"})).json())["pacts"][0]
        assert waiting["state"] == "waiting for you"
        assert (await (await post(c, 101, {"action": "pact_accept", "id": waiting["id"]})).json())["ok"]
        assert (await (await post(c, 100, {"action": "pact_break", "id": waiting["id"]})).json())["ok"]
    run_as(g, steps, hooks={"offer": offer_hook, "resolved": resolved_hook})
    assert [s[0] for s in seen].count("offer") == 2


def test_modes_assign_and_split_armies_from_the_activity(tmp_path):
    g = make_game(tmp_path)
    army = next(a for a in g.world["armies"] if a["owner"] == 0)
    dest = next(k for k in W.owned(g.world, 0) if k != army["tile"])

    async def steps(c):
        h = {"Authorization": "Bearer 100"}
        data = await (await c.get("/api/nation", headers=h)).json()
        assert data["mode"] == "basic" and data["advanced_ok"] and data["assign"] == {}
        assert (await post(c, 100, {"action": "assign", "resource": "wood", "count": 2})).status == 400      # basic mode
        assert (await post(c, 100, {"action": "move", "army": army["id"], "tile": dest, "n": 1})).status == 400
        assert (await (await post(c, 100, {"action": "mode", "mode": "advanced"})).json())["ok"]
        assert (await (await c.get("/api/map", headers=h)).json())["mode"] == "advanced"
        assert (await (await post(c, 100, {"action": "assign", "resource": "wood", "count": 2})).json())["ok"]
        data = await (await c.get("/api/nation", headers=h)).json()
        assert data["assign"] == {"wood": 2} and set(data["room"]) <= set(data["resources"])
        assert (await (await post(c, 100, {"action": "assign", "resource": "wood", "count": None})).json())["ok"]
        assert (await c.get("/api/nation", headers=h)).status == 200
        assert (await (await c.get("/api/nation", headers=h)).json())["assign"] == {}
        assert (await (await post(c, 100, {"action": "move", "army": army["id"], "tile": dest, "n": 1})).json())["ok"]
        assert (await post(c, 100, {"action": "mode", "mode": "wizard"})).status == 400
    run_as(g, steps)
