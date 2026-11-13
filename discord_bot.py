"""The Discord bot. Run with:  python3 discord_bot.py   (reads settings from .env)
All game rules live in the population/ package; this file only talks to Discord."""
import asyncio
import io
import logging
import os
import time
import traceback
import sys

sys.stdout.reconfigure(line_buffering=True)    # show print() lines in the service log right away

import discord
from discord import app_commands
from discord.ext import tasks

from population import guide, infopage, mapnav, notify, render, rulepanel, ruleset, schedule
from population.game import Game, GameError
from population.games import Games, NoGame, SETTINGS, ThisServer, check_setting, current_server, remember_server, settings
from population import stats
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
ACTIVITY_PORT = os.environ.get("ACTIVITY_PORT", "").strip()           # set to turn the Discord Activity web server on
ACTIVITY_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DM_REPORTS = os.environ.get("DM_REPORTS", "1").strip().lower() not in ("0", "false", "no", "off")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTING_DEFAULTS = {"tick_time": TICK_TIME, "tick_tz": TICK_TZ, "tick_minutes": TICK_MINUTES, "admin_role": ADMIN_ROLE,
                    "dm_reports": "1" if DM_REPORTS else "0", "advanced_mode": "1"}      # each server can override these with /admin settings
games = Games(os.environ.get("GAMES_DIR", "games"), int(os.environ.get("MAX_GAMES", "20") or 20))   # one game per server
game = ThisServer(games, "game")        # the game of the server the current command came from
store = ThisServer(games, "store")
client = discord.Client(intents=discord.Intents.default())   # no privileged intents needed
remember_server(client._connection)     # so game and store know which server a command came from
tree = app_commands.CommandTree(client)
admin = app_commands.Group(name="admin", description="Admin commands")


# ---------- small helpers ----------

tutorials = {}      # discord user id -> Tutorial. Each is kept in a small file in PRACTICE_DIR so a restart doesn't end it.
PRACTICE_DIR = os.environ.get("PRACTICE_DIR", "practice")


def practice_path(user_id):
    return os.path.join(PRACTICE_DIR, "%d.db" % user_id)


def load_practice_games(max_days=14):
    """After a restart: bring back practice games, and forget ones nobody has touched for two weeks."""
    if not os.path.isdir(PRACTICE_DIR):
        return
    for name in sorted(os.listdir(PRACTICE_DIR)):
        stem, path = name[:-3], os.path.join(PRACTICE_DIR, name)
        if not (name.endswith(".db") and stem.isdigit()):
            continue
        if time.time() - os.path.getmtime(path) > max_days * 86400:
            os.remove(path)
            continue
        try:
            tutorials[int(stem)] = Tutorial(int(stem), path=path)
        except Exception:
            traceback.print_exc()           # a broken file just means that practice game is gone


def record_error(where, interaction, error):
    """Keep the last 20 crashes for /admin errorlog, and write the full traceback to the log."""
    cause = getattr(error, "original", error)
    if isinstance(cause, GameError):            # e.g. no game in this server: a message, not a crash
        return cause
    frames = traceback.extract_tb(cause.__traceback__)
    rows = store.get("error_log", [])
    rows.append({"at": int(time.time()), "where": where, "user": interaction.user.id,
                 "error": "%s: %s" % (type(cause).__name__, str(cause)[:200]),
                 "place": "%s:%d" % (os.path.basename(frames[-1].filename), frames[-1].lineno) if frames else "?"})
    store.set("error_log", rows[-20:])
    logging.getLogger("feudalism").error("%s failed", where, exc_info=error)
    return cause


async def tell_failure(interaction, error, cause):
    text = "Something went wrong on my side. The admin can see the details with /admin errorlog."
    if isinstance(cause, GameError):
        text = "Can't do that: %s" % cause
    try:
        if is_admin(interaction):                      # admins also get the actual error right here
            text += "\n`%s: %s`" % (type(cause).__name__, str(cause)[:300])
    except Exception:
        pass
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


@tree.error
async def command_failed(interaction, error):
    if isinstance(error, app_commands.TransformerError):       # a channel the bot can't see, or the wrong kind
        return await say(interaction, "I can't use that channel: either I can't see it, or it isn't a text channel. "
                                      "Give my role View Channel and Send Messages there, then try again.")
    where = "/" + getattr(interaction.command, "qualified_name", "?")
    await tell_failure(interaction, error, record_error(where, interaction, error))


async def view_failed(self, interaction, error, item):
    await tell_failure(interaction, error, record_error("a button or menu", interaction, error))


async def modal_failed(self, interaction, error):
    await tell_failure(interaction, error, record_error("a pop-up", interaction, error))


discord.ui.View.on_error = view_failed          # crashes in buttons and menus are recorded too
discord.ui.Modal.on_error = modal_failed


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


def admin_role():
    "This server's admin role name (the .env value unless /admin settings changed it)."
    found = games.get(current_server.get())
    return settings(found.store, SETTING_DEFAULTS)["admin_role"] if found else ADMIN_ROLE


def is_admin(interaction):
    """Has the admin role (name checked ignoring case and spaces), or Discord's Administrator permission."""
    roles = getattr(interaction.user, "roles", [])
    if any(r.name.strip().lower() == admin_role().strip().lower() for r in roles):
        return True
    return interaction.permissions.administrator


def deny(interaction):
    seen = ", ".join(r.name for r in getattr(interaction.user, "roles", []) if r.name != "@everyone") or "none"
    return "You need the '%s' role for this. (Roles I can see on you: %s)" % (admin_role(), seen)


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


@tree.command(description="Open the clickable map (Discord Activity)")
async def play(interaction: discord.Interaction):
    if not ACTIVITY_PORT:
        return await say(interaction, "Can't do that: the clickable map is not turned on for this bot")
    try:
        await interaction.response.launch_activity()
    except discord.HTTPException as e:
        await say(interaction, "Can't do that: Discord would not start the Activity (%s)" % e.text)


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
        if self.tab == "overview":
            for n in (1, 5, 10):
                b = discord.ui.Button(label="Train %d" % n, row=1)
                b.callback = self.train(n)
                self.add_item(b)
            return
        if self.tab != "economy":
            return
        nation = self.g.world["nations"][self.g.nid(self.uid)]
        prios = nation["priorities"]
        advanced = nation.get("mode", "basic") == "advanced"
        can = infopage.available(self.g.world, self.g.rules, self.g.nid(self.uid))
        if self.resource not in can:
            self.resource = can[0]
        menu = discord.ui.Select(placeholder="Pick a resource...", row=1, options=[
            discord.SelectOption(label=r.capitalize(), value=r, description="priority %d of 5" % prios[r],
                                 default=r == self.resource) for r in can])
        menu.callback = self.pick(menu)
        self.add_item(menu)
        if advanced:
            fixed = nation.get("assign", {}).get(self.resource)
            placed = tick.work_plan(self.g.world, self.g.rules, nation).get(self.resource, (0, 0))[0]
            shown = placed if fixed is None else fixed
            text = "%s: %s" % (self.resource, "auto (%d)" % placed if fixed is None else "%d set" % fixed)
            minus = discord.ui.Button(label="-", row=2, disabled=shown <= 0)
            minus.callback = self.count(shown - 1)
            label = discord.ui.Button(label=text, row=2, disabled=True)
            plus = discord.ui.Button(label="+", row=2)
            plus.callback = self.count(shown + 1)
            auto = discord.ui.Button(label="Auto", row=2, disabled=fixed is None)
            auto.callback = self.count(None)
            for item in (minus, label, plus, auto):
                self.add_item(item)
        else:
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
        mode = discord.ui.Button(label="Switch to %s mode" % ("basic" if advanced else "advanced"), row=3)
        mode.callback = self.toggle_mode(advanced)
        self.add_item(mode)

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

    def train(self, n):
        async def cb(interaction):
            try:
                text = self.g.train(self.uid, n)
            except (GameError, ValueError) as e:
                return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
            await interaction.response.edit_message(embed=self.embed(), view=self)
            await interaction.followup.send(text, ephemeral=True)
        return cb

    def count(self, value):
        async def cb(interaction):
            try:
                self.g.set_assign(self.uid, self.resource, value)
            except (GameError, ValueError) as e:
                return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
            await self.refresh(interaction)
        return cb

    def toggle_mode(self, advanced):
        async def cb(interaction):
            try:
                self.g.set_mode(self.uid, "basic" if advanced else "advanced")
            except (GameError, ValueError) as e:
                return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
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
    """Seven short help pages, one button each."""

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
          ("buildings", "Buildings", "Show homes, barracks, markets, walls and workshops"),
          ("people", "Housing numbers", "Label buildings with how many people they house"),
          ("production", "Production", "Show what each of your tiles makes tonight"),
          ("coords", "Coordinates", "Label every tile with its number"))
LETTER = {"food": "F", "livestock": "L", "wood": "W", "stone": "S", "ore": "O", "oil": "Oi", "tools": "T", "steel": "St", "provisions": "P", "bricks": "B"}


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
        up = card.get("upgrade")
        if up:
            if up["queued"]:
                self.button("Upgrade (already queued)", 4, self.upgrade_here, disabled=True)
            elif up["missing"]:
                self.button("Upgrade to L%d: short %s" % (up["level"], money(up["missing"]))[:80], 4, self.upgrade_here, disabled=True)
            else:
                self.button("Upgrade to L%d (%s)" % (up["level"], money(up["cost"]))[:80], 4, self.upgrade_here, discord.ButtonStyle.primary)
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

    async def upgrade_here(self, interaction):
        await self.order(interaction, self.g.upgrade, self.selected)

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
@app_commands.describe(tile="Your tile, like 3,-2", building="home, barracks, market, wall, workshop, or port")
@app_commands.choices(building=[app_commands.Choice(name=b, value=b) for b in ("home", "barracks", "market", "wall", "toolworks", "forge", "cookhouse", "kiln", "port")])
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


@tree.command(description="Upgrade a home or barracks so it holds more people")
@app_commands.describe(tile="Your tile with a home or barracks, like 3,-2")
async def upgrade(interaction: discord.Interaction, tile: str):
    await run(interaction, game_for(interaction.user.id).upgrade, interaction.user.id, tile.strip())


@upgrade.autocomplete("tile")
async def upgrade_tiles(interaction: discord.Interaction, current: str):
    nid = my_nation(interaction)
    if nid is None:
        return []
    g = game_for(interaction.user.id)
    ok = [k for k in W.owned(g.world, nid) if tick.upgrade_of(g.rules, g.world["tiles"][k])]
    return tile_choices(ok, current)


@tree.command(description="Basic: priorities decide the work. Advanced: exact worker counts and split armies.")
@app_commands.choices(mode=[app_commands.Choice(name="basic", value="basic"), app_commands.Choice(name="advanced", value="advanced")])
async def mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    await run(interaction, game_for(interaction.user.id).set_mode, interaction.user.id, mode.value)


@tree.command(description="Advanced mode: exact workers per resource, like 'food 8, wood 4' ('auto' clears).")
@app_commands.describe(counts="Pairs like 'food 8, wood 4', or 'auto' to clear them all")
async def assign(interaction: discord.Interaction, counts: str):
    await run(interaction, game_for(interaction.user.id).assign, interaction.user.id, counts)


@tree.command(description="Turn workers into soldiers (needs barracks room, ore and food)")
async def train(interaction: discord.Interaction, count: app_commands.Range[int, 1, 500]):
    await run(interaction, game_for(interaction.user.id).train, interaction.user.id, count)


@tree.command(description="March an army toward a tile (attacks if it is enemy land)")
@app_commands.describe(army="Army number from /status", tile="Destination like 3,-2",
                       count="Advanced mode: send only this many soldiers from the army")
async def move(interaction: discord.Interaction, army: int, tile: str, count: app_commands.Range[int, 1, 5000] = None):
    await run(interaction, game_for(interaction.user.id).move, interaction.user.id, army, tile.strip(), count)


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
        if uid is None or not game.wants_dm(uid):
            return False
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


async def edit_card(o):
    """Show an offer's final state on its board card (or DM) and take the buttons off."""
    o["shown"] = True
    try:
        channel = client.get_channel(o["post"][0]) or await client.fetch_channel(o["post"][0])
        msg = await channel.fetch_message(o["post"][1])
        await msg.edit(embed=offer_embed(game, o), view=None)
    except discord.HTTPException:
        pass


async def refresh_board():
    """After a night: edit board cards whose offers expired, so nobody clicks a dead button."""
    for o in game.world["trades"]:
        if o.get("post") and o["status"] != "open" and not o.get("shown"):
            await edit_card(o)
    game.save()


async def activity_offer(server_id, tid):
    """An offer made in the Activity: post it the same way /trade does."""
    current_server.set(int(server_id))
    return await post_offer(tid)


async def activity_resolved(server_id, tid):
    """An offer accepted or declined in the Activity: update its board card."""
    current_server.set(int(server_id))
    o = trade_mod.find(game.world, tid)
    if o and o.get("post") and not o.get("shown"):
        await edit_card(o)
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
        text += " They haven't turned on DMs (or I couldn't reach them), so they will see it in /status and /offers."
    await say(interaction, text)


@tree.command(description="Turn private messages (night reports, private trade offers) on or off. They are off until you ask.")
@app_commands.choices(setting=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def dms(interaction: discord.Interaction, setting: app_commands.Choice[str]):
    g = game_for(interaction.user.id)
    await say(interaction, g.set_dm(interaction.user.id, setting.value == "on"))


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
    os.makedirs(PRACTICE_DIR, exist_ok=True)
    path = practice_path(interaction.user.id)
    if os.path.exists(path):
        os.remove(path)                     # starting again means a fresh game
    t = Tutorial(interaction.user.id, mode if mode in Game.modes() else "feudalism", path=path)
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
    t = tutorials.pop(interaction.user.id, None)
    if t is None:
        return await say(interaction, "You have no practice game.")
    t.game.store.db.close()
    if os.path.exists(practice_path(interaction.user.id)):
        os.remove(practice_path(interaction.user.id))
    await say(interaction, "Practice game closed. Your commands now work on the real game again.")


tree.add_command(practice)
load_practice_games()


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
    if is_admin(interaction):
        try:
            if interaction.guild_id is None:
                raise GameError("use this in a server")
            games.create(interaction.guild_id)        # a server's first game is made here
            store.set("server_name", interaction.guild.name)
        except GameError as e:
            return await say(interaction, "Can't do that: %s" % e)
    await admin_run(interaction, game.open_signup, mode.value, public=True)


@admin.command(name="close", description="Close sign-ups without starting (names so far are kept)")
async def admin_close(interaction: discord.Interaction):
    await admin_run(interaction, game.close_signups, public=True)


@admin.command(name="reopen", description="Open sign-ups again after closing them (names so far are kept)")
async def admin_reopen(interaction: discord.Interaction):
    await admin_run(interaction, game.reopen_signups, public=True)


@admin.command(name="clear", description="Wipe the sign-up list (sign-ups stay closed until /admin reopen)")
async def admin_clear(interaction: discord.Interaction):
    await admin_run(interaction, game.clear_signups, public=True)


@admin.command(name="start", description="Start the game with everyone who joined. Reports post in this channel.")
@app_commands.describe(radius="Map size. Leave blank to size it for the players (16 players: radius 11)",
                       expected_players="How many players you expect, if more than have joined so far (sizes the map)")
async def admin_start(interaction: discord.Interaction, radius: app_commands.Range[int, 3, 20] = None,
                      expected_players: app_commands.Range[int, 2, 60] = None):
    if is_admin(interaction):
        store.set("report_channel", interaction.channel_id)
        store.set("last_tick", time.time())
    await admin_run(interaction, game.start, radius, None, expected_players, public=True)
    if is_admin(interaction) and game.phase == "running":
        for uid, text in game.start_messages().items():
            if game.wants_dm(uid):
                await dm(uid, text)


@admin.command(name="announcements", description="Choose the channel for announcements and nightly reports")
async def admin_announcements(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    store.set("announce_channel", channel.id)
    await say(interaction, "Announcements and nightly reports will go to #%s." % channel.name)


class ClearView(discord.ui.View):
    """Asks once before wiping a channel."""

    def __init__(self, uid, channel):
        super().__init__(timeout=120)
        self.uid, self.channel = uid, channel

    async def interaction_check(self, interaction):
        return interaction.user.id == self.uid

    @discord.ui.button(label="Yes, delete everything that isn't pinned", style=discord.ButtonStyle.danger)
    async def yes(self, interaction, button):
        await interaction.response.edit_message(content="Clearing #%s ..." % self.channel.name, view=None)
        try:
            gone = await self.channel.purge(limit=None, check=lambda m: not m.pinned)
            text = "Cleared #%s: %d message%s deleted. Pinned messages were kept." % (self.channel.name, len(gone), "" if len(gone) == 1 else "s")
        except discord.Forbidden:
            text = "I can't do that in #%s. Give me Manage Messages and Read Message History there." % self.channel.name
        await interaction.edit_original_response(content=text)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        await interaction.response.edit_message(content="Nothing was deleted.", view=None)
        self.stop()


@admin.command(name="wipe", description="Delete all messages in a channel (for a fresh start), keeping pinned ones")
@app_commands.describe(channel="The channel to clear, for example the announcements, log or trade board channel")
async def admin_wipe(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    await interaction.response.send_message(
        "This deletes every message in #%s (pinned ones stay). It can't be undone. Messages older than two weeks are "
        "removed one at a time, so a long history takes a while." % channel.name,
        view=ClearView(interaction.user.id, channel), ephemeral=True)


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


class NumberModal(discord.ui.Modal):
    """A small pop-up to type an exact number for a rule."""

    def __init__(self, view, key, label, lo, hi):
        super().__init__(title=label[:45])
        self.view, self.key, self.lo, self.hi = view, key, lo, hi
        self.number = discord.ui.TextInput(label="New value (%d to %d)" % (lo, hi), max_length=6)
        self.add_item(self.number)

    async def on_submit(self, interaction):
        try:
            self.view.g.queue_rule(self.key, int(self.number.value.strip()))
        except ValueError:
            return await interaction.response.send_message("Type a whole number, like %d." % self.lo, ephemeral=True)
        except GameError as e:
            return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
        await self.view.refresh(interaction)


class RulesView(discord.ui.View):
    """The admin's rules panel: a tab for each kind of rule, pick a rule, then flip it or step it.
    Changes wait for the next tick."""

    def __init__(self, uid, g):
        super().__init__(timeout=900)
        self.uid, self.g = uid, g
        g.need_rules()
        self.tab = rulepanel.TABS[0][0]
        self.advanced = False                # advanced rules are hidden until the admin asks
        self.sure = False                    # the "reset everyone's stock" button has been pressed once
        self.pick_first()
        self.layout()

    async def interaction_check(self, interaction):
        return interaction.user.id == self.uid

    def pick_first(self):
        self.key = next(iter(self.rows()))

    def rows(self):
        return {r["key"]: r for r in self.g.rule_rows() if r["tab"] == self.tab and (self.advanced or not r["advanced"])}

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
        if self.tab == "start":
            lines.append("These only change nations created afterwards. Nobody's current stock changes unless you press the reset button.")
        return "\n".join(lines)

    def layout(self):
        self.clear_items()
        every = self.g.rule_rows()
        for name, title in rulepanel.TABS:
            waiting = sum(1 for r in every if r["tab"] == name and r["next"] is not None)
            b = discord.ui.Button(label=title + (" (%d)" % waiting if waiting else ""), row=0,
                                  style=discord.ButtonStyle.primary if name == self.tab else discord.ButtonStyle.secondary)
            b.callback = self.go_tab(name)
            self.add_item(b)
        rows = self.rows()
        menu = discord.ui.Select(placeholder="Pick a rule...", row=1, options=[
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
            self.add_item(self.button("Type a number", self.type_number(r["label"], lo, hi)))
        self.add_item(self.button("Undo change", self.set_to(r["now"]), disabled=r["next"] is None))
        toggle = discord.ui.Button(row=4, label="Hide advanced rules" if self.advanced else "Show advanced rules")
        toggle.callback = self.toggle_advanced
        self.add_item(toggle)
        if self.tab == "start" and self.g.phase == "running":
            keep = discord.ui.Button(row=3, label="Save, keep everyone's current stock", style=discord.ButtonStyle.success)
            keep.callback = self.save_only
            self.add_item(keep)
            b = discord.ui.Button(row=3, label="Really reset? This wipes everyone's stockpile" if self.sure
                                  else "Save and set everyone's stock to the start amounts",
                                  style=discord.ButtonStyle.danger if self.sure else discord.ButtonStyle.secondary)
            b.callback = self.reset_stock
            self.add_item(b)

    def button(self, label, callback, disabled=False):
        b = discord.ui.Button(label=label, row=2, disabled=disabled)
        if callback:
            b.callback = callback
        return b

    async def refresh(self, interaction):
        self.sure = False
        self.layout()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def toggle_advanced(self, interaction):
        self.advanced = not self.advanced
        self.pick_first()
        await self.refresh(interaction)

    def go_tab(self, name):
        async def cb(interaction):
            self.tab = name
            self.pick_first()
            await self.refresh(interaction)
        return cb

    def pick(self, menu):
        async def cb(interaction):
            self.key = menu.values[0]
            await self.refresh(interaction)
        return cb

    def type_number(self, label, lo, hi):
        async def cb(interaction):
            await interaction.response.send_modal(NumberModal(self, self.key, label, lo, hi))
        return cb

    def set_to(self, value):
        async def cb(interaction):
            try:
                self.g.queue_rule(self.key, value)
            except GameError as e:
                return await interaction.response.send_message("Can't do that: %s" % e, ephemeral=True)
            await self.refresh(interaction)
        return cb

    async def save_only(self, interaction):
        """Changes are already saved as you make them; this says so and leaves every stockpile alone."""
        waiting = [r for r in self.g.rule_rows() if r["tab"] == "start" and r["next"] is not None]
        self.sure = False
        self.layout()
        text = ("Saved: %d start rule%s will apply at the next tick. Nobody's current stock was changed." % (
            len(waiting), "" if len(waiting) == 1 else "s")) if waiting else "Nothing to save: no start rule has changed. Nobody's stock was changed."
        await interaction.response.edit_message(content=self.text() + "\n\n" + text, view=self)

    async def reset_stock(self, interaction):
        if not self.sure:                    # the first press only asks; the second one does it
            self.sure = True
            self.layout()
            return await interaction.response.edit_message(content=self.text(), view=self)
        try:
            text = self.g.reset_stock()
        except GameError as e:
            text = "Can't do that: %s" % e
        self.sure = False
        self.layout()
        await interaction.response.edit_message(content=self.text() + "\n\n" + text, view=self)


@admin.command(name="rules", description="Change game rules (switches and key numbers) at the next tick")
async def admin_rules(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    try:
        view = RulesView(interaction.user.id, game)
    except GameError as e:
        return await say(interaction, "Can't do that: %s" % e)
    await interaction.response.send_message(view.text(), view=view, ephemeral=True)


@admin.command(name="resetrules", description="Put every rule back to the mode's defaults (next tick if the game is running)")
async def admin_resetrules(interaction: discord.Interaction):
    await admin_run(interaction, game.reset_rules, public=True)


@admin.command(name="errorlog", description="Show the most recent errors the bot ran into")
async def admin_errorlog(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    rows = store.get("error_log", [])
    if not rows:
        return await say(interaction, "No errors recorded.")
    lines = ["<t:%d:R> %s by <@%d>: `%s` (%s)" % (r["at"], r["where"], r["user"], r["error"], r["place"])
             for r in reversed(rows[-10:])]
    await say(interaction, ("**Latest errors** (newest first)\n" + "\n".join(lines))[:1990])


@admin.command(name="settings", description="Show or change this server's tick time, timezone, admin role and DM reports")
@app_commands.choices(name=[app_commands.Choice(name=k, value=k) for k in SETTINGS])
@app_commands.describe(name="Which setting", value="The new value (for tick_minutes, 'off' goes back to a nightly tick). Leave blank to just look.")
async def admin_settings(interaction: discord.Interaction, name: app_commands.Choice[str] = None, value: str = None):
    if not is_admin(interaction):
        return await say(interaction, deny(interaction))
    if name and value is not None:
        try:
            clean = check_setting(name.value, "" if name.value == "tick_minutes" and value.lower() in ("off", "none", "nightly") else value)
        except GameError as e:
            return await say(interaction, "Can't do that: %s" % e)
        store.set("settings", dict(store.get("settings", {}), **{name.value: clean}))
    now = settings(store, SETTING_DEFAULTS)
    await say(interaction, "**Settings for this server**\n" + "\n".join(
        "`%s` = `%s` (%s)" % (k, now[k] or "off", what) for k, what in SETTINGS.items()))


def bot_owner_ids(app):
    return {m.id for m in app.team.members} if app.team else {app.owner.id}


@admin.command(name="botstats", description="Bot owner only: games, servers and how busy the box is")
async def admin_botstats(interaction: discord.Interaction):
    if interaction.user.id not in bot_owner_ids(await client.application_info()):
        return await say(interaction, "Only the bot's owner can see this.")
    stats.write_bot_file(games.folder, len(client.guilds))
    await say(interaction, stats.text(stats.gather(games))[:1990])


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
    started = time.time()
    log = log_channel()
    report = game.run_tick(split_log=log is not None)
    store.set("last_tick", time.time())
    guild = client.get_guild(current_server.get())
    if guild:
        store.set("server_name", guild.name)
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
    if setting("dm_reports") == "1":
        prefix = "**%s**\n" % guild.name if guild else ""        # a player may be in several servers' games
        for uid in game.players:
            if game.wants_dm(uid):
                await dm(uid, prefix + game.report(uid))
    store.set("last_tick_seconds", int(time.time() - started))


def setting(name):
    return settings(store, SETTING_DEFAULTS)[name]


def tick_is_due():
    hours = (game.rules or {}).get("tick_hours", 24)
    return schedule.is_due(time.time(), store.get("last_tick", 0), hours, setting("tick_time"), setting("tick_tz"),
                           setting("tick_minutes"))


async def tell_phone(title, message, priority="default"):
    await asyncio.to_thread(notify.send, APP_DIR, title, message, priority)


@tasks.loop(seconds=30)
async def ticker():
    for server_id, g in games.all():            # one server's trouble never stops another's night
        current_server.set(server_id)
        if g.phase != "running" or g.store.get("paused", False) or not tick_is_due():
            continue
        try:
            await do_tick()
            store.set("tick_failure_sent", False)
        except Exception as e:                  # keep the loop alive; try again in 30 seconds
            logging.getLogger("feudalism").exception("tick failed in server %s", server_id)
            if not store.get("tick_failure_sent", False):      # one message per failure streak, not one per retry
                store.set("tick_failure_sent", True)
                await tell_phone("The tick failed in %s" % store.get("server_name", server_id),
                                 "%s: %s" % (type(e).__name__, str(e)[:200]), "high")
    if time.time() - getattr(client, "stats_written", 0) > 300:       # for `feudalism games`
        client.stats_written = time.time()
        stats.write_bot_file(games.folder, len(client.guilds))


def keep_launch_command():
    """Discord refuses a global command sync that leaves out the Activity's Launch command,
    so add it back into every one."""
    original = client.http.bulk_upsert_global_commands

    async def with_launch(application_id, payload):
        payload = list(payload)
        if not any(c.get("type") == 4 for c in payload):
            for c in await client.http.get_global_commands(application_id):
                if c.get("type") == 4:
                    payload.append({k: c[k] for k in ("id", "name", "description", "type", "handler") if k in c})
        return await original(application_id, payload=payload)

    client.http.bulk_upsert_global_commands = with_launch


@client.event
async def on_interaction(interaction: discord.Interaction):
    """Remember when someone last used the bot, so automatic updates wait for a quiet moment."""
    if games.get(current_server.get()):
        store.set("last_activity", time.time())


@client.event
async def on_ready():
    client.add_dynamic_items(TradeButton)
    if not getattr(client, "commands_synced", False):       # on_ready can fire again after a reconnect
        client.commands_synced = True
        if ACTIVITY_PORT:
            keep_launch_command()
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
    if ACTIVITY_PORT and not getattr(client, "activity_runner", None):
        if not ACTIVITY_SECRET:
            print("ACTIVITY_PORT is set but DISCORD_CLIENT_SECRET is missing, so the Activity server is off.")
        else:
            from activity import server as activity_server
            client.activity_runner = await activity_server.start(int(ACTIVITY_PORT), str(client.application_id),
                                                                 ACTIVITY_SECRET, games.get,
                                                                 {"offer": activity_offer, "resolved": activity_resolved})
            print("Activity server listening on port %s" % ACTIVITY_PORT)
    print("Logged in as %s" % client.user)
    if not getattr(client, "start_told", False):
        client.start_told = True
        await tell_phone("Bot is online", "Logged in as %s" % client.user)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env first (see .env.example)")
    client.run(TOKEN)
