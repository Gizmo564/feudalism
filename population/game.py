"""Everything the Discord commands do, with no Discord code in it. Each method returns plain text.
Errors that a player should see are raised as GameError."""
import json
import os
import random

from . import world as W, tick, trade, victory, views, ruleset
from .hexes import neighbors

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules")


class GameError(Exception):
    pass


def parse_pairs(text):
    """'food 5, wood 3' -> {'food': 5, 'wood': 3}"""
    out = {}
    words = text.replace(",", " ").split()
    if len(words) % 2:
        raise GameError("Use pairs like: wood 10, stone 5")
    for i in range(0, len(words), 2):
        name, num = words[i].lower(), words[i + 1]
        if not num.isdigit():
            raise GameError("'%s' is not a whole number" % num)
        out[name] = int(num)
    return out


class Game:
    def __init__(self, store):
        self.store = store
        self.phase = store.get("phase", "none")          # none -> signup -> running -> over
        self.signups = store.get("signups", [])          # [{"uid", "name"}]
        self.players = store.get("players", {})          # discord user id -> nation id
        self.world = store.get("world")
        self.rules = store.get("rules")

    def save(self):
        for k in ("phase", "signups", "players", "world", "rules"):
            self.store.set(k, getattr(self, k))

    # ---------- setup (admin) ----------

    def open_signup(self, mode):
        path = os.path.join(RULES_DIR, mode + ".json")
        if not os.path.exists(path):
            raise GameError("Unknown mode '%s'. Modes: %s" % (mode, ", ".join(self.modes())))
        if self.phase == "running":
            raise GameError("A game is already running")
        self.rules = ruleset.load(path)
        self.phase, self.signups, self.players, self.world = "signup", [], {}, None
        self.save()
        return "Sign-ups are open for **%s**. Players use /join." % self.rules["name"]

    @staticmethod
    def modes():
        return sorted(f[:-5] for f in os.listdir(RULES_DIR) if f.endswith(".json"))

    def start(self, radius, seed=None):
        if self.phase != "signup":
            raise GameError("Open sign-ups first")
        if not self.signups:
            raise GameError("Nobody has joined yet")
        seed = random.randrange(1, 10 ** 6) if seed is None else seed
        self.world = W.new_world(self.rules, [s["name"] for s in self.signups], radius, seed)
        self.players = {s["uid"]: i for i, s in enumerate(self.signups)}
        self.phase = "running"
        self.store.snapshot(0, W.to_json(self.world))
        self.save()
        return "**%s** has begun with %d nations on a radius-%d map." % (self.rules["name"], len(self.signups), radius)

    def add_player(self, uid, name):
        """Admin: add someone after the game started (late join)."""
        self.need_running()
        self.check_name(name)
        nid = W.add_nation(self.world, self.rules, name)
        self.players[str(uid)] = nid
        self.save()
        return "%s joined as %s." % (name, name)

    def set_fog(self, on):
        self.need_running()
        self.world["settings"]["fog"] = bool(on)
        self.save()
        return "Fog of war is now %s." % ("on" if on else "off")

    def set_hidden_oil(self, on):
        self.need_running()
        self.world["settings"]["hidden_oil"] = bool(on)
        W.discover_oil(self.world)
        self.save()
        return "Oil deposits are now %s." % ("hidden until the land is claimed" if on else "visible to everyone")

    def expand(self, rings):
        self.need_running()
        W.expand_map(self.world, self.rules, rings)
        self.save()
        return "Map expanded to radius %d (%d tiles)." % (self.world["radius"], len(self.world["tiles"]))

    def set_empire(self, percent):
        """Change the land share needed to win (the starting value; it still decays late in the game)."""
        self.need_running()
        if "ranks" not in self.rules or self.rules["victory"].get("land_share_percent") is None:
            raise GameError("This mode has no land-share win condition")
        if not 5 <= percent <= 100:
            raise GameError("Pick a percent from 5 to 100")
        self.rules["victory"]["land_share_percent"] = percent
        self.save()
        return "An empire now needs %d%% of the land (it can fall later in the game)." % percent

    def end(self):
        self.need_running()
        rows = victory.end_game(self.world, self.rules)
        self.phase = "over"
        self.save()
        return "Game ended by an admin. Winner: **%s**." % self.world["nations"][self.world["winner"]["nation"]]["name"]

    def rollback(self, to_tick):
        text = self.store.snapshot_get(to_tick)
        if text is None:
            raise GameError("No snapshot for tick %d. Available: %s" % (to_tick, self.store.snapshot_ticks()))
        self.world = W.from_json(text)
        self.phase = "running"
        self.save()
        return "Rolled back to tick %d." % to_tick

    # ---------- players ----------

    def check_name(self, name):
        if not 2 <= len(name) <= 20:
            raise GameError("Nation names are 2 to 20 characters")
        taken = [s["name"].lower() for s in self.signups]
        if self.world:
            taken += [n["name"].lower() for n in self.world["nations"]]
        if name.lower() in taken:
            raise GameError("That name is taken")

    def join(self, uid, name):
        uid = str(uid)
        if self.phase != "signup":
            raise GameError("Sign-ups are not open. Ask an admin to add you.")
        if any(s["uid"] == uid for s in self.signups):
            raise GameError("You have already joined")
        self.check_name(name)
        self.signups.append({"uid": uid, "name": name})
        self.save()
        return "Welcome, **%s**! (%d joined)" % (name, len(self.signups))

    def need_running(self):
        if self.phase != "running":
            raise GameError("There is no game running")

    def nid(self, uid):
        self.need_running()
        n = self.players.get(str(uid))
        if n is None:
            raise GameError("You are not in this game")
        return n

    def word(self, key):
        return self.rules.get("terms", {}).get(key, key)

    def claim(self, uid, tile):
        nid = self.nid(uid)
        tick.queue(self.world, self.rules, nid, {"type": "claim", "tile": tile})
        self.save()
        cost = tick.claim_cost(self.world, self.rules, nid)
        return "Claim of %s queued for tonight. Cost: %s." % (tile, self.fmt(cost))

    def build(self, uid, tile, building):
        nid = self.nid(uid)
        tick.queue(self.world, self.rules, nid, {"type": "build", "tile": tile, "building": building})
        self.save()
        return "Build %s on %s queued." % (building, tile)

    def train(self, uid, n):
        nid = self.nid(uid)
        tick.queue(self.world, self.rules, nid, {"type": "train", "n": n})
        self.save()
        return "Training %d %ss queued." % (n, self.word("soldier"))

    def move(self, uid, army, dest):
        nid = self.nid(uid)
        tick.queue(self.world, self.rules, nid, {"type": "move", "army": army, "dest": dest})
        self.save()
        return "Army %d will march toward %s." % (army, dest)

    def priorities(self, uid, text):
        nid = self.nid(uid)
        tick.set_priorities(self.world, self.rules, nid, parse_pairs(text))
        self.save()
        return "Priorities updated."

    def trade_offer(self, uid, to_name, give_text, get_text):
        nid = self.nid(uid)
        other = next((n for n in self.world["nations"] if n["name"].lower() == to_name.lower()), None)
        if other is None:
            raise GameError("No nation called '%s'" % to_name)
        give = {"resources": parse_pairs(give_text)} if give_text.strip() else {}
        get = {"resources": parse_pairs(get_text)} if get_text.strip() else {}
        try:
            tid = trade.offer(self.world, self.rules, nid, other["id"], give, get)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "Offer %d sent to %s." % (tid, other["name"])

    def trade_accept(self, uid, tid):
        nid = self.nid(uid)
        try:
            trade.accept(self.world, self.rules, tid, nid)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "Trade %d accepted." % tid

    def trade_decline(self, uid, tid):
        nid = self.nid(uid)
        try:
            trade.decline(self.world, tid, nid)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "Trade %d declined." % tid

    # ---------- reading ----------

    @staticmethod
    def fmt(res):
        return ", ".join("%d %s" % (v, k) for k, v in res.items() if v) or "nothing"

    def status(self, uid):
        nid = self.nid(uid)
        w, r = self.world, self.rules
        n = w["nations"][nid]
        hw, hs = W.housing(w, r, nid)
        title = views.rank_title(w, r, nid)
        lines = ["**%s**%s - tick %d" % (n["name"], " (%s)" % title if title else "", w["tick"])]
        if not n["alive"]:
            return lines[0] + "\nYour nation has fallen. You are now a spectator."
        lines.append("Land: %d tiles | %ss: %d/%d | %ss: %d/%d" % (
            len(W.owned(w, nid)), self.word("worker"), n["workers"], hw, self.word("soldier"), W.soldiers(w, nid), hs))
        lines.append("Stock: " + self.fmt(n["resources"]))
        lines.append("Priorities: " + ", ".join("%s %d" % (k, v) for k, v in n["priorities"].items()))
        lines.append("Next claim costs: " + self.fmt(tick.claim_cost(w, r, nid)))
        for a in w["armies"]:
            if a["owner"] == nid:
                lines.append("Army %d: %d at %s%s" % (a["id"], a["n"], a["tile"], " -> %s" % a["dest"] if a["dest"] else ""))
        for o in w["trades"]:
            if o["status"] == "open" and o["to"] == nid:
                lines.append("Offer %d from %s: gives %s, wants %s" % (
                    o["id"], w["nations"][o["from"]]["name"], self.fmt(o["give"].get("resources", {})),
                    self.fmt(o["get"].get("resources", {}))))
        queued = [o for o in w["orders"] if o["nation"] == nid]
        if queued:
            lines.append("Queued orders: %d" % len(queued))
        news = w.get("last_events", {}).get(str(nid), [])
        if news:
            lines.append("Last night:\n- " + "\n- ".join(news[:10]))
        return "\n".join(lines)

    def standings(self):
        self.need_running()
        rows = views.public_standings(self.world, self.rules, -1) if not self.world["settings"]["fog"] else []
        rows = sorted(rows, key=lambda x: (-x["land"], -x["population"]))
        if not rows:
            return "Standings are hidden while fog of war is on."
        return "\n".join("%d. %s%s - %d tiles, %d people%s" % (
            i + 1, r["name"], " (%s)" % r["rank"] if r.get("rank") else "", r["land"], r["population"],
            "" if r["alive"] else " (fallen)") for i, r in enumerate(rows[:15]))

    def titles(self):
        """{discord user id: title} for the bot to turn into roles. Empty if the mode has no titles."""
        if self.phase not in ("running", "over") or "ranks" not in self.rules:
            return {}
        return {uid: views.rank_title(self.world, self.rules, nid)
                for uid, nid in self.players.items() if self.world["nations"][nid]["alive"]}

    # ---------- the nightly tick ----------

    def run_tick(self):
        """Run one tick. Returns the public report text."""
        self.need_running()
        tick.run_tick(self.world, self.rules)
        events = self.world["events"]
        self.world["last_events"] = events
        self.world["events"] = {}
        self.store.snapshot(self.world["tick"], W.to_json(self.world))
        lines = ["**Tick %d**" % self.world["tick"]]
        if not self.world["settings"]["fog"]:
            lines += ["- " + e for e in events.get("public", [])[:15]]
        if self.world["winner"]:
            self.phase = "over"
            lines.append("**%s wins: %s**" % (self.world["nations"][self.world["winner"]["nation"]]["name"],
                                              self.world["winner"]["reason"]))
        self.save()
        return "\n".join(lines)
