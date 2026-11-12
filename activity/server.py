"""The web side of the Discord Activity: serves the page and a few JSON endpoints.
Discord loads the page in an iframe and sends the page's requests through its proxy, as /.proxy/<path>."""
import os
import time

import aiohttp
from aiohttp import web

from population import mapnav, render, views
from population.game import GameError

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DISCORD_API = "https://discord.com/api"


async def discord_request(method, path, **kwargs):
    """Call Discord's web API. Returns (status, json). Tests replace this."""
    async with aiohttp.ClientSession() as session:
        async with session.request(method, DISCORD_API + path, **kwargs) as resp:
            return resp.status, await resp.json(content_type=None)


def describe(o):
    """One line for a queued order."""
    kind = o["type"]
    if kind == "claim":
        return "Claim " + o["tile"]
    if kind == "build":
        return "Build %s on %s" % (o["building"], o["tile"])
    if kind == "move":
        return "Army %d to %s" % (o["army"], o["dest"])
    if kind == "train":
        return "Train %d" % o["n"]
    return kind


def make_app(client_id, client_secret, find_game, call=discord_request):
    "find_game(server id) gives that server's Game, or None. The page sends its server in the X-Guild-Id header."
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

    def here(request):
        return find_game(request.headers.get("X-Guild-Id", ""))

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
        game = here(request)
        nid = game.players.get(str(user["id"])) if game else None
        nation = game.world["nations"][nid]["name"] if game and game.phase == "running" and nid is not None else None
        return web.json_response({"id": user["id"], "name": user.get("global_name") or user["username"], "nation": nation})

    async def map_data(request):
        """The map exactly as this player may see it (fog of war included)."""
        user = await asker(request)
        if user is None:
            return web.json_response({"error": "not logged in"}, status=401)
        game = here(request)
        nid = game.players.get(str(user["id"])) if game else None
        if game is None or game.phase != "running" or nid is None:
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
            "tick": world["tick"], "me": nid, "capital": world["nations"][nid]["capital"], "nations": nations,
            "resources": [[r, stock.get(r, 0)] for r in rules["resources"]],
            "holdings": {"land": land, "buildings": buildings, "building_types": list(rules["buildings"])},
            "tiles": [{"k": t["k"], "terrain": t["terrain"], "owner": t["owner"], "building": t["building"],
                       "oil": bool(t["oil"]), "visible": t["visible"]} for t in tiles],
            "armies": [{"id": a["id"], "owner": a["owner"], "tile": a["tile"], "n": a["n"],
                        "dest": a["dest"] if a["owner"] == nid else None} for a in armies],
            "orders": [describe(o) for o in world["orders"] if o["nation"] == nid],
            "soldier": game.word("soldier"),
            "caravans": [{"from": c["from"], "to": c["to"], "tile": c["tile"], "dest": c["dest"]} for c in caravans]})

    async def tile_data(request):
        """What one tile is and what this player could do there (the same card the Discord map shows)."""
        user = await asker(request)
        if user is None:
            return web.json_response({"error": "not logged in"}, status=401)
        game = here(request)
        nid = game.players.get(str(user["id"])) if game else None
        if game is None or game.phase != "running" or nid is None:
            return web.json_response({"error": "not in a running game"}, status=404)
        card = mapnav.tile_card(game.world, game.rules, nid, request.query.get("k", ""))
        keys = ("text", "can_claim", "builds", "can_move", "claim_cost", "claim_missing", "claim_queued", "build_cost", "build_missing")
        return web.json_response({k: card[k] for k in keys})

    async def order(request):
        """Place or clear an order. The game's own checks decide whether it is allowed, exactly as in Discord."""
        user = await asker(request)
        if user is None:
            return web.json_response({"error": "not logged in"}, status=401)
        game = here(request)
        if game is None or game.players.get(str(user["id"])) is None or game.phase != "running":
            return web.json_response({"error": "not in a running game"}, status=404)
        try:
            body = await request.json()
            uid, kind = int(user["id"]), body.get("action")
            if kind == "claim":
                message = game.claim(uid, str(body.get("tile", "")))
            elif kind == "build":
                message = game.build(uid, str(body.get("tile", "")), str(body.get("building", "")))
            elif kind == "move":
                message = game.move(uid, int(body.get("army")), str(body.get("tile", "")))
            elif kind == "cancel":
                message = game.cancel(uid)
            else:
                return web.json_response({"ok": False, "message": "Unknown order."}, status=400)
        except (GameError, ValueError, TypeError) as e:
            return web.json_response({"ok": False, "message": str(e)}, status=400)
        game.store.set("last_activity", time.time())        # automatic updates wait while people are playing
        return web.json_response({"ok": True, "message": message})

    async def index(request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    for prefix in ("", "/.proxy"):        # Discord asks for /.proxy/<path>; a plain browser asks for <path>
        app.router.add_get(prefix + "/api/config", config)
        app.router.add_post(prefix + "/api/token", token)
        app.router.add_get(prefix + "/api/me", me)
        app.router.add_get(prefix + "/api/map", map_data)
        app.router.add_get(prefix + "/api/tile", tile_data)
        app.router.add_post(prefix + "/api/order", order)
        app.router.add_static(prefix + "/static", STATIC)
    app.router.add_get("/", index)
    return app


async def start(port, client_id, client_secret, find_game):
    """Run the web server alongside the bot (called once the bot is connected)."""
    runner = web.AppRunner(make_app(client_id, client_secret, find_game))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
