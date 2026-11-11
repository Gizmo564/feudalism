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

from population import mapnav, render, ruleset
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
    if game.phase not in ("running", "over"):
        return await say(interaction, "Can't do that: there is no game running")
    nid = game.players.get(str(interaction.user.id))
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(file=png(overview(nid)), ephemeral=True)


@tree.command(description="Interactive map around your capital: pan, zoom, click a tile to claim, build or move")
@app_commands.describe(radius="How many tiles out to show (1-6)")
async def zoom(interaction: discord.Interaction, radius: app_commands.Range[int, 1, 6] = 3):
    nid = my_nation(interaction)
    if nid is None:
        return await say(interaction, "Can't do that: you need to be a player in a running game (try /map)")
    await interaction.response.defer(ephemeral=True)
    view = MapView(interaction.user.id, nid, radius)
    await interaction.followup.send(content=view.text(), file=png(view.image()), view=view, ephemeral=True)


def overview(nid=None):
    """The whole-map picture: terrain as colour, claimable tiles outlined for a player."""
    return render.render(game.world, game.rules, nid=nid, tint=True, icons=False, claim=nid is not None, tidy=True)


DIR_BUTTONS = {"nw": "\u2196", "ne": "\u2197", "w": "\u25c0", "e": "\u25b6", "sw": "\u2199", "se": "\u2198"}
BUILD_EMOJI = {"home": "\U0001f3e0", "barracks": "\U0001f6e1\ufe0f", "market": "\U0001f3ea", "wall": "\U0001f9f1"}


class MapView(discord.ui.View):
    """Buttons and menus under the zoomed map. Only the player who opened it can use it (it is private)."""

    def __init__(self, uid, nid, radius=3):
        super().__init__(timeout=900)
        self.uid, self.nid, self.radius, self.icons = uid, nid, radius, False
        self.center = game.world["nations"][nid]["capital"]
        self.selected = None
        self.layout()

    async def interaction_check(self, interaction):
        return interaction.user.id == self.uid

    def image(self):
        return render.render(game.world, game.rules, nid=self.nid, center=self.center, radius=self.radius, tile=48,
                             legend=False, coords="claim", tint=True, icons=self.icons, claim=True, tidy=True,
                             mark=self.selected)

    def text(self):
        if self.selected is None:
            return "Pick a tile from the menu to claim, build or move there. Outlined tiles are free land you can claim."
        return mapnav.tile_card(game.world, game.rules, self.nid, self.selected)["text"]

    def add(self, item, row):
        item.row = row
        self.add_item(item)

    def button(self, label, row, callback, style=discord.ButtonStyle.secondary, disabled=False):
        b = discord.ui.Button(label=label, style=style, disabled=disabled)
        b.callback = callback
        self.add(b, row)

    def layout(self):
        self.clear_items()
        world, rules = game.world, game.rules
        for row, names in enumerate((("nw", "ne", "home", "sw", "se"), ("w", "e", "out", "in", "icons"))):
            for name in names:
                if name in DIR_BUTTONS:
                    self.button(DIR_BUTTONS[name], row, self.mover(name))
                elif name == "home":
                    self.button("Center", row, self.go_home)
                elif name == "out":
                    self.button("-", row, self.resize(1), disabled=self.radius >= 6)
                elif name == "in":
                    self.button("+", row, self.resize(-1), disabled=self.radius <= 1)
                else:
                    self.button("Icons: on" if self.icons else "Icons: off", row, self.toggle_icons,
                                discord.ButtonStyle.primary if self.icons else discord.ButtonStyle.secondary)
        options = mapnav.pick_options(world, rules, self.nid, self.center, self.radius)
        if options:
            menu = discord.ui.Select(placeholder="Pick a tile...", options=[
                discord.SelectOption(label=k, description=text[:100], value=k, default=k == self.selected)
                for k, text in options])
            menu.callback = self.picker(menu)
            self.add(menu, 2)
        if self.selected is None:
            return
        card = mapnav.tile_card(world, rules, self.nid, self.selected)
        if card["can_claim"]:
            self.button("Claim", 3, self.claim_here, discord.ButtonStyle.success)
        for b in card["builds"]:
            self.button(b.capitalize(), 3, self.build_here(b), discord.ButtonStyle.primary)
        armies = mapnav.my_armies(world, self.nid)
        if armies and card["can_move"]:
            soldier = game.word("soldier")
            menu = discord.ui.Select(placeholder="Send an army here...", options=[
                discord.SelectOption(label="Army %d (%d %ss)" % (a["id"], a["n"], soldier), description="at " + a["tile"],
                                     value=str(a["id"])) for a in armies[:25]])
            menu.callback = self.send_army(menu)
            self.add(menu, 4)

    async def redraw(self, interaction):
        self.layout()
        await interaction.response.defer()
        await interaction.edit_original_response(content=self.text(), attachments=[png(self.image())], view=self)

    def mover(self, name):
        async def cb(interaction):
            self.center = mapnav.shift(game.world, self.center, name)
            await self.redraw(interaction)
        return cb

    async def go_home(self, interaction):
        self.center = game.world["nations"][self.nid]["capital"]
        await self.redraw(interaction)

    def resize(self, change):
        async def cb(interaction):
            self.radius = max(1, min(6, self.radius + change))
            await self.redraw(interaction)
        return cb

    async def toggle_icons(self, interaction):
        self.icons = not self.icons
        await self.redraw(interaction)

    def picker(self, menu):
        async def cb(interaction):
            self.selected = menu.values[0]
            await self.redraw(interaction)
        return cb

    async def order(self, interaction, fn, *args):
        """Queue an order and tell the player privately; the map stays open."""
        try:
            text = fn(interaction.user.id, *args)
        except (GameError, ValueError) as e:
            text = "Can't do that: %s" % e
        await interaction.response.send_message(text, ephemeral=True)

    async def claim_here(self, interaction):
        await self.order(interaction, game.claim, self.selected)

    def build_here(self, building):
        async def cb(interaction):
            await self.order(interaction, game.build, self.selected, building)
        return cb

    def send_army(self, menu):
        async def cb(interaction):
            await self.order(interaction, game.move, int(menu.values[0]), self.selected)
        return cb


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
        data = overview()
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
