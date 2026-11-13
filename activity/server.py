"""The web side of the Discord Activity: serves the page and a few JSON endpoints.
Discord loads the page in an iframe and sends the page's requests through its proxy, as /.proxy/<path>."""
import os
import time

import aiohttp
from aiohttp import web

from population import advice, guide, infopage, mapnav, pacts, render, tick, views, world as W
from population.game import GameError

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DISCORD_API = "https://discord.com/api"


async def discord_request(method, path, **kwargs):
    """Call Discord's web API. Returns (status, json). Tests replace this."""
    async with aiohttp.ClientSession() as session:
        async with session.request(method, DISCORD_API + path, **kwargs) as resp:
            return resp.status, await resp.json(content_type=None)


describe = tick.describe_order


def over_data(game, nid):
    """The game over screen: who won and why, the final standings (all nations, fog or not), and whether this player won."""
    if game.phase != "over":
        return None
    w = game.world
    win = w["winner"]
    rows = sorted(({"name": n["name"], "land": len(W.owned(w, n["id"])), "population": W.people(w, n["id"]), "alive": n["alive"], "me": n["id"] == nid}
                   for n in w["nations"]), key=lambda r: (-r["land"], -r["population"]))
    return {"winner": w["nations"][win["nation"]]["name"] if win else None, "reason": win["reason"] if win else "",
            "you_won": bool(win) and win["nation"] == nid, "rows": rows}


def nation_data(game, uid, nid):
    """Everything the Nation, Trade and Diplomacy tabs show, in the same words the commands use."""
    w, r = game.world, game.rules
    n = w["nations"][nid]
    hw, hs = W.housing(w, r, nid)
    soldiers = W.soldiers(w, nid)
    plan = tick.work_plan(w, r, n)
    can = infopage.available(w, r, nid)
    net, nights = advice.food_balance(w, r, nid)
    try:
        standings = game.standings()
    except GameError as e:
        standings = str(e)
    offers = []
    for o in w["trades"]:
        if o["status"] != "open":
            continue
        mine = o["from"] == nid
        if not mine and o["to"] not in (nid, None):
            continue
        offers.append({"id": o["id"], "line": game.offer_line(o), "mine": mine})
    pact_rows = []
    for p in pacts.for_nation(w, nid):
        other = w["nations"][p["b"] if p["a"] == nid else p["a"]]["name"]
        state = "active" if p["status"] == "active" else ("waiting for them" if p["a"] == nid else "waiting for you")
        pact_rows.append({"id": p["id"], "other": other, "state": state, "text": p["text"]})
    return {
        "alive": n["alive"], "worker": game.word("worker"), "soldier": game.word("soldier"),
        "people": {"workers": n["workers"], "worker_room": hw, "soldiers": soldiers, "soldier_room": hs},
        "work": [{"resource": res, "workers": c, "amount": a} for res, (c, a) in sorted(plan.items(), key=lambda x: -x[1][0])],
        "priorities": [{"resource": res, "value": n["priorities"].get(res, 0), "can": res in can, "amount": infopage.estimate(w, r, n, plan, res)}
                       for res in r["resources"]],
        "food": {"net": net, "nights": nights},
        "advice": advice.advice(w, r, nid),
        "train_cost": game.fmt(r["soldier"]["train_cost"]),
        "steel": {"problem": tick.steel_problem(w, r, nid), "cost": game.fmt(tick.train_cost(r, True)) if tick.steel_cfg(r) else ""},
        "contests": game.contests(nid),
        "updates": {"pending": game.pending(nid), "past": game.world.get("past_reports", {}).get(str(nid), [])},
        "report": game.report(uid), "standings": standings,
        "offers": offers, "pacts": pact_rows,
        "others": [m["name"] for m in w["nations"] if m["id"] != nid],
        "resources": list(r["resources"]),
        "dm": game.wants_dm(uid),
        "mode": n.get("mode", "basic"), "advanced_ok": game.advanced_allowed(),
        "assign": dict(n.get("assign", {})), "working": {res: c for res, (c, _) in plan.items()},
        "room": {res: tiles * r["workers_per_tile"] for res, (tiles, _) in infopage.makeable(w, r, nid).items()},
    }


def make_app(client_id, client_secret, find_game, call=discord_request, hooks=None):
    """find_game(server id) gives that server's Game, or None. The page sends its server in the X-Guild-Id header.
    hooks (optional, from the bot): async offer(server_id, tid) -> True if the offer was posted to the board or DMed;
    async resolved(server_id, tid) edits the board card of an offer that was just accepted or declined."""
    hooks = hooks or {}

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
        live = bool(game) and game.phase in ("running", "over") and nid is not None
        nation = game.world["nations"][nid]["name"] if live else None
        return web.json_response({"id": user["id"], "name": user.get("global_name") or user["username"], "nation": nation,
                                  "fallen": live and not game.world["nations"][nid]["alive"],
                                  "over": over_data(game, nid) if live else None, "mode": game.rules["name"].lower() if live else None,
                                  "win": guide.winning(game.rules).split("\n\n")[0].replace("**", "").split("\n", 1)[1] if live else None})

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
                       "oil": bool(t["oil"]), "visible": t["visible"], "level": t.get("level", 1)} for t in tiles],
            "armies": [{"id": a["id"], "owner": a["owner"], "tile": a["tile"], "n": a["n"], "steel": bool(a.get("steel")),
                        "dest": a["dest"] if a["owner"] == nid else None} for a in armies],
            "orders": [describe(o) for o in world["orders"] if o["nation"] == nid],
            "contested": [k for k, c in world.get("contested", {}).items() if k in {t["k"] for t in tiles if t["visible"]}],
            "contests": game.contests(nid), "pending": sum(len(v) for v in game.pending(nid).values()),
            "soldier": game.word("soldier"), "worker": game.word("worker"), "mode": world["nations"][nid].get("mode", "basic"),
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
        keys = ("text", "can_claim", "builds", "can_move", "claim_cost", "claim_missing", "claim_queued", "build_cost",
                "build_missing", "upgrade")
        return web.json_response({k: card[k] for k in keys})

    async def nation(request):
        """The player's own nation: people, work, priorities, trades, pacts, report, standings."""
        user = await asker(request)
        if user is None:
            return web.json_response({"error": "not logged in"}, status=401)
        game = here(request)
        nid = game.players.get(str(user["id"])) if game else None
        if game is None or game.phase != "running" or nid is None:
            return web.json_response({"error": "not in a running game"}, status=404)
        return web.json_response(nation_data(game, int(user["id"]), nid))

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
                count = body.get("n")
                message = game.move(uid, int(body.get("army")), str(body.get("tile", "")), None if count is None else int(count))
            elif kind == "cancel":
                message = game.cancel(uid, body.get("number"), body.get("text"))
            elif kind == "upgrade":
                message = game.upgrade(uid, str(body.get("tile", "")))
            elif kind == "train":
                message = game.train(uid, int(body.get("n")), bool(body.get("steel")))
            elif kind == "priority":
                message = game.set_priority(uid, str(body.get("resource", "")), int(body.get("value")))
            elif kind == "trade_offer":
                private = bool(body.get("private"))
                tid, message = game.make_offer(uid, str(body.get("to", "")), str(body.get("give", "")),
                                               str(body.get("get", "")), private=private)
                server_id = request.headers.get("X-Guild-Id", "")
                posted = await safely(hooks.get("offer"), server_id, tid)
                if posted:
                    message += " They were sent it with buttons." if private else " It is on the trade board."
                elif private:
                    message += " They haven't turned on DMs, so they will see it in their Trade tab and /offers."
            elif kind in ("trade_accept", "trade_decline"):
                tid = int(body.get("id"))
                message = (game.trade_accept if kind == "trade_accept" else game.trade_decline)(uid, tid)
                await safely(hooks.get("resolved"), request.headers.get("X-Guild-Id", ""), tid)
            elif kind == "pact_propose":
                message = game.pact_propose(uid, str(body.get("to", "")), str(body.get("text", "")))
            elif kind in ("pact_accept", "pact_decline", "pact_break"):
                message = getattr(game, kind)(uid, int(body.get("id")))
            elif kind == "mode":
                message = game.set_mode(uid, str(body.get("mode", "")))
            elif kind == "assign":
                count = body.get("count")
                message = game.set_assign(uid, str(body.get("resource", "")), None if count is None else int(count))
            elif kind == "contest":
                message = game.contest(uid, str(body.get("tile", "")), str(body.get("choice", "")))
            elif kind == "dm":
                message = game.set_dm(uid, bool(body.get("on")))
            else:
                return web.json_response({"ok": False, "message": "Unknown order."}, status=400)
        except (GameError, ValueError, TypeError) as e:
            return web.json_response({"ok": False, "message": str(e)}, status=400)
        for to, note in game.take_notices():                # private messages this order caused (pact news)
            await safely(hooks.get("dm"), to, note)
        game.store.set("last_activity", time.time())        # automatic updates wait while people are playing
        return web.json_response({"ok": True, "message": message})

    async def safely(hook, *args):
        """Run a bot hook; the order has already worked, so a Discord hiccup must not undo or hide it."""
        if hook is None:
            return False
        try:
            return await hook(*args)
        except Exception:
            return False

    async def index(request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    for prefix in ("", "/.proxy"):        # Discord asks for /.proxy/<path>; a plain browser asks for <path>
        app.router.add_get(prefix + "/api/config", config)
        app.router.add_post(prefix + "/api/token", token)
        app.router.add_get(prefix + "/api/me", me)
        app.router.add_get(prefix + "/api/map", map_data)
        app.router.add_get(prefix + "/api/tile", tile_data)
        app.router.add_get(prefix + "/api/nation", nation)
        app.router.add_post(prefix + "/api/order", order)
        app.router.add_static(prefix + "/static", STATIC)
    app.router.add_get("/", index)
    return app


async def start(port, client_id, client_secret, find_game, hooks=None):
    """Run the web server alongside the bot (called once the bot is connected)."""
    runner = web.AppRunner(make_app(client_id, client_secret, find_game, hooks=hooks))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
