"""Everything the Discord commands do, with no Discord code in it. Each method returns plain text.
Errors that a player should see are raised as GameError."""
import json
import os
import random

from . import advice, guide, infopage, pacts, world as W, tick, trade, victory, views, ruleset
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

    def queue(self, nid, order):
        try:
            tick.queue(self.world, self.rules, nid, order)
        except ValueError as e:
            raise GameError(str(e))
        self.save()

    def check_claim(self, nid, k):
        """Raise a GameError that says what is wrong and what to do instead."""
        w, r = self.world, self.rules
        t = w["tiles"].get(k)
        if t is None:
            raise GameError("%s isn't on the map. Open /zoom and pick a tile from the menu." % k)
        if t["terrain"] == "water":
            raise GameError("%s is water, and nobody can claim water." % k)
        if t["owner"] == nid:
            raise GameError("%s is already yours." % k)
        if t["owner"] is not None:
            raise GameError("%s belongs to %s. To take it, send an army there (/zoom, then Send army)." % (
                k, w["nations"][t["owner"]]["name"]))
        if k not in advice.free_frontier(w, nid):
            raise GameError("%s doesn't touch your land. You can only claim tiles next to yours: the outlined ones in /zoom." % k)
        if tick.foreign_army_on(w, k, nid):
            raise GameError("Foreign soldiers are standing on %s. Drive them off first." % k)
        if advice.claim_queued(w, nid) >= r["claim"]["per_tick"]:
            raise GameError("You already have a claim queued for tonight (the limit is %d a night). /cancel clears your orders." %
                            r["claim"]["per_tick"])
        cost = tick.claim_cost(w, r, nid)
        missing = advice.shortfall(advice.available(w, r, nid), cost)
        if missing:
            raise GameError("Not enough to claim %s: it costs %s and you are short %s. Orders are paid when the night starts, "
                            "so wait until you have enough." % (k, self.fmt(cost), self.fmt(missing)))

    def check_build(self, nid, k, building):
        w, r = self.world, self.rules
        if building not in r["buildings"]:
            raise GameError("Unknown building '%s'. Choose from: %s." % (building, ", ".join(r["buildings"])))
        t = w["tiles"].get(k)
        if t is None or t["owner"] != nid:
            raise GameError("%s isn't yours. Build on land you own (pick it in /zoom)." % k)
        if t["building"]:
            raise GameError("%s already has a %s." % (k, t["building"]))
        if tick.foreign_army_on(w, k, nid):
            raise GameError("Foreign soldiers are standing on %s. Drive them off first." % k)
        cost = r["buildings"][building]["cost"]
        missing = advice.shortfall(advice.available(w, r, nid), cost)
        if missing:
            raise GameError("Not enough for a %s: it costs %s and you are short %s." % (building, self.fmt(cost), self.fmt(missing)))

    def claim(self, uid, tile):
        nid = self.nid(uid)
        self.check_claim(nid, tile)
        self.queue(nid, {"type": "claim", "tile": tile})
        return "Claim of %s queued for tonight. Cost: %s." % (tile, self.fmt(tick.claim_cost(self.world, self.rules, nid)))

    def build(self, uid, tile, building):
        nid = self.nid(uid)
        self.check_build(nid, tile, building)
        self.queue(nid, {"type": "build", "tile": tile, "building": building})
        return "Build %s on %s queued. Cost: %s." % (building, tile, self.fmt(self.rules["buildings"][building]["cost"]))

    def train(self, uid, n):
        nid = self.nid(uid)
        w, r = self.world, self.rules
        me = w["nations"][nid]
        room = W.housing(w, r, nid)[1] - W.soldiers(w, nid)
        can = min(n, room, me["workers"] - 1)
        for res, v in r["soldier"]["train_cost"].items():
            if v > 0:
                can = min(can, me["resources"].get(res, 0) // v)
        if can <= 0:
            reasons = []
            if room <= 0:
                reasons.append("no room: build a barracks")
            if me["workers"] <= 1:
                reasons.append("not enough %ss" % self.word("worker"))
            short = advice.shortfall(me["resources"], r["soldier"]["train_cost"])
            if short:
                reasons.append("short of %s" % self.fmt(short))
            raise GameError("Can't train any %ss right now: %s." % (self.word("soldier"), "; ".join(reasons) or "nothing available"))
        self.queue(nid, {"type": "train", "n": n})
        note = "" if can >= n else " (you asked for %d, but only %d fit right now)" % (n, can)
        return "Training %d %ss queued%s." % (can, self.word("soldier"), note)

    def move(self, uid, army, dest):
        nid = self.nid(uid)
        w = self.world
        mine = [a for a in w["armies"] if a["owner"] == nid]
        if not any(a["id"] == army for a in mine):
            have = ", ".join("%d (at %s)" % (a["id"], a["tile"]) for a in mine) or "none"
            raise GameError("You have no army %d. Your armies: %s." % (army, have))
        t = w["tiles"].get(dest)
        if t is None:
            raise GameError("%s isn't on the map. Open /zoom and pick a tile from the menu." % dest)
        if t["terrain"] == "water":
            raise GameError("Armies can't cross water. Pick a land tile.")
        self.queue(nid, {"type": "move", "army": army, "dest": dest})
        return "Army %d will march toward %s (one tile a night)." % (army, dest)

    def cancel(self, uid):
        nid = self.nid(uid)
        mine = [o for o in self.world["orders"] if o["nation"] == nid]
        self.world["orders"] = [o for o in self.world["orders"] if o["nation"] != nid]
        self.save()
        return "Cleared %d queued order%s." % (len(mine), "" if len(mine) == 1 else "s") if mine else "You had no queued orders."

    def priorities(self, uid, text):
        nid = self.nid(uid)
        try:
            tick.set_priorities(self.world, self.rules, nid, parse_pairs(text))
        except ValueError as e:
            raise GameError("%s. Resources: %s. Priorities go from 0 to 5." % (e, ", ".join(self.rules["resources"])))
        self.save()
        return "Priorities updated."

    def nation_named(self, name):
        other = next((n for n in self.world["nations"] if n["name"].lower() == name.strip().lower()), None)
        if other is None:
            raise GameError("No nation called '%s'" % name)
        return other

    def trade_offer(self, uid, to_name, give_text, get_text):
        """to_name blank = an open offer for anyone with a Market. Returns the text for the player."""
        return self.make_offer(uid, to_name, give_text, get_text)[1]

    def make_offer(self, uid, to_name, give_text, get_text, private=False):
        """Three kinds: open to anyone (no name), public to one nation, or private to one nation (private=True)."""
        nid = self.nid(uid)
        other = self.nation_named(to_name) if (to_name or "").strip() else None
        if private and other is None:
            raise GameError("A private offer needs a nation. Name who it is for, or make it public.")
        try:
            give = {"resources": parse_pairs(give_text)} if give_text.strip() else {}
            get = {"resources": parse_pairs(get_text)} if get_text.strip() else {}
            tid = trade.offer(self.world, self.rules, nid, other["id"] if other else None, give, get)
        except ValueError as e:
            raise GameError(str(e))
        if private:
            trade.find(self.world, tid)["private"] = True
        self.save()
        return tid, ("Private offer %d sent to %s." % (tid, other["name"]) if private else
                     "Offer %d sent to %s." % (tid, other["name"])) if other else "Offer %d posted for anyone with a Market." % tid

    def offer_line(self, o):
        """One public sentence describing an offer."""
        w = self.world
        who = w["nations"][o["from"]]["name"]
        to = " to %s" % w["nations"][o["to"]]["name"] if o["to"] is not None else " to anyone with a Market"
        if o.get("private"):
            to += " (private)"
        return "**Offer %d** - %s gives %s and wants %s%s." % (
            o["id"], who, self.fmt(o["give"].get("resources", {})), self.fmt(o["get"].get("resources", {})), to)

    # ---------- pacts (records only) ----------

    def pact_propose(self, uid, to_name, text):
        nid, other = self.nid(uid), self.nation_named(to_name)
        try:
            pid = pacts.propose(self.world, nid, other["id"], text)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "Pact %d proposed to %s. It only counts if they accept; nothing enforces it." % (pid, other["name"])

    def pact_accept(self, uid, pid):
        nid = self.nid(uid)
        try:
            pacts.accept(self.world, pid, nid)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "Pact %d is now on the public record." % pid

    def pact_decline(self, uid, pid):
        nid = self.nid(uid)
        try:
            pacts.decline(self.world, pid, nid)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "Pact %d closed." % pid

    def pact_break(self, uid, pid):
        nid = self.nid(uid)
        try:
            pacts.end(self.world, pid, nid)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "Pact %d broken. Everyone will see it in the next report." % pid

    def pact_list(self, uid):
        nid = self.nid(uid)
        w = self.world
        mine = pacts.for_nation(w, nid)
        if not mine:
            return "You have no pacts. Propose one with /pact propose."
        lines = []
        for p in mine:
            other = w["nations"][p["b"] if p["a"] == nid else p["a"]]["name"]
            state = "active" if p["status"] == "active" else ("waiting for them" if p["a"] == nid else "waiting for you")
            lines.append("Pact %d with %s (%s): %s" % (p["id"], other, state, p["text"]))
        return "\n".join(lines)

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
        o = trade.find(self.world, tid)
        if o and o["status"] == "open" and o["to"] is None and o["from"] != nid:
            raise GameError("That offer is open to everyone. Only %s can withdraw it. To pass, just ignore it." %
                            self.world["nations"][o["from"]]["name"])
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
        lines.append("\n".join("- " + a for a in advice.advice(w, r, nid)))
        lines.append("Land: %d tiles | %ss: %d/%d | %ss: %d/%d" % (
            len(W.owned(w, nid)), self.word("worker"), n["workers"], hw, self.word("soldier"), W.soldiers(w, nid), hs))
        lines.append("Stock: " + self.fmt(n["resources"]))
        lines.append("Priorities: " + ", ".join("%s %d" % (k, v) for k, v in n["priorities"].items()))
        lines.append("Next claim costs: " + self.fmt(tick.claim_cost(w, r, nid)))
        for a in w["armies"]:
            if a["owner"] == nid:
                lines.append("Army %d: %d at %s%s" % (a["id"], a["n"], a["tile"], " -> %s" % a["dest"] if a["dest"] else ""))
        for o in w["trades"]:
            if o["status"] == "open" and o["to"] in (nid, None) and o["from"] != nid:
                lines.append("Offer %d from %s: gives %s, wants %s" % (
                    o["id"], w["nations"][o["from"]]["name"], self.fmt(o["give"].get("resources", {})),
                    self.fmt(o["get"].get("resources", {}))))
        for p in pacts.for_nation(w, nid):
            other = w["nations"][p["b"] if p["a"] == nid else p["a"]]["name"]
            lines.append("Pact %d with %s (%s): %s" % (p["id"], other, p["status"], p["text"]))
        queued = [o for o in w["orders"] if o["nation"] == nid]
        if queued:
            lines.append("Queued orders: %d" % len(queued))
        news = w.get("last_events", {}).get(str(nid), [])
        if news:
            lines.append("Last night:\n- " + "\n- ".join(news[:10]))
        return "\n".join(lines)

    def info_page(self, uid, tab="overview", resource="food"):
        """The /info page for one tab, as data for the bot to show."""
        return infopage.page(self.world, self.rules, self.nid(uid), tab, resource)

    def set_priority(self, uid, resource, value):
        nid = self.nid(uid)
        if resource not in self.rules["resources"]:
            raise GameError("Unknown resource '%s'." % resource)
        try:
            tick.set_priorities(self.world, self.rules, nid, {resource: value})
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "%s priority is now %d." % (resource.capitalize(), value)

    def report(self, uid):
        nid = self.nid(uid)
        row = self.world.get("report", {}).get(str(nid))
        return guide.report(self.world, self.rules, nid, row, self.world.get("last_events", {}).get(str(nid), []))

    def help(self, tab="basics"):
        if self.rules is None:
            raise GameError("No game has been set up yet.")
        return guide.help_page(self.rules, tab)

    def standings(self):
        self.need_running()
        rows = views.public_standings(self.world, self.rules, -1) if not self.world["settings"]["fog"] else []
        rows = sorted(rows, key=lambda x: (-x["land"], -x["population"]))
        if not rows:
            return "Standings are hidden while fog of war is on."
        table = "\n".join("%d. %s%s - %d tiles, %d people%s" % (
            i + 1, r["name"], " (%s)" % r["rank"] if r.get("rank") else "", r["land"], r["population"],
            "" if r["alive"] else " (fallen)") for i, r in enumerate(rows[:15]))
        return "\n".join([table] + advice.race(self.world, self.rules))

    def public_events(self):
        """The last night's public events (empty while fog of war hides them)."""
        pacts_news = list(self.world.get("last_pacts", []))
        if self.world["settings"]["fog"]:
            return pacts_news
        return pacts_news + list(self.world.get("last_public", []))

    def welcome(self, name):
        return guide.welcome(self.rules, name)

    def start_messages(self):
        """{discord user id: first-night message} for everyone in a game that has just started."""
        return {uid: guide.started(self.world, self.rules, nid) for uid, nid in self.players.items()}

    def titles(self):
        """{discord user id: title} for the bot to turn into roles. Empty if the mode has no titles."""
        if self.phase not in ("running", "over") or "ranks" not in self.rules:
            return {}
        return {uid: views.rank_title(self.world, self.rules, nid)
                for uid, nid in self.players.items() if self.world["nations"][nid]["alive"]}

    # ---------- the nightly tick ----------

    def run_tick(self, split_log=False):
        """Run one tick. Returns the public report text. With split_log the night's public events are left out
        (the bot posts them in the game-log channel instead; see public_events)."""
        self.need_running()
        before = {n["id"]: {"land": len(W.owned(self.world, n["id"])), "workers": n["workers"],
                            "soldiers": W.soldiers(self.world, n["id"]), "res": dict(n["resources"])}
                  for n in self.world["nations"] if n["alive"]}
        tick.run_tick(self.world, self.rules)
        advice.record(self.world, self.rules)
        self.world["report"] = {str(i): dict(b, tick=self.world["tick"]) for i, b in before.items()}
        events = self.world["events"]
        self.world["last_public"] = list(events.get("public", []))
        self.world["last_pacts"] = list(events.get("pacts", []))
        self.world["last_events"] = events
        self.world["events"] = {}
        self.store.snapshot(self.world["tick"], W.to_json(self.world))
        lines = ["**Tick %d**" % self.world["tick"]]
        if not split_log:
            shown = events.get("pacts", []) + ([] if self.world["settings"]["fog"] else events.get("public", []))
            lines += ["- " + e for e in shown[:15]]
        lines += advice.race(self.world, self.rules)
        if self.world["winner"]:
            self.phase = "over"
            lines.append("**%s wins: %s**" % (self.world["nations"][self.world["winner"]["nation"]]["name"],
                                              self.world["winner"]["reason"]))
        self.save()
        return "\n".join(lines)
