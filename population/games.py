"""One game per Discord server, each in its own file: games/<server id>.db. No Discord code here.
`game` and `store` in discord_bot.py are ThisServer stand-ins: they look up the game of the server the current
command came from, so the commands themselves don't need to know about servers."""
import contextvars
import os
import re
from zoneinfo import ZoneInfo

from .game import Game, GameError
from .store import Store

current_server = contextvars.ContextVar("current_server", default=None)     # the server (guild) id of this command


class NoGame(GameError):
    pass


class Games:
    def __init__(self, folder, max_games=20):
        self.folder, self.max_games = folder, max_games
        os.makedirs(folder, exist_ok=True)
        self.loaded = {}

    def path(self, server_id):
        return os.path.join(self.folder, "%d.db" % server_id)

    def ids(self):
        return sorted(int(f[:-3]) for f in os.listdir(self.folder) if f.endswith(".db") and f[:-3].isdigit())

    def get(self, server_id):
        "The server's game, or None if it has none."
        if server_id is None or not str(server_id).isdigit():
            return None
        server_id = int(server_id)
        if server_id not in self.loaded:
            if not os.path.exists(self.path(server_id)):
                return None
            self.loaded[server_id] = Game(Store(self.path(server_id)))
        return self.loaded[server_id]

    def create(self, server_id):
        "A server's first game. Does nothing if it already has one; refuses past the cap."
        if self.get(server_id):
            return self.get(server_id)
        if len(self.ids()) >= self.max_games:
            raise GameError("this bot is already running its limit of %d games. The owner can raise MAX_GAMES" % self.max_games)
        self.loaded[int(server_id)] = Game(Store(self.path(int(server_id))))
        return self.loaded[int(server_id)]

    def all(self):
        return [(i, self.get(i)) for i in self.ids()]


class ThisServer:
    "Acts like the Game (part='game') or its Store (part='store') of the server the current command came from."
    def __init__(self, games, part):
        self._games, self._part = games, part

    def __getattr__(self, name):
        server_id = current_server.get()
        found = self._games.get(server_id)
        if found is None:
            raise NoGame("use this in a server where an admin has started a game" if server_id is None else
                         "there is no game in this server yet. An admin starts one with /admin open")
        return getattr(found.store if self._part == "store" else found, name)


def remember_server(state):
    """Make every interaction (command, button, pop-up) run with its server id in current_server. discord.py starts
    the code for an interaction from inside this event, so the value follows into it."""
    original = state.parsers["INTERACTION_CREATE"]

    def parse(data):
        current_server.set(int(data["guild_id"]) if data.get("guild_id") else None)
        original(data)
    state.parsers["INTERACTION_CREATE"] = parse


# ---------- per-server settings: the .env values are the defaults, /admin settings overrides them ----------

SETTINGS = {
    "tick_time": "time of the nightly tick, like 02:00",
    "tick_tz": "timezone for the tick time, like America/Denver",
    "tick_minutes": "tick every N minutes instead of nightly (blank for nightly)",
    "admin_role": "name of the admin role",
    "dm_reports": "send night reports as DMs (1 or 0)",
}


def settings(store, defaults):
    return dict(defaults, **store.get("settings", {}))


def check_setting(name, text):
    "The cleaned-up value, or a GameError saying what is allowed."
    text = text.strip()
    if name == "tick_time" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text):
        raise GameError("tick_time looks like 02:00 (24-hour)")
    if name == "tick_tz":
        try:
            ZoneInfo(text)
        except Exception:
            raise GameError("'%s' is not a timezone name (try America/Denver)" % text)
    if name == "tick_minutes" and text and not (text.isdigit() and 1 <= int(text) <= 1440):
        raise GameError("tick_minutes is a number from 1 to 1440, or blank")
    if name == "admin_role" and not 1 <= len(text) <= 50:
        raise GameError("admin_role needs a name (up to 50 characters)")
    if name == "dm_reports":
        if text.lower() not in ("1", "0", "on", "off", "yes", "no"):
            raise GameError("dm_reports is 1 or 0")
        text = "1" if text.lower() in ("1", "on", "yes") else "0"
    return text
