"""Everything the Discord commands do, with no Discord code in it. Each method returns plain text.
Errors that a player should see are raised as GameError."""
import json
import os
import random

from . import advice, guide, infopage, pacts, rulepanel, world as W, tick, trade, victory, views, ruleset
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
        self.signups_closed = store.get("signups_closed", False)
        self.pending_rules = store.get("pending_rules", {})   # {rule key: new value}, applied at the next tick
        self.dm_optin = store.get("dm_optin", [])             # discord user ids that chose to get DMs (off for everyone by default)

    def save(self):
        for k in ("phase", "signups", "players", "world", "rules", "pending_rules", "signups_closed", "dm_optin"):
            self.store.set(k, getattr(self, k))

    # ---------- setup (admin) ----------

    def open_signup(self, mode):
        path = os.path.join(RULES_DIR, mode + ".json")
        if not os.path.exists(path):
            raise GameError("Unknown mode '%s'. Modes: %s" % (mode, ", ".join(self.modes())))
        if self.phase == "running":
            raise GameError("A game is already running")
        self.rules = ruleset.load(path)
        self.pending_rules, self.signups_closed = {}, False
        self.phase, self.signups, self.players, self.world = "signup", [], {}, None
        self.save()
        return "Sign-ups are open for **%s**. Players use /join." % self.rules["name"]

    @staticmethod
    def modes():
        return sorted(f[:-5] for f in os.listdir(RULES_DIR) if f.endswith(".json"))

    def start(self, radius=None, seed=None, expected_players=None):
        """radius blank: the map is sized for expected_players, or for the people who joined."""
        if self.phase != "signup":
            raise GameError("Open sign-ups first")
        if not self.signups:
            raise GameError("Nobody has joined yet")
        if radius is None:
            radius = W.default_radius(max(expected_players or 0, len(self.signups)))
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

    # ---------- rules the admin can change (applied at the next tick) ----------

    def rule_rows(self):
        """One dict per changeable rule: key, label, kind, current value, pending value (or None), limits, note."""
        self.need_rules()
        rows = []
        for key in rulepanel.keys(self.rules):
            _, label, kind, _, _, limits, note, tab = rulepanel.BY_KEY[key]
            rows.append({"key": key, "label": label, "kind": kind, "limits": limits, "note": note, "tab": tab,
                         "risky": key in rulepanel.RISKY, "advanced": key in rulepanel.ADVANCED, "now": rulepanel.current(self.world, self.rules, key),
                         "next": self.pending_rules.get(key)})
        return rows

    def queue_rule(self, key, value):
        """Running game: ask for a change at the next tick (setting it back to the current value cancels the
        request). Sign-up phase: nothing is running yet, so the change happens right away."""
        self.need_rules()
        try:
            value = rulepanel.check(self.rules, key, value)
        except ValueError as e:
            raise GameError(str(e))
        now = rulepanel.current(self.world, self.rules, key)
        if self.phase == "signup":
            rulepanel.apply(None, self.rules, key, value)
            self.save()
            return "%s is now %s (the game hasn't started, so it takes effect right away)." % (
                rulepanel.label(key), rulepanel.show(value))
        if value == now:
            self.pending_rules.pop(key, None)
            text = "%s stays %s." % (rulepanel.label(key), rulepanel.show(now))
        else:
            self.pending_rules[key] = value
            text = "%s changes from %s to %s at the next tick." % (rulepanel.label(key), rulepanel.show(now),
                                                                  rulepanel.show(value))
        self.save()
        return text

    def reset_stock(self):
        """Admin: put every living nation's stockpile back to the starting amounts, right now."""
        self.need_running()
        start = dict(self.rules["start"]["resources"])
        for r in self.rules["resources"]:                       # use the amounts waiting for the next tick, if any
            if "start_" + r in self.pending_rules:
                start[r] = self.pending_rules["start_" + r]
        alive = [n for n in self.world["nations"] if n["alive"]]
        for n in alive:
            n["resources"] = {r: start.get(r, 0) for r in self.rules["resources"]}
            W.ev(self.world, n["id"], "The admin reset your stockpile to the starting amounts")
        self.save()
        return "Reset the stockpile of %d nation%s to the starting amounts." % (len(alive), "" if len(alive) == 1 else "s")

    def reset_rules(self):
        """Ask for every changeable rule to go back to the mode's own ruleset file."""
        self.need_rules()
        original = next((r for r in (ruleset.load(os.path.join(RULES_DIR, f)) for f in sorted(os.listdir(RULES_DIR))
                                      if f.endswith(".json")) if r["name"] == self.rules["name"]), None)
        if original is None:
            raise GameError("I can't find the default rules for %s" % self.rules["name"])
        changed = 0
        for key in rulepanel.keys(self.rules):
            want = rulepanel.current(None, original, key)
            if want != rulepanel.current(self.world, self.rules, key) or key in self.pending_rules:
                self.queue_rule(key, want)
                changed += 1
        if not changed:
            return "Every rule is already at its default."
        when = "right away" if self.phase == "signup" else "at the next tick"
        return "%d rule%s will go back to the default %s." % (changed, "" if changed == 1 else "s", when)

    def need_rules(self):
        if self.phase not in ("signup", "running"):
            raise GameError("No game is set up yet. Use /admin open first")

    def apply_pending_rules(self):
        """Put the queued rule changes into effect. Returns one line per change for the announcement."""
        lines = []
        for key in rulepanel.keys(self.rules):
            if key in self.pending_rules:
                old, new = rulepanel.current(self.world, self.rules, key), self.pending_rules[key]
                rulepanel.apply(self.world, self.rules, key, new)
                lines.append("Rule change: %s %s -> %s" % (rulepanel.label(key), rulepanel.show(old), rulepanel.show(new)))
        self.pending_rules = {}
        return lines

    def expand(self, rings):
        self.need_running()
        W.expand_map(self.world, self.rules, rings)
        self.save()
        return "Map expanded to radius %d (%d tiles)." % (self.world["radius"], len(self.world["tiles"]))

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
        rulepanel.sync_world(self.world, self.rules)        # rules you set since then stay in force
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
        if self.signups_closed:
            raise GameError("Sign-ups are closed. Ask an admin to add you.")
        if any(s["uid"] == uid for s in self.signups):
            raise GameError("You have already joined")
        self.check_name(name)
        self.signups.append({"uid": uid, "name": name})
        self.save()
        return "Welcome, **%s**! (%d joined)" % (name, len(self.signups))

    def need_signup(self):
        if self.phase != "signup":
            raise GameError("Sign-ups are not open. Use /admin open first")

    def close_signups(self):
        """Stop taking /join without starting the game. Names already joined are kept."""
        self.need_signup()
        self.signups_closed = True
        self.save()
        return "Sign-ups are closed (%d joined). Use /admin start when you are ready." % len(self.signups)

    def reopen_signups(self):
        self.need_signup()
        self.signups_closed = False
        self.save()
        return "Sign-ups are open again (%d joined so far)." % len(self.signups)

    def clear_signups(self):
        """Wipe the list of names. Sign-ups stay closed until /admin reopen."""
        self.need_signup()
        self.signups, self.signups_closed = [], True
        self.save()
        return "The sign-up list is cleared and sign-ups are closed. Use /admin reopen to let people join again."

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
        if k in w.get("contested", {}):
            raise GameError("%s is contested right now. The players claiming it are choosing whether to give it up or fight." % k)
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
        if building == "port":
            problem = tick.port_problem(w, r, nid, k)
            if problem:
                raise GameError("Can't build a port on %s: %s." % (k, problem))
        else:
            if t is None or t["owner"] != nid:
                raise GameError("%s isn't yours. Build on land you own (pick it in /zoom)." % k)
            if t["building"]:
                raise GameError("%s already has a %s." % (k, t["building"]))
            if tick.foreign_army_on(w, k, nid):
                raise GameError("Foreign soldiers are standing on %s. Drive them off first." % k)
        cost = self.build_price(nid, building)
        missing = advice.shortfall(advice.available(w, r, nid), cost)
        if missing:
            raise GameError("Not enough for a %s: it costs %s and you are short %s." % (building, self.fmt(cost), self.fmt(missing)))

    def build_price(self, nid, building):
        """What the next one costs, counting the ones already ordered tonight."""
        queued = sum(1 for o in self.world["orders"] if o["nation"] == nid and o["type"] == "build" and o.get("building") == building)
        return tick.build_cost(self.world, self.rules, nid, building, queued)

    def check_upgrade(self, nid, k):
        w, r = self.world, self.rules
        t = w["tiles"].get(k)
        if t is None or t["owner"] != nid:
            raise GameError("%s isn't yours. Pick one of your own tiles in /zoom." % k)
        if t["building"] is None:
            raise GameError("%s has no building to upgrade." % k)
        up = tick.upgrade_of(r, t)
        if up is None:
            raise GameError("The %s on %s can't be upgraded any further." % (t["building"], k) if t["building"] != "capital"
                            else "A capital can't be upgraded.")
        if tick.foreign_army_on(w, k, nid):
            raise GameError("Foreign soldiers are standing on %s. Drive them off first." % k)
        if any(o["nation"] == nid and o["type"] == "upgrade" and o.get("tile") == k for o in w["orders"]):
            raise GameError("%s already has an upgrade queued for tonight." % k)
        missing = advice.shortfall(advice.available(w, r, nid), up["cost"])
        if missing:
            raise GameError("Not enough to upgrade the %s: it costs %s and you are short %s." % (t["building"], self.fmt(up["cost"]), self.fmt(missing)))

    def claim(self, uid, tile):
        nid = self.nid(uid)
        self.check_claim(nid, tile)
        self.queue(nid, {"type": "claim", "tile": tile})
        return "Claim of %s queued for tonight. Cost: %s." % (tile, self.fmt(tick.claim_cost(self.world, self.rules, nid)))

    def build(self, uid, tile, building):
        nid = self.nid(uid)
        self.check_build(nid, tile, building)
        price = self.build_price(nid, building)
        self.queue(nid, {"type": "build", "tile": tile, "building": building})
        return "Build %s on %s queued. Cost: %s." % (building, tile, self.fmt(price))

    def upgrade(self, uid, tile):
        nid = self.nid(uid)
        self.check_upgrade(nid, tile)
        t = self.world["tiles"][tile]
        up = tick.upgrade_of(self.rules, t)
        self.queue(nid, {"type": "upgrade", "tile": tile})
        return "Upgrade of the %s on %s to level %d queued. Cost: %s." % (t["building"], tile, W.level_of(t) + 1, self.fmt(up["cost"]))

    def train(self, uid, n, steel=False):
        nid = self.nid(uid)
        w, r = self.world, self.rules
        me = w["nations"][nid]
        if steel and tick.steel_problem(w, r, nid):
            raise GameError("Can't train steel %ss: %s." % (self.word("soldier"), tick.steel_problem(w, r, nid)))
        cost = tick.train_cost(r, steel)
        mine = [o for o in w["orders"] if o["nation"] == nid and o["type"] == "train"]   # trainings already queued tonight count against the limits
        queued = sum(o["n"] for o in mine)
        spent = {}
        for o in mine:
            for res, v in tick.train_cost(r, bool(o.get("steel"))).items():
                spent[res] = spent.get(res, 0) + v * o["n"]
        room = W.housing(w, r, nid)[1] - W.soldiers(w, nid) - queued
        can = max(0, min(n, room, me["workers"] - 1 - queued))
        for res, v in cost.items():
            if v > 0:
                can = min(can, max(0, me["resources"].get(res, 0) - spent.get(res, 0)) // v)
        if can <= 0:
            reasons = []
            if room <= 0:
                reasons.append("no room: build a barracks")
            if me["workers"] <= 1:
                reasons.append("not enough %ss" % self.word("worker"))
            short = advice.shortfall(me["resources"], cost)
            if short:
                reasons.append("short of %s" % self.fmt(short))
            raise GameError("Can't train any %ss right now: %s." % (self.word("soldier"), "; ".join(reasons) or "nothing available"))
        order = {"type": "train", "n": can}
        if steel:
            order["steel"] = True
        self.queue(nid, order)
        note = "" if can >= n else " (you asked for %d, but only %d fit right now)" % (n, can)
        return "Training %d %s%ss queued%s." % (can, "steel " if steel else "", self.word("soldier"), note)

    def contests(self, nid):
        """Tiles contested with you: [{"tile", "against": [names], "choice": "yield"/"fight"/None}]."""
        out = []
        for k, c in sorted(self.world.get("contested", {}).items()):
            if nid in c["nations"]:
                out.append({"tile": k, "against": [self.world["nations"][i]["name"] for i in c["nations"] if i != nid],
                            "choice": c["choices"].get(str(nid))})
        return out

    def contest(self, uid, tile, choice):
        """Give up a contested tile ("yield") or fight for it ("fight"). You can change your mind until the night."""
        nid = self.nid(uid)
        c = self.world.get("contested", {}).get(tile)
        if c is None or nid not in c["nations"]:
            raise GameError("%s isn't contested with you. /contest only works on tiles you and someone else claimed in the same night." % tile)
        if choice not in ("yield", "fight"):
            raise GameError("Choose yield or fight.")
        c["choices"][str(nid)] = choice
        self.world["nations"][nid]["active"] = True
        self.save()
        if choice == "yield":
            return "You will give up %s. If the others give up too it goes back to being free." % tile
        return ("You will fight for %s. If someone else fights too, the soldiers each of you has within 3 tiles roll a duel when the night starts; "
                "the loser loses some of them." % tile)

    def mode_of(self, uid):
        return self.world["nations"][self.nid(uid)].get("mode", "basic")

    def advanced_allowed(self):
        """Admins can switch Advanced off for the whole server (/admin settings advanced_mode 0)."""
        return self.store.get("settings", {}).get("advanced_mode", "1") == "1"

    def set_mode(self, uid, mode):
        nid = self.nid(uid)
        if mode not in ("basic", "advanced"):
            raise GameError("Mode is basic or advanced.")
        if mode == "advanced" and not self.advanced_allowed():
            raise GameError("Advanced mode is switched off on this server.")
        self.world["nations"][nid]["mode"] = mode
        self.save()
        if mode == "basic":
            return "Basic mode: your work priorities (0-5) decide who works on what. Your exact counts are kept if you switch back."
        return ("Advanced mode: set exact %s counts with /assign (for example 'food 8, wood 4'). Resources without a count "
                "still use your priorities. You can also send part of an army." % self.word("worker"))

    def need_advanced(self, nid):
        if self.world["nations"][nid].get("mode", "basic") != "advanced":
            raise GameError("That is an advanced-mode order. Turn it on with /mode advanced.")
        if not self.advanced_allowed():
            raise GameError("Advanced mode is switched off on this server.")

    def assign(self, uid, text):
        """/assign food 8, wood 4. 'auto' (or 'clear') puts everything back to priorities."""
        nid = self.nid(uid)
        self.need_advanced(nid)
        if text.strip().lower() in ("auto", "clear"):
            tick.set_assign(self.world, self.rules, nid, {r: None for r in self.rules["resources"]})
            self.save()
            return "All resources are automatic again (they follow your priorities)."
        try:
            tick.set_assign(self.world, self.rules, nid, parse_pairs(text))
        except ValueError as e:
            raise GameError("%s. Resources: %s." % (e, ", ".join(self.rules["resources"])))
        self.save()
        return "Worker counts updated. %s" % self.assign_line(nid)

    def set_assign(self, uid, resource, count):
        """One resource at a time, for the Activity. count None means automatic."""
        nid = self.nid(uid)
        self.need_advanced(nid)
        try:
            tick.set_assign(self.world, self.rules, nid, {resource: count})
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        return "%s: %s." % (resource.capitalize(), "automatic" if count is None else "%d %ss" % (count, self.word("worker")))

    def assign_line(self, nid):
        n = self.world["nations"][nid]
        fixed = n.get("assign", {})
        placed = tick.work_plan(self.world, self.rules, n)
        if not fixed:
            return "Nothing is set by hand: everyone follows your priorities."
        return "Set by hand: " + ", ".join("%s %d (working %d)" % (r, c, placed.get(r, (0, 0))[0]) for r, c in fixed.items()) + "."

    def move(self, uid, army, dest, count=None):
        nid = self.nid(uid)
        w = self.world
        mine = [a for a in w["armies"] if a["owner"] == nid]
        if count is not None:
            self.need_advanced(nid)
        if not any(a["id"] == army for a in mine):
            have = ", ".join("%d (at %s)" % (a["id"], a["tile"]) for a in mine) or "none"
            raise GameError("You have no army %d. Your armies: %s." % (army, have))
        t = w["tiles"].get(dest)
        if t is None:
            raise GameError("%s isn't on the map. Open /zoom and pick a tile from the menu." % dest)
        if t["terrain"] == "water" and t["building"] != "port":
            raise GameError("Armies can't stand in open water. Pick a land tile or one of the ports.")
        a = next(x for x in mine if x["id"] == army)
        boat = ""
        if tick.crosses_water(w, tick.find_path(w, a["tile"], dest)):
            fee = self.rules.get("port", {}).get("boat_wood", 0)
            if w["nations"][nid]["resources"].get("wood", 0) < fee:
                raise GameError("That route crosses water over ports, and a boat costs %d wood. You don't have enough." % fee)
            boat = " A boat will cost %d wood." % fee
        if count is not None and not 0 < count <= a["n"]:
            raise GameError("Army %d has %d %ss. Send between 1 and %d." % (army, a["n"], self.word("soldier"), a["n"]))
        order = {"type": "move", "army": army, "dest": dest}
        if count is not None and count < a["n"]:
            order["n"] = count
        self.queue(nid, order)
        if "n" in order:
            return "%d %ss from army %d will march toward %s (one tile a night); the rest stay.%s" % (count, self.word("soldier"), army, dest, boat)
        return "Army %d will march toward %s (one tile a night).%s" % (army, dest, boat)

    def orders(self, uid):
        """What you have queued for tonight, numbered (use the number with /cancel)."""
        nid = self.nid(uid)
        mine = [o for o in self.world["orders"] if o["nation"] == nid]
        if not mine:
            return "You have nothing queued for tonight."
        return "Queued for tonight:\n" + "\n".join("%d. %s" % (i + 1, tick.describe_order(o)) for i, o in enumerate(mine))

    def cancel(self, uid, number=None, text=None):
        """Clear all your queued orders, or just order `number` (as /orders numbers them). `text` guards against a changed list."""
        nid = self.nid(uid)
        mine = [o for o in self.world["orders"] if o["nation"] == nid]
        if number is None:
            self.world["orders"] = [o for o in self.world["orders"] if o["nation"] != nid]
            self.save()
            return "Cleared %d queued order%s." % (len(mine), "" if len(mine) == 1 else "s") if mine else "You had no queued orders."
        if not isinstance(number, int) or not 1 <= number <= len(mine):
            raise GameError("You have %d queued order%s. /orders shows them." % (len(mine), "" if len(mine) == 1 else "s"))
        gone = mine[number - 1]
        if text is not None and tick.describe_order(gone) != text:
            raise GameError("Your orders changed. Look at the list again.")
        self.world["orders"].remove(gone)
        self.save()
        return "Cancelled: %s." % tick.describe_order(gone)

    def priorities(self, uid, text):
        nid = self.nid(uid)
        try:
            tick.set_priorities(self.world, self.rules, nid, parse_pairs(text))
        except ValueError as e:
            raise GameError("%s. Resources: %s. Priorities go from 0 to 5." % (e, ", ".join(self.rules["resources"])))
        self.save()
        return "Priorities updated."

    def auto_refine(self, uid, on):
        nid = self.nid(uid)
        if not self.rules.get("refine"):
            raise GameError("This mode has no refined goods.")
        self.world["nations"][nid]["auto"] = on
        self.save()
        if on:
            return ("Auto-refine is on: %ss in a workshop work whenever its inputs are in store, and stay free for other jobs when they are not. "
                    "Your priorities still decide the rest. Turn off with /autorefine off." % self.word("worker"))
        return "Auto-refine is off: workshops follow your priorities only. Turn on with /autorefine on."

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

    def pending(self, nid):
        """What needs this nation's answer: pact proposals made to it, open trade offers it can accept, contested tiles it hasn't answered."""
        w = self.world
        mine = [{"id": p["id"], "line": "%s proposes pact %d: %s" % (w["nations"][p["a"]]["name"], p["id"], p["text"])}
                for p in pacts.for_nation(w, nid) if p["status"] == "proposed" and p["b"] == nid]
        offers = [{"id": o["id"], "line": self.offer_line(o)} for o in w["trades"]
                  if o["status"] == "open" and o["from"] != nid and o["to"] in (nid, None)]
        contests = [c for c in self.contests(nid) if c["choice"] is None]
        return {"pacts": mine, "offers": offers, "contests": contests}

    def notify(self, nid, text):
        """Queue a private message for a nation's player, if they chose DMs. The bot sends it after the command (take_notices)."""
        uid = next((u for u, n in self.players.items() if n == nid), None)
        if uid is not None and self.wants_dm(uid):
            self.notices = getattr(self, "notices", []) + [(uid, text)]

    def take_notices(self):
        out, self.notices = getattr(self, "notices", []), []
        return out

    def pact_propose(self, uid, to_name, text):
        nid, other = self.nid(uid), self.nation_named(to_name)
        try:
            pid = pacts.propose(self.world, nid, other["id"], text)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        self.notify(other["id"], "%s proposes pact %d: %s\nAnswer with /pact accept %d or /pact decline %d." % (
            self.world["nations"][nid]["name"], pid, " ".join(text.split()), pid, pid))
        return "Pact %d proposed to %s. It only counts if they accept; nothing enforces it." % (pid, other["name"])

    def pact_accept(self, uid, pid):
        nid = self.nid(uid)
        try:
            p = pacts.accept(self.world, pid, nid)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        self.notify(p["a"], "%s accepted pact %d: %s" % (self.world["nations"][nid]["name"], pid, p["text"]))
        return "Pact %d is now on the public record." % pid

    def pact_decline(self, uid, pid):
        nid = self.nid(uid)
        try:
            p = pacts.decline(self.world, pid, nid)
        except ValueError as e:
            raise GameError(str(e))
        self.save()
        other = p["b"] if nid == p["a"] else p["a"]
        self.notify(other, "%s %s pact %d: %s" % (self.world["nations"][nid]["name"], "withdrew" if nid == p["a"] else "declined", pid, p["text"]))
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
        if n.get("mode") == "advanced":
            lines.append("Advanced mode. " + self.assign_line(nid))
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
        rules = self.rules or ruleset.load(os.path.join(RULES_DIR, "feudalism.json"))
        return guide.help_page(rules, tab)

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

    def wants_dm(self, uid):
        return str(uid) in self.dm_optin

    def set_dm(self, uid, on):
        """Each player chooses for themselves; nobody (admins included) can switch it on for someone else."""
        uid = str(uid)
        if on and uid not in self.dm_optin:
            self.dm_optin = self.dm_optin + [uid]
        if not on:
            self.dm_optin = [u for u in self.dm_optin if u != uid]
        self.save()
        return ("Private messages are on: you will get your night report and private trade offers by DM. Turn off with /dms off."
                if on else "Private messages are off. Use /report any time to see your night.")

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
        rule_lines = self.apply_pending_rules()
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
        past = self.world.setdefault("past_reports", {})        # each player's last 7 night reports, for the Activity's Updates tab
        for i in before:
            rows = past.setdefault(str(i), [])
            rows.insert(0, {"tick": self.world["tick"], "text": guide.report(self.world, self.rules, i, self.world["report"][str(i)], events.get(str(i), []))})
            del rows[7:]
        self.world["events"] = {}
        self.store.snapshot(self.world["tick"], W.to_json(self.world))
        lines = ["**Tick %d**" % self.world["tick"]] + rule_lines
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
