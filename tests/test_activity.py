import asyncio

from aiohttp.test_utils import TestClient, TestServer

from activity import server
from conftest import RULES_PATH  # noqa: F401  (puts the project on the path)
from population.game import Game
from population.store import Store


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
        client = TestClient(TestServer(server.make_app("12345", "secret", make_game(tmp_path), fake_discord(calls))))
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
        c = TestClient(TestServer(server.make_app("1", "s", make_game(tmp_path), visitor)))
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
    run(tmp_path, steps)


def test_map_data_hides_fogged_tiles_and_players_not_in_the_game(tmp_path):
    async def go():
        game = make_game(tmp_path)
        game.world["settings"]["fog"] = True
        async def asker(method, path, **kw):
            return 200, {"id": kw["headers"]["Authorization"][7:], "username": "u"}
        c = TestClient(TestServer(server.make_app("1", "s", game, asker)))
        await c.start_server()
        try:
            data = await (await c.get("/api/map", headers={"Authorization": "Bearer 100"})).json()
            fogged = [t for t in data["tiles"] if not t["visible"]]
            assert fogged and all(t["owner"] is None and t["building"] is None and not t["oil"] for t in fogged)
            assert (await c.get("/api/map", headers={"Authorization": "Bearer 999"})).status == 404
        finally:
            await c.close()
    asyncio.run(go())
