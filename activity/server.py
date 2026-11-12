"""The web side of the Discord Activity: serves the page and a few JSON endpoints.
Discord loads the page in an iframe and sends the page's requests through its proxy, as /.proxy/<path>."""
import os

import aiohttp
from aiohttp import web

from population import mapnav, render, views

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DISCORD_API = "https://discord.com/api"


async def discord_request(method, path, **kwargs):
    """Call Discord's web API. Returns (status, json). Tests replace this."""
    async with aiohttp.ClientSession() as session:
        async with session.request(method, DISCORD_API + path, **kwargs) as resp:
            return resp.status, await resp.json(content_type=None)


def make_app(client_id, client_secret, game, call=discord_request):
    @web.middleware
    async def no_cache(request, handler):
        """Cloudflare and Discord would otherwise keep serving an old copy of the page after an update."""
        resp = await handler(request)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(middlewares=[no_cache])

    async def config(request):
        return web.json_response({"clientId": client_id})

    async def token(request):
        """Swap the one-time code from Discord's login for an access token."""
        code = (await request.json()).get("code")
        if not code:
            return web.json_response({"error": "no code"}, status=400)
        status, data = await call("POST", "/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret, "grant_type": "authorization_code", "code": code})
        if status != 200 or "access_token" not in data:
            return web.json_response({"error": "login failed"}, status=401)
        return web.json_response({"access_token": data["access_token"]})

    async def asker(request):
        """The Discord user making this request (checked with Discord), or None."""
        bearer = request.headers.get("Authorization", "")
        if not bearer.startswith("Bearer "):
            return None
        status, user = await call("GET", "/users/@me", headers={"Authorization": bearer})
        return user if status == 200 and "id" in user else None

    async def me(request):
        """Who is asking? Ask Discord about the token, then look up their nation."""
        user = await asker(request)
        if user is None:
            return web.json_response({"error": "not logged in"}, status=401)
        nid = game.players.get(str(user["id"]))
        nation = game.world["nations"][nid]["name"] if game.phase == "running" and nid is not None else None
        return web.json_response({"id": user["id"], "name": user.get("global_name") or user["username"], "nation": nation})

    async def map_data(request):
        """The map exactly as this player may see it (fog of war included)."""
        user = await asker(request)
        if user is None:
            return web.json_response({"error": "not logged in"}, status=401)
        nid = game.players.get(str(user["id"]))
        if game.phase != "running" or nid is None:
            return web.json_response({"error": "not in a running game"}, status=404)
        world, rules = game.world, game.rules
        tiles = views.map_view(world, rules, nid)
        armies = views.armies_view(world, rules, nid)
        caravans = views.caravans_view(world, rules, nid)
        shown = {t["owner"] for t in tiles if t["owner"] is not None} | {a["owner"] for a in armies} | {nid}
        shown |= {c["from"] for c in caravans} | {c["to"] for c in caravans}
        nations = [{"id": n["id"], "name": n["name"], "color": "#%02x%02x%02x" % render.nation_color(n["id"])}
                   for n in world["nations"] if n["id"] in shown]
        mine = [t for t in world["tiles"].values() if t["owner"] == nid]
        land = {}
        buildings = {}
        for t in mine:
            land[t["terrain"]] = land.get(t["terrain"], 0) + 1
            if t["building"]:
                buildings[t["building"]] = buildings.get(t["building"], 0) + 1
        stock = world["nations"][nid]["resources"]
        return web.json_response({
            "me": nid, "capital": world["nations"][nid]["capital"], "nations": nations,
            "resources": [[r, stock.get(r, 0)] for r in rules["resources"]],
            "holdings": {"land": land, "buildings": buildings, "building_types": list(rules["buildings"])},
            "tiles": [{"k": t["k"], "terrain": t["terrain"], "owner": t["owner"], "building": t["building"],
                       "oil": bool(t["oil"]), "visible": t["visible"]} for t in tiles],
            "armies": [{"owner": a["owner"], "tile": a["tile"], "n": a["n"]} for a in armies],
            "caravans": [{"from": c["from"], "to": c["to"], "tile": c["tile"], "dest": c["dest"]} for c in caravans]})

    async def tile_data(request):
        """What one tile is and what this player could do there (the same card the Discord map shows)."""
        user = await asker(request)
        if user is None:
            return web.json_response({"error": "not logged in"}, status=401)
        nid = game.players.get(str(user["id"]))
        if game.phase != "running" or nid is None:
            return web.json_response({"error": "not in a running game"}, status=404)
        card = mapnav.tile_card(game.world, game.rules, nid, request.query.get("k", ""))
        keys = ("text", "can_claim", "builds", "can_move", "claim_cost", "claim_missing", "claim_queued", "build_cost", "build_missing")
        return web.json_response({k: card[k] for k in keys})

    async def index(request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    for prefix in ("", "/.proxy"):        # Discord asks for /.proxy/<path>; a plain browser asks for <path>
        app.router.add_get(prefix + "/api/config", config)
        app.router.add_post(prefix + "/api/token", token)
        app.router.add_get(prefix + "/api/me", me)
        app.router.add_get(prefix + "/api/map", map_data)
        app.router.add_get(prefix + "/api/tile", tile_data)
        app.router.add_static(prefix + "/static", STATIC)
    app.router.add_get("/", index)
    return app


async def start(port, client_id, client_secret, game):
    """Run the web server alongside the bot (called once the bot is connected)."""
    runner = web.AppRunner(make_app(client_id, client_secret, game))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
