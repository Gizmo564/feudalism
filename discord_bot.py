"""The Discord bot. Run with:  python3 discord_bot.py   (reads settings from .env)
All game rules live in the population/ package; this file only talks to Discord."""
import io
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

from population import guide, infopage, mapnav, render, rulepanel, ruleset
from population.game import Game, GameError
from population.store import Store
from population.tutorial import Tutorial
from population.hexes import neighbors
from population import world as W
from population import tick
from population import trade as trade_mod


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
DM_REPORTS = os.environ.get("DM_REPORTS", "1").strip().lower() not in ("0", "false", "no", "off")

store = Store(os.environ.get("DB_PATH", "game.db"))
game = Game(store)
client = discord.Client(intents=discord.Intents.default())   # no privileged intents needed
tree = app_commands.CommandTree(client)
admin = app_commands.Group(name="admin", description="Admin commands")


# ---------- small helpers ----------

tutorials = {}      # discord user id -> Tutorial. Practice games live in memory only.


@tree.error
async def command_failed(interaction, error):
    """Log any crash (so `feudalism logs` shows it) and tell the player instead of leaving them hanging."""
    logging.getLogger("feudalism").error("Command failed: %s", getattr(interaction.command, "qualified_name", "?"),
                                         exc_info=error)
    text = "Something went wrong on my side. The admin can see the details in the log."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


def game_for(user_id):
    """The game a player is looking at: their practice game if they have one, otherwise the real game."""
    t = tutorials.get(user_id)
    return t.game if t else game


async def say(interaction, text, file=None, public=False, **kwargs):
    """Reply to a command. Private ("only you can see this") unless public=True.
    Anyone in a practice game always gets private replies, tagged so they know it is practice."""
    parent = getattr(interaction.command, "parent", None)
    if interaction.user.id in tutorials and parent is not admin:
        public = False
        if text:
            text = "**[Practice game]** " + text
    kwargs["ephemeral"] = not public
    if file:
        kwargs["file"] = file
    await interaction.response.send_message(text, **kwargs)


async def run(interaction, fn, *args, public=False):
    """Call a Game method and show its text. Errors are always private."""
    try:
        text = fn(*args)
    except (GameError, ValueError) as e:
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
    g = game_for(interaction.user.id)
    nid = g.players.get(str(interaction.user.id))
    return nid if g.phase == "running" and nid is not None else None


def make_embed(page):
    """Turn an infopage dict into a Discord embed."""
    embed = discord.Embed(title=page["title"], description=page["description"] or None,
                          colour=discord.Colour.from_rgb(*page["color"]))
    for name, value, inline in page["fields"]:
        embed.add_field(name=name, value=value, inline=inline)
    if page["footer"]:
        embed.set_footer(text=page["footer"][:2000])
    return embed


async def dm(user_id, text):
    """Send a private message. Quietly gives up if the person has DMs closed."""
    try:
        user = await client.fetch_user(int(user_id))
        await user.send(text[:2000])
    except (discord.HTTPException, ValueError):
        pass


def log_channel():
    channel_id = store.get("log_channel")
    return client.get_channel(channel_id) if channel_id else None


# ---------- player commands ----------

@tree.command(description="Join the game that is open for sign-ups")
@app_commands.describe(name="Your nation's name (2-20 characters)")
async def join(interaction: discord.Interaction, name: str):
    try:
        text = game.join(interaction.user.id, name)
    except GameError as e:
        return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
    await interaction.response.send_message(text)                       # public: everyone sees who joined
    await interaction.followup.send(game.welcome(name), ephemeral=True)   # private: what happens next


@tree.command(description="Your nation: what needs attention, land, people, stock, and orders (only you see this)")
async def status(interaction: discord.Interaction):
    await run(interaction, game_for(interaction.user.id).status, interaction.user.id)


@tree.command(description="Your nation in detail, with tabs. Change work priorities with buttons (only you see this)")
async def info(interaction: discord.Interaction):
    g = game_for(interaction.user.id)
    try:
        g.nid(interaction.user.id)
    except GameError as e:
        return await say(interaction, "Can't do that: %s" % e)
    view = InfoView(interaction.user.id, g)
    await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)


@tree.command(description="What happened to you last night: gains, losses, and news (only you see this)")
async def report(interaction: discord.Interaction):
    await run(interaction, game_for(interaction.user.id).report, interaction.user.id)


@tree.command(name="help", description="How to play, in a few short pages")
async def help_command(interaction: discord.Interaction):
    g = game_for(interaction.user.id)
    try:
        text = g.help("basics")
    except GameError as e:
        return await say(interaction, "Can't do that: %s" % e)
    view = HelpView(interaction.user.id, g)
    await interaction.response.send_message(text, view=view, ephemeral=True)


@tree.command(description="Clear tonight's queued orders so you can start over")
async def cancel(interaction: discord.Interaction):
    await run(interaction, game_for(interaction.user.id).cancel, interaction.user.id)


@tree.command(description="Everyone's land and population, and how close anyone is to winning")
async def standings(interaction: discord.Interaction):
    await run(interaction, game_for(interaction.user.id).standings, public=True)


@tree.command(description="The whole map as you can see it")
async def map(interaction: discord.Interaction):
    g = game_for(interaction.user.id)
    if g.phase not in ("running", "over"):
        return await say(interaction, "Can't do that: there is no game running")
    nid = g.players.get(str(interaction.user.id))
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(file=png(overview(g, nid)), ephemeral=True)


@tree.command(description="Interactive map around your capital: pan, zoom, click a tile to claim, build or move")
@app_commands.describe(radius="How many tiles out to show (1-6)")
async def zoom(interaction: discord.Interaction, radius: app_commands.Range[int, 1, 6] = 3):
    nid = my_nation(interaction)
    if nid is None:
        return await say(interaction, "Can't do that: you need to be a player in a running game (try /map)")
    await interaction.response.defer(ephemeral=True)
    view = MapView(interaction.user.id, nid, radius, game_for(interaction.user.id))
    await interaction.followup.send(content=view.text(), file=png(view.image()), view=view, ephemeral=True)


def overview(g=None, nid=None):
    """The whole-map picture: terrain as colour, claimable tiles outlined for a player."""
    g = g or game
    return render.render(g.world, g.rules, nid=nid, tint=True, icons=False, claim=nid is not None, tidy=True)


def money(res):
    return ", ".join("%d %s" % (v, k) for k, v in res.items() if v) or "nothing"


class InfoView(discord.ui.View):
    """The /info page: four tabs, and on the Economy tab buttons that change work priorities."""

    def __init__(self, uid, g):
        super().__init__(timeout=900)
        self.uid, self.g, self.tab, self.resource = uid, g, "overview", "food"
        self.layout()

    async def interaction_check(self, interaction):
        return interaction.user.id == self.uid

    def embed(self):
        return make_embed(self.g.info_page(self.uid, self.tab, self.resource))

    def layout(self):
        self.clear_items()
        for tab in infopage.TABS:
            b = discord.ui.Button(label=tab.capitalize(), row=0, style=discord.ButtonStyle.primary if tab == self.tab
                                  else discord.ButtonStyle.secondary)
            b.callback = self.switch(tab)
            self.add_item(b)
        if self.tab != "economy":
            return
        prios = self.g.world["nations"][self.g.nid(self.uid)]["priorities"]
        can = infopage.available(self.g.world, self.g.rules, self.g.nid(self.uid))
        if self.resource not in can:
            self.resource = can[0]
        menu = discord.ui.Select(placeholder="Pick a resource...", row=1, options=[
            discord.SelectOption(label=r.capitalize(), value=r, description="priority %d of 5" % prios[r],
                                 default=r == self.resource) for r in can])
        menu.callback = self.pick(menu)
        self.add_item(menu)
        value = prios[self.resource]
        off = discord.ui.Button(label="Off", row=2, disabled=value <= 0)
        off.callback = self.change(to=0)
        minus = discord.ui.Button(label="-", row=2, disabled=value <= 0)
        minus.callback = self.change(-1)
        label = discord.ui.Button(label="%s: %d/5" % (self.resource, value), row=2, disabled=True)
        plus = discord.ui.Button(label="+", row=2, disabled=value >= 5)
        plus.callback = self.change(1)
        top = discord.ui.Button(label="Max", row=2, disabled=value >= 5)
        top.callback = self.change(to=5)
        for item in (off, minus, label, plus, top):
            self.add_item(item)

    async def refresh(self, interaction):
        self.layout()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    def switch(self, tab):
        async def cb(interaction):
            self.tab = tab
            await self.refresh(interaction)
        return cb

    def pick(self, menu):
        async def cb(interaction):
            self.resource = menu.values[0]
            await self.refresh(interaction)
        return cb

    def change(self, step=0, to=None):
        async def cb(interaction):
            current = self.g.world["nations"][self.g.nid(self.uid)]["priorities"][self.resource]
            try:
                self.g.set_priority(self.uid, self.resource, current + step if to is None else to)
            except (GameError, ValueError) as e:
                return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
            await self.refresh(interaction)
        return cb


class HelpView(discord.ui.View):
    """Five short help pages, one button each."""

    def __init__(self, uid, g):
        super().__init__(timeout=900)
        self.uid, self.g = uid, g
        for tab in guide.HELP_TABS:
            b = discord.ui.Button(label=tab.capitalize(), style=discord.ButtonStyle.secondary)
            b.callback = self.show(tab)
            self.add_item(b)

    async def interaction_check(self, interaction):
        return interaction.user.id == self.uid

    def show(self, tab):
        async def cb(interaction):
            await interaction.response.edit_message(content=self.g.help(tab), view=self)
        return cb


DIR_BUTTONS = {"nw": "\u2196", "ne": "\u2197", "w": "\u2190", "e": "\u2192", "sw": "\u2199", "se": "\u2198"}
LAYERS = (("icons", "Terrain icons", "Draw a small picture on each tile"),
          ("buildings", "Buildings", "Show homes, barracks, markets and walls"),
          ("people", "Housing numbers", "Label buildings with how many people they house"),
          ("production", "Production", "Show what each of your tiles makes tonight"),
          ("coords", "Coordinates", "Label every tile with its number"))
LETTER = {"food": "F", "livestock": "L", "wood": "W", "stone": "S", "ore": "O", "oil": "Oi"}


class MapView(discord.ui.View):
    """Buttons and menus under the zoomed map. Only the player who opened it can use it (it is private)."""

    def __init__(self, uid, nid, radius=3, g=None):
        super().__init__(timeout=900)
        self.uid, self.nid, self.radius, self.g = uid, nid, radius, g or game
        self.practice = uid in tutorials
        self.layers = {"icons": False, "buildings": True, "people": False, "production": False, "coords": False}
        self.center = self.g.world["nations"][nid]["capital"]
        self.selected = None
        self.choosing_army = False
        self.layout()

    async def interaction_check(self, interaction):
        return interaction.user.id == self.uid

    def image(self):
        return render.render(self.g.world, self.g.rules, nid=self.nid, center=self.center, radius=self.radius, tile=48,
                             legend=False, coords=True if self.layers["coords"] else "claim", tint=True, icons=self.layers["icons"], claim=True,
                             tidy=True, mark=self.selected, buildings=self.layers["buildings"],
                             people=self.layers["people"], production=self.production())

    def production(self):
        """{tile: 'F5'} for your working tiles: resource letter and workers, out of the per-tile maximum."""
        if not self.layers["production"]:
            return None
        plan = tick.tile_plan(self.g.world, self.g.rules, self.g.world["nations"][self.nid])
        return {k: " ".join("%s%d" % (LETTER[r], c) for r, (c, _) in per.items()) for k, per in plan.items()}

    def text(self):
        lines = ["**[Practice game]**"] if self.practice else []
        if self.selected is None:
            lines.append("Pick a tile from the menu to claim, build or move there. Outlined tiles are free land you can claim.")
        else:
            lines.append(mapnav.tile_card(self.g.world, self.g.rules, self.nid, self.selected)["text"])
        if self.layers["production"]:
            cap = self.g.rules["workers_per_tile"]
            lines.append("Tags show resource and workers (a tile takes up to %d). F food, L livestock, W wood, S stone, O ore." % cap)
        if self.layers["people"]:
            hw, hs = W.housing(self.g.world, self.g.rules, self.nid)
            n = self.g.world["nations"][self.nid]
            lines.append("Housing: %d/%d %ss, %d/%d %ss" % (n["workers"], hw, self.g.word("worker"),
                                                           W.soldiers(self.g.world, self.nid), hs, self.g.word("soldier")))
        return "\n".join(lines)

    def add(self, item, row):
        item.row = row
        self.add_item(item)

    def button(self, label, row, callback, style=discord.ButtonStyle.secondary, disabled=False):
        b = discord.ui.Button(label=label, style=style, disabled=disabled)
        b.callback = callback
        self.add(b, row)

    def layout(self):
        """Row 0-1: move and zoom. Row 2: layers. Row 3: tile picker. Row 4: actions for the picked tile
        (with their costs; greyed out when you can't pay)."""
        self.clear_items()
        world, rules = self.g.world, self.g.rules
        for name in ("nw", "ne"):
            self.button(DIR_BUTTONS[name], 0, self.mover(name))
        self.button("Center", 0, self.go_home, discord.ButtonStyle.primary)
        for name in ("sw", "se"):
            self.button(DIR_BUTTONS[name], 0, self.mover(name))
        for name in ("w", "e"):
            self.button(DIR_BUTTONS[name], 1, self.mover(name))
        self.button("Zoom out", 1, self.resize(1), disabled=self.radius >= 6)
        self.button("Zoom in", 1, self.resize(-1), disabled=self.radius <= 1)
        layers = discord.ui.Select(placeholder="Show on map...", min_values=0, max_values=len(LAYERS), options=[
            discord.SelectOption(label=label, description=desc, value=key, default=self.layers[key])
            for key, label, desc in LAYERS])
        layers.callback = self.set_layers(layers)
        self.add(layers, 2)
        options = mapnav.pick_options(world, rules, self.nid, self.center, self.radius)
        if options:
            menu = discord.ui.Select(placeholder="Pick a tile...", options=[
                discord.SelectOption(label=k, description=text[:100], value=k, default=k == self.selected)
                for k, text in options])
            menu.callback = self.picker(menu)
            self.add(menu, 3)
        if self.selected is None:
            return
        card = mapnav.tile_card(world, rules, self.nid, self.selected)
        armies = mapnav.my_armies(world, self.nid)
        if self.choosing_army and armies and card["can_move"]:
            soldier = self.g.word("soldier")
            menu = discord.ui.Select(placeholder="Which army goes there?", options=[
                discord.SelectOption(label="Army %d (%d %ss)" % (a["id"], a["n"], soldier), description="at " + a["tile"],
                                     value=str(a["id"])) for a in armies[:25]])
            menu.callback = self.send_army(menu)
            self.add(menu, 4)
            return
        if card["can_claim"]:
            if card["claim_queued"]:
                self.button("Claim (one already queued)", 4, self.claim_here, disabled=True)
            elif card["claim_missing"]:
                self.button("Claim: short %s" % money(card["claim_missing"]), 4, self.claim_here, disabled=True)
            else:
                self.button("Claim (%s)" % money(card["claim_cost"]), 4, self.claim_here, discord.ButtonStyle.success)
        for b in card["builds"]:
            missing = card["build_missing"][b]
            label = "%s: short %s" % (b.capitalize(), money(missing)) if missing else "%s (%s)" % (
                b.capitalize(), money(card["build_cost"][b]))
            self.button(label[:80], 4, self.build_here(b), discord.ButtonStyle.primary, disabled=bool(missing))
        if armies and card["can_move"]:
            self.button("Send army", 4, self.pick_army)

    async def redraw(self, interaction):
        self.layout()
        await interaction.response.defer()
        await interaction.edit_original_response(content=self.text(), attachments=[png(self.image())], view=self)

    def mover(self, name):
        async def cb(interaction):
            self.center = mapnav.shift(self.g.world, self.center, name)
            await self.redraw(interaction)
        return cb

    async def go_home(self, interaction):
        self.center = self.g.world["nations"][self.nid]["capital"]
        await self.redraw(interaction)

    def resize(self, change):
        async def cb(interaction):
            self.radius = max(1, min(6, self.radius + change))
            await self.redraw(interaction)
        return cb

    def set_layers(self, menu):
        async def cb(interaction):
            for key, _, _ in LAYERS:
                self.layers[key] = key in menu.values
            await self.redraw(interaction)
        return cb

    def picker(self, menu):
        async def cb(interaction):
            self.selected = menu.values[0]
            self.choosing_army = False
            await self.redraw(interaction)
        return cb

    async def pick_army(self, interaction):
        self.choosing_army = True
        await self.redraw(interaction)

    async def order(self, interaction, fn, *args):
        """Queue an order and tell the player privately; the map stays open."""
        try:
            text = fn(interaction.user.id, *args)
        except (GameError, ValueError) as e:
            text = "Can't do that: %s" % e
        await interaction.response.send_message(text, ephemeral=True)

    async def claim_here(self, interaction):
        await self.order(interaction, self.g.claim, self.selected)

    def build_here(self, building):
        async def cb(interaction):
            await self.order(interaction, self.g.build, self.selected, building)
        return cb

    def send_army(self, menu):
        async def cb(interaction):
            self.choosing_army = False
            await self.order(interaction, self.g.move, int(menu.values[0]), self.selected)
        return cb


@tree.command(description="Claim a free tile next to your land (1 per night, cost rises as you grow)")
@app_commands.describe(tile="Tile coordinates like 3,-2")
async def claim(interaction: discord.Interaction, tile: str):
    await run(interaction, game_for(interaction.user.id).claim, interaction.user.id, tile.strip())


@claim.autocomplete("tile")
async def claim_tiles(interaction: discord.Interaction, current: str):
    nid = my_nation(interaction)
    if nid is None:
        return []
    g = game_for(interaction.user.id)
    free = {n for k in W.owned(g.world, nid) for n in neighbors(k) if W.is_free_land(g.world, n)}
    return tile_choices(sorted(free), current)


@tree.command(description="Build on one of your tiles")
@app_commands.describe(tile="Your tile, like 3,-2", building="home, barracks, market or wall")
@app_commands.choices(building=[app_commands.Choice(name=b, value=b) for b in ("home", "barracks", "market", "wall")])
async def build(interaction: discord.Interaction, tile: str, building: app_commands.Choice[str]):
    await run(interaction, game_for(interaction.user.id).build, interaction.user.id, tile.strip(), building.value)


@build.autocomplete("tile")
async def build_tiles(interaction: discord.Interaction, current: str):
    nid = my_nation(interaction)
    if nid is None:
        return []
    g = game_for(interaction.user.id)
    free = [k for k in W.owned(g.world, nid) if g.world["tiles"][k]["building"] is None]
    return tile_choices(free, current)


@tree.command(description="Turn workers into soldiers (needs barracks room, ore and food)")
async def train(interaction: discord.Interaction, count: app_commands.Range[int, 1, 500]):
    await run(interaction, game_for(interaction.user.id).train, interaction.user.id, count)


@tree.command(description="March an army toward a tile (attacks if it is enemy land)")
@app_commands.describe(army="Army number from /status", tile="Destination like 3,-2")
async def move(interaction: discord.Interaction, army: int, tile: str):
    await run(interaction, game_for(interaction.user.id).move, interaction.user.id, army, tile.strip())


@move.autocomplete("tile")
async def move_tiles(interaction: discord.Interaction, current: str):
    g = game_for(interaction.user.id)
    if g.phase != "running":
        return []
    land = [k for k, t in g.world["tiles"].items() if t["terrain"] != "water"]
    return tile_choices(sorted(land), current)


@tree.command(description="Set work priorities 0-5, e.g. 'food 5, wood 3, oil 0' (or use the buttons in /info)")
async def priorities(interaction: discord.Interaction, settings: str):
    await run(interaction, game_for(interaction.user.id).priorities, interaction.user.id, settings)


def trade_channel():
    channel_id = store.get("trade_channel")
    return client.get_channel(channel_id) if channel_id else announce_channel()


def offer_embed(g, o):
    """The public trade-board card for one offer."""
    status = o["status"]
    color = {"open": 0xC8A24A, "accepted": 0x4C9F70}.get(status, 0x8A8A8A)
    text = g.offer_line(o)
    if status != "open":
        who = g.world["nations"][o["to"]]["name"] if o["to"] is not None else ""
        text += "\n**%s**" % {"accepted": "Accepted by %s." % who, "declined": "Declined.", "withdrawn": "Withdrawn.",
                              "expired": "Expired."}.get(status, status)
    else:
        text += "\nExpires after %d nights." % g.rules["trade"]["offer_expires_ticks"]
    return discord.Embed(title="Trade board", description=text, color=color)


class TradeButton(discord.ui.DynamicItem[discord.ui.Button], template=r"trade:(?P<act>accept|decline):(?P<id>\d+)"):
    """Accept/Decline under a public offer. The custom id carries the offer number, so these keep working after a restart."""

    def __init__(self, act, tid):
        accept = act == "accept"
        super().__init__(discord.ui.Button(label="Accept" if accept else "Decline",
                                           style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
                                           custom_id="trade:%s:%d" % (act, tid)))
        self.act, self.tid = act, tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["id"]))

    async def callback(self, interaction):
        try:
            if self.act == "accept":
                text = game.trade_accept(interaction.user.id, self.tid)
            else:
                text = game.trade_decline(interaction.user.id, self.tid)
        except (GameError, ValueError) as e:
            return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
        o = trade_mod.find(game.world, self.tid)
        o["shown"] = True
        game.save()
        await interaction.response.edit_message(embed=offer_embed(game, o), view=None)
        await interaction.followup.send(text, ephemeral=True)


def offer_view(tid):
    view = discord.ui.View(timeout=None)
    view.add_item(TradeButton("accept", tid))
    view.add_item(TradeButton("decline", tid))
    return view


async def post_offer(tid):
    """Show an offer with Accept/Decline buttons: on the public board, or by DM for a private offer.
    Returns True if it was posted."""
    o = trade_mod.find(game.world, tid)
    if o.get("private"):
        uid = next((u for u, n in game.players.items() if n == o["to"]), None)
        try:
            channel = await (await client.fetch_user(int(uid))).create_dm()
        except (discord.HTTPException, TypeError, ValueError):
            return False
    else:
        channel = trade_channel()
    if channel is None:
        return False
    try:
        msg = await channel.send(embed=offer_embed(game, o), view=offer_view(tid))
    except discord.Forbidden:
        return False
    o["post"] = [msg.channel.id, msg.id]
    game.save()
    return True


async def refresh_board():
    """After a night: edit board cards whose offers expired, so nobody clicks a dead button."""
    for o in game.world["trades"]:
        if o.get("post") and o["status"] != "open" and not o.get("shown"):
            o["shown"] = True
            try:
                channel = client.get_channel(o["post"][0]) or await client.fetch_channel(o["post"][0])
                msg = await channel.fetch_message(o["post"][1])
                await msg.edit(embed=offer_embed(game, o), view=None)
            except discord.HTTPException:
                pass
    game.save()


@tree.command(description="Offer a trade. Leave 'to' empty to post it on the trade board for anyone with a Market.")
@app_commands.describe(give="What you give, e.g. 'wood 10'", get="What you want, e.g. 'stone 5'",
                       to="Nation name (leave empty for an open offer)",
                       who_sees="Public: posted on the trade board. Private: only you and the named nation see it")
@app_commands.choices(who_sees=[app_commands.Choice(name="Public (trade board)", value="public"),
                                app_commands.Choice(name="Private (only you and them)", value="private")])
async def trade(interaction: discord.Interaction, give: str, get: str = "", to: str = "", who_sees: str = "public"):
    g = game_for(interaction.user.id)
    private = who_sees == "private"
    try:
        tid, text = g.make_offer(interaction.user.id, to, give, get, private=private)
    except (GameError, ValueError) as e:
        return await say(interaction, "Can't do that: %s" % e)
    if g is game and await post_offer(tid):
        text += " They were sent it with buttons." if private else " It is on the trade board."
    elif private:
        text += " I couldn't DM them, so they will see it in /status and /offers."
    await say(interaction, text)


@tree.command(description="List the trade offers you can accept right now")
async def offers(interaction: discord.Interaction):
    g = game_for(interaction.user.id)
    nid = g.players.get(str(interaction.user.id))
    if nid is None:
        return await say(interaction, "You are not in this game.")
    lines = [g.offer_line(o) for o in g.world["trades"]
             if o["status"] == "open" and o["from"] != nid and o["to"] in (nid, None)]
    await say(interaction, "\n".join(lines) or "No open offers right now. Post one with /trade.")


@tree.command(description="Accept a trade offer (see /status for offer numbers)")
async def accept(interaction: discord.Interaction, offer: int):
    await run(interaction, game_for(interaction.user.id).trade_accept, interaction.user.id, offer)
    if game_for(interaction.user.id) is game:
        await refresh_board()


@tree.command(description="Decline a trade offer")
async def decline(interaction: discord.Interaction, offer: int):
    await run(interaction, game_for(interaction.user.id).trade_decline, interaction.user.id, offer)
    if game_for(interaction.user.id) is game:
        await refresh_board()


# ---------- pacts: written promises, kept as a record, never enforced ----------

pact = app_commands.Group(name="pact", description="Promises between nations. The game records them; it does not enforce them.")


@pact.command(name="propose", description="Propose a pact, e.g. 'no attacks until night 30'")
@app_commands.describe(nation="Who you are proposing to", terms="What you both promise (under 120 characters)")
async def pact_propose(interaction: discord.Interaction, nation: str, terms: str):
    await run(interaction, game_for(interaction.user.id).pact_propose, interaction.user.id, nation, terms)


@pact.command(name="accept", description="Accept a pact proposed to you. It goes on the public record.")
async def pact_accept(interaction: discord.Interaction, number: int):
    await run(interaction, game_for(interaction.user.id).pact_accept, interaction.user.id, number)


@pact.command(name="decline", description="Decline a proposal, or withdraw one you made")
async def pact_decline(interaction: discord.Interaction, number: int):
    await run(interaction, game_for(interaction.user.id).pact_decline, interaction.user.id, number)


@pact.command(name="break", description="End an active pact. Everyone will see that you broke it.")
async def pact_break(interaction: discord.Interaction, number: int):
    await run(interaction, game_for(interaction.user.id).pact_break, interaction.user.id, number)


@pact.command(name="list", description="Your pacts and proposals")
async def pact_list(interaction: discord.Interaction):
    await run(interaction, game_for(interaction.user.id).pact_list, interaction.user.id)


tree.add_command(pact)


# ---------- practice game ----------

practice = app_commands.Group(name="tutorial", description="Practise in a private test game")


@practice.command(name="start", description="Start a private practice game against two simple rivals")
async def tutorial_start(interaction: discord.Interaction):
    mode = (game.rules or {}).get("name", "feudalism").lower()
    t = Tutorial(interaction.user.id, mode if mode in Game.modes() else "feudalism")
    tutorials[interaction.user.id] = t
    await say(interaction, "Practice game started. Nothing here touches the real game, and every command now works on this "
                           "practice realm until you type /tutorial stop.\n\n" + t.hint())


@practice.command(name="night", description="Let one night pass in your practice game")
async def tutorial_night(interaction: discord.Interaction):
    t = tutorials.get(interaction.user.id)
    if t is None:
        return await say(interaction, "You have no practice game. Type /tutorial start.")
    t.night()
    await say(interaction, t.game.report(interaction.user.id) + "\n\n" + t.hint())


@practice.command(name="hint", description="What to try next in your practice game")
async def tutorial_hint(interaction: discord.Interaction):
    t = tutorials.get(interaction.user.id)
    await say(interaction, t.hint() if t else "You have no practice game. Type /tutorial start.")


@practice.command(name="stop", description="Leave the practice game and go back to the real one")
async def tutorial_stop(interaction: discord.Interaction):
    if tutorials.pop(interaction.user.id, None) is None:
        return await say(interaction, "You have no practice game.")
    await say(interaction, "Practice game closed. Your commands now work on the real game again.")


tree.add_command(practice)


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


@admin.command(name="close", description="Close sign-ups without starting (use closed=False to reopen)")
async def admin_close(interaction: discord.Interaction, closed: bool = True):
    await admin_run(interaction, game.set_signups_closed, closed, public=True)


@admin.command(name="start", description="Start the game with everyone who joined. Reports post in this channel.")
async def admin_start(interaction: discord.Interaction, radius: app_commands.Range[int, 3, 15] = 8):
    if is_admin(interaction):
        store.set("report_channel", interaction.channel_id)
        store.set("last_tick", time.time())
    await admin_run(interaction, game.start, radius, public=True)
    if is_admin(interaction) and game.phase == "running":
        for uid, text in game.start_messages().items():
            await dm(uid, text)


@admin.command(name="announcements", description="Choose the channel for announcements and nightly reports")
async def admin_announcements(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    store.set("announce_channel", channel.id)
    await say(interaction, "Announcements and nightly reports will go to #%s." % channel.name)


@admin.command(name="tradeboard", description="Choose the channel for public trade offers (empty = use announcements)")
async def admin_tradeboard(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    store.set("trade_channel", channel.id if channel else None)
    await say(interaction, "Trade offers will be posted in #%s." % channel.name if channel
              else "Trade offers will use the announcements channel.")


@admin.command(name="gamelog", description="Choose a channel for the nightly story of events (leave empty to turn off)")
async def admin_gamelog(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    store.set("log_channel", channel.id if channel else None)
    await say(interaction, "Nightly events will go to #%s." % channel.name if channel
              else "Game log turned off. Events will be part of the nightly report.")


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


class RulesView(discord.ui.View):
    """The admin's rules panel: pick a rule, then flip it or step it. Changes wait for the next tick."""

    def __init__(self, uid, g):
        super().__init__(timeout=900)
        self.uid, self.g = uid, g
        g.need_rules()
        self.key = rulepanel.keys(g.rules)[0]
        self.layout()

    async def interaction_check(self, interaction):
        return interaction.user.id == self.uid

    def rows(self):
        return {r["key"]: r for r in self.g.rule_rows()}

    def text(self):
        if self.g.phase == "signup":
            lines = ["**Game rules** - the game hasn't started yet, so changes apply right away.", ""]
        else:
            lines = ["**Game rules** - changes apply at the next tick and are announced.", ""]
        for r in self.rows().values():
            now = rulepanel.show(r["now"])
            value = "%s -> **%s** (next tick)" % (now, rulepanel.show(r["next"])) if r["next"] is not None else "**%s**" % now
            lines.append("%s%s: %s" % ("\u25b6 " if r["key"] == self.key else "", r["label"], value))
        r = self.rows()[self.key]
        lines += ["", r["note"]]
        if r["risky"]:
            lines.append("Careful: this changes the whole economy.")
        return "\n".join(lines)

    def layout(self):
        self.clear_items()
        rows = self.rows()
        menu = discord.ui.Select(placeholder="Pick a rule...", row=0, options=[
            discord.SelectOption(label=r["label"], value=k, default=k == self.key,
                                 description="now %s%s" % (rulepanel.show(r["now"]),
                                                           "" if r["next"] is None else ", next %s" % rulepanel.show(r["next"])))
            for k, r in rows.items()])
        menu.callback = self.pick(menu)
        self.add_item(menu)
        r = rows[self.key]
        target = r["now"] if r["next"] is None else r["next"]
        if r["kind"] == "switch":
            self.add_item(self.button("Turn %s" % ("off" if target else "on"), self.set_to(not target)))
        else:
            lo, hi, step = r["limits"]
            self.add_item(self.button("-%d" % step, self.set_to(target - step), disabled=target - step < lo))
            self.add_item(self.button("%s" % target, None, disabled=True))
            self.add_item(self.button("+%d" % step, self.set_to(target + step), disabled=target + step > hi))
        self.add_item(self.button("Undo change", self.set_to(r["now"]), disabled=r["next"] is None))

    def button(self, label, callback, disabled=False):
        b = discord.ui.Button(label=label, row=1, disabled=disabled)
        if callback:
            b.callback = callback
        return b

    async def refresh(self, interaction):
        self.layout()
        await interaction.response.edit_message(content=self.text(), view=self)

    def pick(self, menu):
        async def cb(interaction):
            self.key = menu.values[0]
            await self.refresh(interaction)
        return cb

    def set_to(self, value):
        async def cb(interaction):
            try:
                self.g.queue_rule(self.key, value)
            except GameError as e:
                return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
            await self.refresh(interaction)
        return cb


@admin.command(name="rules", description="Change game rules (switches and key numbers) at the next tick")
async def admin_rules(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    try:
        view = RulesView(interaction.user.id, game)
    except GameError as e:
        return await say(interaction, "Can't do that: %s" % e)
    await interaction.response.send_message(view.text(), view=view, ephemeral=True)


@admin.command(name="expand", description="Add rings of new land to the map")
async def admin_expand(interaction: discord.Interaction, rings: app_commands.Range[int, 1, 5] = 1):
    await admin_run(interaction, game.expand, rings, public=True)


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
    log = log_channel()
    report = game.run_tick(split_log=log is not None)
    store.set("last_tick", time.time())
    channel = announce_channel()
    if channel:
        data = overview()
        await channel.send((report + "\nUse /report to see your own night.")[:1990], file=png(data, "tick.png"))
        try:
            await sync_roles(channel.guild)
        except discord.Forbidden:
            await channel.send("I couldn't update title roles. Give me Manage Roles and put my role above the title roles.")
    if log:
        events = game.public_events()
        text = "**Night %d**\n%s" % (game.world["tick"], "\n".join("- " + e for e in events) or "A quiet night.")
        for i in range(0, len(text), 1900):
            await log.send(text[i:i + 1900])
    await refresh_board()
    if DM_REPORTS:
        for uid in game.players:
            await dm(uid, game.report(uid))


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
    client.add_dynamic_items(TradeButton)
    if not getattr(client, "commands_synced", False):       # on_ready can fire again after a reconnect
        client.commands_synced = True
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            tree.clear_commands(guild=None)    # remove any older global copies so each command shows once
            await tree.sync()
        else:
            await tree.sync()
    if not ticker.is_running():
        ticker.start()
    print("Logged in as %s" % client.user)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env first (see .env.example)")
    client.run(TOKEN)
