"""The Discord bot. Run with:  python3 discord_bot.py   (reads settings from .env)
All game rules live in the population/ package; this file only talks to Discord."""
import io
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

from population import render, ruleset
from population.game import Game, GameError
from population.store import Store
from population.hexes import neighbors
from population import world as W


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
TOKEN = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = os.environ.get("GUILD_ID", "").strip()
ADMIN_ROLE = os.environ.get("ADMIN_ROLE", "admin")
TICK_TIME = os.environ.get("TICK_TIME", "02:00")
TICK_TZ = os.environ.get("TICK_TZ", "America/Denver")
TICK_MINUTES = os.environ.get("TICK_MINUTES", "").strip()

store = Store(os.environ.get("DB_PATH", "game.db"))
game = Game(store)
client = discord.Client(intents=discord.Intents.default())   # no privileged intents needed
tree = app_commands.CommandTree(client)
admin = app_commands.Group(name="admin", description="Admin commands")


# ---------- small helpers ----------

async def say(interaction, text, file=None, public=False):
    """Reply to a command. Private ("only you can see this") unless public=True."""
    kwargs = {"ephemeral": not public}
    if file:
        kwargs["file"] = file
    await interaction.response.send_message(text, **kwargs)


async def run(interaction, fn, *args, public=False):
    """Call a Game method and show its text. Errors are always private."""
    try:
        text = fn(*args)
    except GameError as e:
        return await say(interaction, "Can't do that: %s" % e)
    await say(interaction, text, public=public)


def announce_channel():
    channel_id = store.get("announce_channel") or store.get("report_channel")
    return client.get_channel(channel_id) if channel_id else None


async def publish(interaction, text):
    """Show a public message. If an announcements channel is set (and it isn't this one), post there
    and give the admin a private note; otherwise reply publicly right here."""
    channel = announce_channel()
    if channel and channel.id != interaction.channel_id:
        try:
            await channel.send(text)
        except discord.Forbidden:
            return await say(interaction, "I can't post in #%s. Give me Send Messages there." % channel.name)
        return await say(interaction, "Posted in #%s." % channel.name)
    await say(interaction, text, public=True)


def is_admin(interaction):
    """Has the admin role (name checked ignoring case and spaces), or Discord's Administrator permission."""
    roles = getattr(interaction.user, "roles", [])
    if any(r.name.strip().lower() == ADMIN_ROLE.strip().lower() for r in roles):
        return True
    return interaction.permissions.administrator


def deny(interaction):
    seen = ", ".join(r.name for r in getattr(interaction.user, "roles", []) if r.name != "@everyone") or "none"
    return "You need the '%s' role for this. (Roles I can see on you: %s)" % (ADMIN_ROLE, seen)


def png(data, name="map.png"):
    return discord.File(io.BytesIO(data), filename=name)


def tile_choices(keys, typed):
    typed = typed.strip()
    out = [k for k in keys if typed in k][:25]
    return [app_commands.Choice(name=k, value=k) for k in out]


def my_nation(interaction):
    nid = game.players.get(str(interaction.user.id))
    return nid if game.phase == "running" and nid is not None else None


# ---------- player commands ----------

@tree.command(description="Join the game that is open for sign-ups")
@app_commands.describe(name="Your nation's name (2-20 characters)")
async def join(interaction: discord.Interaction, name: str):
    await run(interaction, game.join, interaction.user.id, name, public=True)


@tree.command(description="Your nation: land, people, stock, orders, and last night's news (only you see this)")
async def status(interaction: discord.Interaction):
    await run(interaction, game.status, interaction.user.id)


@tree.command(description="Everyone's land and population")
async def standings(interaction: discord.Interaction):
    await run(interaction, game.standings, public=True)


@tree.command(description="The whole map as you can see it")
async def map(interaction: discord.Interaction):
    await show_map(interaction, zoom=False, radius=3)


@tree.command(description="Zoomed map around your capital, with tile coordinates")
@app_commands.describe(radius="How many tiles out to show (1-6)")
async def zoom(interaction: discord.Interaction, radius: app_commands.Range[int, 1, 6] = 3):
    await show_map(interaction, zoom=True, radius=radius)


async def show_map(interaction, zoom, radius):
    if game.phase not in ("running", "over"):
        return await say(interaction, "Can't do that: there is no game running")
    nid = game.players.get(str(interaction.user.id))
    await interaction.response.defer(ephemeral=True)
    if zoom and nid is not None:
        data = render.render(game.world, game.rules, nid=nid, center=game.world["nations"][nid]["capital"],
                             radius=radius, tile=48, legend=False, coords=True)
    else:
        data = render.render(game.world, game.rules, nid=nid)
    await interaction.followup.send(file=png(data), ephemeral=True)


@tree.command(description="Claim a free tile next to your land (1 per night, cost rises as you grow)")
@app_commands.describe(tile="Tile coordinates like 3,-2")
async def claim(interaction: discord.Interaction, tile: str):
    await run(interaction, game.claim, interaction.user.id, tile.strip())


@claim.autocomplete("tile")
async def claim_tiles(interaction: discord.Interaction, current: str):
    nid = my_nation(interaction)
    if nid is None:
        return []
    free = {n for k in W.owned(game.world, nid) for n in neighbors(k) if W.is_free_land(game.world, n)}
    return tile_choices(sorted(free), current)


@tree.command(description="Build on one of your tiles")
@app_commands.describe(tile="Your tile, like 3,-2", building="home, barracks, market or wall")
@app_commands.choices(building=[app_commands.Choice(name=b, value=b) for b in ("home", "barracks", "market", "wall")])
async def build(interaction: discord.Interaction, tile: str, building: app_commands.Choice[str]):
    await run(interaction, game.build, interaction.user.id, tile.strip(), building.value)


@build.autocomplete("tile")
async def build_tiles(interaction: discord.Interaction, current: str):
    nid = my_nation(interaction)
    if nid is None:
        return []
    free = [k for k in W.owned(game.world, nid) if game.world["tiles"][k]["building"] is None]
    return tile_choices(free, current)


@tree.command(description="Turn workers into soldiers (needs barracks room, ore and food)")
async def train(interaction: discord.Interaction, count: app_commands.Range[int, 1, 500]):
    await run(interaction, game.train, interaction.user.id, count)


@tree.command(description="March an army toward a tile (attacks if it is enemy land)")
@app_commands.describe(army="Army number from /status", tile="Destination like 3,-2")
async def move(interaction: discord.Interaction, army: int, tile: str):
    await run(interaction, game.move, interaction.user.id, army, tile.strip())


@move.autocomplete("tile")
async def move_tiles(interaction: discord.Interaction, current: str):
    if game.phase != "running":
        return []
    land = [k for k, t in game.world["tiles"].items() if t["terrain"] != "water"]
    return tile_choices(sorted(land), current)


@tree.command(description="Set work priorities 0-5, e.g. 'food 5, wood 3, oil 0'")
async def priorities(interaction: discord.Interaction, settings: str):
    await run(interaction, game.priorities, interaction.user.id, settings)


@tree.command(description="Offer a trade. You need a Market, and so does they.")
@app_commands.describe(to="Nation name", give="What you give, e.g. 'wood 10'", get="What you want, e.g. 'stone 5'")
async def trade(interaction: discord.Interaction, to: str, give: str, get: str = ""):
    await run(interaction, game.trade_offer, interaction.user.id, to, give, get)


@tree.command(description="Accept a trade offer (see /status for offer numbers)")
async def accept(interaction: discord.Interaction, offer: int):
    await run(interaction, game.trade_accept, interaction.user.id, offer)


@tree.command(description="Decline a trade offer")
async def decline(interaction: discord.Interaction, offer: int):
    await run(interaction, game.trade_decline, interaction.user.id, offer)


# ---------- admin commands (need the admin role) ----------

async def admin_run(interaction, fn, *args, public=False):
    """Run an admin action. public=True announces the result to everyone."""
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    try:
        text = fn(*args)
    except GameError as e:
        return await say(interaction, "Can't do that: %s" % e)
    if public:
        await publish(interaction, text)
    else:
        await say(interaction, text)


@admin.command(name="open", description="Open sign-ups for a mode")
@app_commands.choices(mode=[app_commands.Choice(name=m, value=m) for m in Game.modes()])
async def admin_open(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    await admin_run(interaction, game.open_signup, mode.value, public=True)


@admin.command(name="start", description="Start the game with everyone who joined. Reports post in this channel.")
async def admin_start(interaction: discord.Interaction, radius: app_commands.Range[int, 3, 15] = 8):
    if is_admin(interaction):
        store.set("report_channel", interaction.channel_id)
        store.set("last_tick", time.time())
    await admin_run(interaction, game.start, radius, public=True)


@admin.command(name="announcements", description="Choose the channel for announcements and nightly reports")
async def admin_announcements(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    store.set("announce_channel", channel.id)
    await say(interaction, "Announcements and nightly reports will go to #%s." % channel.name)


@admin.command(name="announce", description="Post a message to the announcements channel")
async def admin_announce(interaction: discord.Interaction, message: str):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    await publish(interaction, message)


@admin.command(name="tick", description="Run a tick right now")
async def admin_tick(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    await interaction.response.defer(ephemeral=True)
    try:
        await do_tick()
        await interaction.followup.send("Tick done.", ephemeral=True)
    except GameError as e:
        await interaction.followup.send("Can't do that: %s" % e, ephemeral=True)


@admin.command(name="pause", description="Pause or resume the automatic ticks")
async def admin_pause(interaction: discord.Interaction, paused: bool):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    store.set("paused", paused)
    await say(interaction, "Paused." if paused else "Resumed.")


@admin.command(name="fog", description="Turn fog of war on or off")
async def admin_fog(interaction: discord.Interaction, on: bool):
    await admin_run(interaction, game.set_fog, on, public=True)


@admin.command(name="hiddenoil", description="Hide oil deposits until someone claims the land")
async def admin_hiddenoil(interaction: discord.Interaction, on: bool):
    await admin_run(interaction, game.set_hidden_oil, on, public=True)


@admin.command(name="expand", description="Add rings of new land to the map")
async def admin_expand(interaction: discord.Interaction, rings: app_commands.Range[int, 1, 5] = 1):
    await admin_run(interaction, game.expand, rings, public=True)


@admin.command(name="empire", description="Set the land percent needed to win (Feudalism)")
async def admin_empire(interaction: discord.Interaction, percent: int):
    await admin_run(interaction, game.set_empire, percent, public=True)


@admin.command(name="addplayer", description="Add a late joiner to a running game")
async def admin_addplayer(interaction: discord.Interaction, user: discord.Member, name: str):
    await admin_run(interaction, game.add_player, user.id, name, public=True)


@admin.command(name="rollback", description="Restore the world to the state after a past tick")
async def admin_rollback(interaction: discord.Interaction, tick: int):
    await admin_run(interaction, game.rollback, tick)


@admin.command(name="end", description="End the game; the land leader wins")
async def admin_end(interaction: discord.Interaction):
    await admin_run(interaction, game.end, public=True)


tree.add_command(admin)


# ---------- ticks and roles ----------

async def sync_roles(guild):
    """Give each player the Discord role matching their title. Needs Manage Roles and a bot role above them."""
    titles = game.titles()
    if not titles:
        return
    names = [r["title"] for r in game.rules["ranks"]]
    roles = {}
    for name in names:
        roles[name] = discord.utils.get(guild.roles, name=name) or await guild.create_role(name=name, reason="Game title")
    for uid, title in titles.items():
        try:
            member = await guild.fetch_member(int(uid))
        except discord.NotFound:
            continue
        have = [r for r in member.roles if r.name in names]
        if [r.name for r in have] != [title]:
            await member.remove_roles(*have, reason="Title changed") if have else None
            await member.add_roles(roles[title], reason="Title earned")


async def do_tick():
    report = game.run_tick()
    store.set("last_tick", time.time())
    channel = announce_channel()
    if channel:
        data = render.render(game.world, game.rules)
        await channel.send(report, file=png(data, "tick.png"))
        try:
            await sync_roles(channel.guild)
        except discord.Forbidden:
            await channel.send("I couldn't update title roles. Give me Manage Roles and put my role above the title roles.")


def tick_is_due():
    last = store.get("last_tick", 0)
    now = time.time()
    if TICK_MINUTES:
        return now - last >= float(TICK_MINUTES) * 60
    tz = ZoneInfo(TICK_TZ)
    hour, minute = (int(x) for x in TICK_TIME.split(":"))
    today = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= today.timestamp() > last


@tasks.loop(seconds=30)
async def ticker():
    if game.phase == "running" and not store.get("paused", False) and tick_is_due():
        await do_tick()


@client.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    if not ticker.is_running():
        ticker.start()
    print("Logged in as %s" % client.user)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env first (see .env.example)")
    client.run(TOKEN)
