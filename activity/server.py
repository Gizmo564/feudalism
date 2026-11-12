"""The web side of the Discord Activity: serves the page and a few JSON endpoints.
Discord loads the page in an iframe and sends the page's requests through its proxy, as /.proxy/<path>."""
import os

import aiohttp
from aiohttp import web

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DISCORD_API = "https://discord.com/api"


async def discord_request(method, path, **kwargs):
    """Call Discord's web API. Returns (status, json). Tests replace this."""
    async with aiohttp.ClientSession() as session:
        async with session.request(method, DISCORD_API + path, **kwargs) as resp:
            return resp.status, await resp.json(content_type=None)


def make_app(client_id, client_secret, game, call=discord_request):
    app = web.Application()

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

    async def me(request):
        """Who is asking? Ask Discord about the token, then look up their nation."""
        bearer = request.headers.get("Authorization", "")
        if not bearer.startswith("Bearer "):
            return web.json_response({"error": "not logged in"}, status=401)
        status, user = await call("GET", "/users/@me", headers={"Authorization": bearer})
        if status != 200 or "id" not in user:
            return web.json_response({"error": "not logged in"}, status=401)
        nid = game.players.get(str(user["id"]))
        nation = game.world["nations"][nid]["name"] if game.phase == "running" and nid is not None else None
        return web.json_response({"id": user["id"], "name": user.get("global_name") or user["username"], "nation": nation})

    async def index(request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    for prefix in ("", "/.proxy"):        # Discord asks for /.proxy/<path>; a plain browser asks for <path>
        app.router.add_get(prefix + "/api/config", config)
        app.router.add_post(prefix + "/api/token", token)
        app.router.add_get(prefix + "/api/me", me)
        app.router.add_static(prefix + "/static", STATIC)
    app.router.add_get("/", index)
    return app


async def start(port, client_id, client_secret, game):
    """Run the web server alongside the bot (called once the bot is connected)."""
    runner = web.AppRunner(make_app(client_id, client_secret, game))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
