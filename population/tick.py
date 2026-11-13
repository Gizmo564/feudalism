"""Orders and the nightly tick. All randomness comes from one seeded generator per tick."""
import math
import random
from collections import deque

from . import world as W
from . import combat, trade, victory
from .hexes import distance, neighbors

ORDER_TYPES = ("claim", "build", "upgrade", "train", "move")


# ---------- player actions (stored now, carried out at the next tick) ----------

def describe_order(o):
    """One line for a queued order."""
    kind = o["type"]
    if kind == "claim":
        return "Claim " + o["tile"]
    if kind == "build":
        return "Build %s on %s" % (o["building"], o["tile"])
    if kind == "move":
        return "Army %d to %s" % (o["army"], o["dest"])
    if kind == "train":
        return "Train %d%s" % (o["n"], " steel" if o.get("steel") else "")
    if kind == "upgrade":
        return "Upgrade %s" % o["tile"]
    return kind


def queue(world, rules, nid, order):
    """Save an order. Checks that happen only at tick time (cost, adjacency) are not repeated here."""
    if not world["nations"][nid]["alive"]:
        raise ValueError("your nation has been eliminated")
    if order.get("type") not in ORDER_TYPES:
        raise ValueError("unknown order type")
    if order["type"] == "build" and order.get("building") not in rules["buildings"]:
        raise ValueError("unknown building")
    if order["type"] == "upgrade" and not isinstance(order.get("tile"), str):
        raise ValueError("upgrade needs a tile")
    if order["type"] == "train" and not (isinstance(order.get("n"), int) and order["n"] > 0):
        raise ValueError("train needs a positive number")
    o = dict(order)
    o["nation"] = nid
    world["orders"].append(o)
    world["nations"][nid]["active"] = True


def set_priorities(world, rules, nid, prios):
    """prios: {"food": 5, ...}. 0 turns a resource off. Only the ratios matter."""
    n = world["nations"][nid]
    if not n["alive"]:
        raise ValueError("your nation has been eliminated")
    for r, v in prios.items():
        if r not in rules["resources"]:
            raise ValueError("unknown resource %s" % r)
        if not isinstance(v, int) or not 0 <= v <= 5:
            raise ValueError("priority must be 0 to 5")
    n["priorities"].update(prios)
    n["active"] = True


def set_assign(world, rules, nid, counts):
    """Advanced mode: counts is {"food": 8, ...}; a value of None goes back to automatic for that resource."""
    n = world["nations"][nid]
    if not n["alive"]:
        raise ValueError("your nation has been eliminated")
    for r, v in counts.items():
        if r not in rules["resources"]:
            raise ValueError("unknown resource %s" % r)
        if v is not None and not (isinstance(v, int) and 0 <= v <= 10000):
            raise ValueError("a worker count must be a whole number, 0 or more")
    assign = dict(n.get("assign", {}))
    for r, v in counts.items():
        if v is None:
            assign.pop(r, None)
        else:
            assign[r] = v
    n["assign"] = assign
    n["active"] = True


# ---------- helpers ----------

def allocate(workers, weights, cap):
    """Spread whole workers over keys in proportion to weights, at most `cap` each (cap can be a number or {key: number})."""
    room = cap if isinstance(cap, dict) else {k: cap for k in weights}
    alloc = {k: 0 for k in weights}
    remaining = workers
    active = set(k for k, w in weights.items() if w > 0)
    while remaining > 0 and active:
        tw = sum(weights[k] for k in active)
        given = 0
        rem = []
        for k in sorted(active):
            num = remaining * weights[k]
            take = min(num // tw, room[k] - alloc[k])
            alloc[k] += take
            given += take
            rem.append((-(num % tw), k))
        remaining -= given
        for _, k in sorted(rem):
            if remaining == 0:
                break
            if alloc[k] < room[k]:
                alloc[k] += 1
                remaining -= 1
        active = set(k for k in active if alloc[k] < room[k])
    return alloc


def find_path(world, start, dest):
    """Tiles to walk from start to dest (start not included): over land only if that works, else over land and
    ports. [] if you are already there, None if there is no way."""
    if start == dest:
        return []
    for ports in (False, True):
        prev = {start: None}
        q = deque([start])
        while q and dest not in prev:
            cur = q.popleft()
            for n in neighbors(cur):
                t = world["tiles"].get(n)
                if t is None or n in prev or (t["terrain"] == "water" and not (ports and t["building"] == "port")):
                    continue
                prev[n] = cur
                q.append(n)
        if dest in prev:
            path, step = [], dest
            while step != start:
                path.append(step)
                step = prev[step]
            return path[::-1]
    return None


def next_step(world, start, dest):
    """First tile on the way from start to dest, or None."""
    path = find_path(world, start, dest)
    return path[0] if path else None


def port_problem(world, rules, nid, k):
    """Why nation nid can't build a port on tile k (a sentence), or None if it can."""
    t = world["tiles"].get(k)
    if t is None or t["terrain"] != "water":
        return "a port goes on water"
    if t["building"] or t["owner"] is not None:
        return "there is already a port here"
    if not any(world["tiles"][x]["owner"] == nid for x in neighbors(k) if x in world["tiles"]):
        return "it must touch your land or one of your ports"
    if not any(x["owner"] == nid and x["building"] == "market" for x in world["tiles"].values()):
        return "you need a market first"
    return None


def crosses_water(world, path):
    return any(world["tiles"][k]["terrain"] == "water" for k in path or [])


def can_pay(res, cost):
    return all(res[r] >= v for r, v in cost.items())


def pay(res, cost):
    for r, v in cost.items():
        res[r] -= v


def build_cost(world, rules, nid, building, queued=0):
    """Each building you already own of the same kind makes the next one dearer. `queued` counts ones already ordered tonight."""
    rise = rules.get("build", {}).get("rise_percent", 0)
    have = sum(1 for t in world["tiles"].values() if t["owner"] == nid and t["building"] == building) + queued
    return {r: -(-v * (100 + rise * have) // 100) if v else 0 for r, v in rules["buildings"][building]["cost"].items()}


def upgrade_of(rules, tile):
    """The next upgrade of the building on this tile ({"cost": ..., housing numbers}), or None."""
    if tile["building"] in (None, "capital"):
        return None
    ups = rules["buildings"][tile["building"]].get("upgrades", [])
    level = W.level_of(tile)
    return ups[level - 1] if level - 1 < len(ups) else None


def claim_cost(world, rules, nid):
    c = rules["claim"]
    count = len(W.owned(world, nid))
    out = dict(c["base"])
    for r, v in c["per_tile"].items():
        out[r] = out.get(r, 0) + v * count
    share = c.get("share_percent", 0)
    if share:   # the price is a share of what you hold, so you can always afford some land; never below the minimum
        have = world["nations"][nid]["resources"]
        out = {}
        for r, m in c.get("share_min", {}).items():
            base = c.get("share_stone_percent", share) if r == "stone" else share    # stone has its own share
            pct = min(90, base + c.get("share_per_tile", 0) * count)
            out[r] = max(m, have.get(r, 0) * pct // 100)
    scarcity = c.get("scarcity_percent", 0)
    if scarcity:   # the fuller the map, the dearer the land; at a full map prices are (100 + scarcity)% of normal
        pct = world.get("claimed_pct", W.claimed_pct(world))
        out = {r: max(1, v * (10000 + scarcity * pct) // 10000) if v else 0 for r, v in out.items()}
    return out


def foreign_army_on(world, k, nid):
    return any(a["tile"] == k and a["owner"] != nid for a in world["armies"])


def kill_soldiers(world, nid, count):
    for a in sorted((a for a in world["armies"] if a["owner"] == nid), key=lambda a: -a["id"]):
        if count <= 0:
            break
        t = min(a["n"], count)
        a["n"] -= t
        count -= t
    world["armies"] = [a for a in world["armies"] if a["n"] > 0]


# ---------- orders ----------

def do_claim(world, rules, n, o):
    nid = n["id"]
    k = o.get("tile")
    t = world["tiles"].get(k)
    if k in world.get("contested", {}):
        return W.ev(world, nid, "Claim of %s skipped: it is contested, and the players involved are deciding who gets it" % k)
    if n["claims_this_tick"] >= rules["claim"]["per_tick"]:
        return W.ev(world, nid, "Claim of %s skipped: only %d claim(s) per tick" % (k, rules["claim"]["per_tick"]))
    if t is None or t["terrain"] == "water":
        return W.ev(world, nid, "Claim of %s failed: not land" % k)
    if t["owner"] is not None:
        return W.ev(world, nid, "Claim of %s failed: already owned" % k)
    if not any(world["tiles"][x]["owner"] == nid for x in neighbors(k) if x in world["tiles"]):
        return W.ev(world, nid, "Claim of %s failed: it must touch your land" % k)
    if foreign_army_on(world, k, nid):
        return W.ev(world, nid, "Claim of %s failed: foreign soldiers are there" % k)
    cost = claim_cost(world, rules, nid)
    if not can_pay(n["resources"], cost):
        return W.ev(world, nid, "Claim of %s failed: not enough resources (need %s)" % (k, cost))
    pay(n["resources"], cost)
    t["owner"] = nid
    n["claims_this_tick"] += 1
    W.ev(world, nid, "You claimed %s" % k)


def do_build(world, rules, n, o):
    nid = n["id"]
    k, b = o.get("tile"), o["building"]
    t = world["tiles"].get(k)
    if b == "port":
        problem = port_problem(world, rules, nid, k)
        if problem:
            return W.ev(world, nid, "Port on %s failed: %s" % (k, problem))
    elif t is None or t["owner"] != nid:
        return W.ev(world, nid, "Build on %s failed: not your tile" % k)
    elif t["terrain"] == "water" or t["building"] is not None:
        return W.ev(world, nid, "Build on %s failed: it already has a building" % k)
    if foreign_army_on(world, k, nid):
        return W.ev(world, nid, "Build on %s failed: foreign soldiers are there" % k)
    cost = build_cost(world, rules, nid, b)
    if not can_pay(n["resources"], cost):
        return W.ev(world, nid, "Build %s failed: not enough resources (need %s)" % (b, cost))
    pay(n["resources"], cost)
    t["owner"] = nid          # a port is built on water you don't own yet, next to your land
    t["building"] = b
    t.pop("level", None)
    good = refined_good(rules, b)
    if good and n["priorities"].get(good, 0) == 0:
        n["priorities"][good] = 2      # a new workshop starts working without a trip to the priorities
    W.ev(world, nid, "You built a %s on %s" % (b, k))


def do_upgrade(world, rules, n, o):
    nid = n["id"]
    k = o.get("tile")
    t = world["tiles"].get(k)
    if t is None or t["owner"] != nid:
        return W.ev(world, nid, "Upgrade of %s failed: not your tile" % k)
    up = upgrade_of(rules, t)
    if up is None:
        return W.ev(world, nid, "Upgrade of %s failed: nothing to upgrade there" % k)
    if foreign_army_on(world, k, nid):
        return W.ev(world, nid, "Upgrade of %s failed: foreign soldiers are there" % k)
    if not can_pay(n["resources"], up["cost"]):
        return W.ev(world, nid, "Upgrade of %s failed: not enough resources (need %s)" % (k, up["cost"]))
    pay(n["resources"], up["cost"])
    t["level"] = W.level_of(t) + 1
    W.ev(world, nid, "You upgraded the %s on %s to level %d" % (t["building"], k, t["level"]))


def steel_cfg(rules):
    cfg = rules.get("steel_soldiers", {})
    return cfg if cfg.get("enabled") else None


def steel_problem(world, rules, nid):
    """Why nation nid can't train steel soldiers (a sentence), or None if it can."""
    cfg = steel_cfg(rules)
    if cfg is None:
        return "steel soldiers are switched off on this server"
    best = max([W.level_of(t) for t in world["tiles"].values() if t["owner"] == nid and t["building"] == "barracks"] or [0])
    if best < cfg["min_barracks_level"]:
        return "you need a barracks upgraded to level %d" % cfg["min_barracks_level"]
    return None


def train_cost(rules, steel=False):
    cost = dict(rules["soldier"]["train_cost"])
    if steel:
        cost["steel"] = cost.get("steel", 0) + steel_cfg(rules)["steel"]
    return cost


def strength(rules, a):
    """An army's fighting strength: its soldiers, plus the steel bonus."""
    cfg = steel_cfg(rules)
    return a["n"] * (100 + cfg["bonus_percent"]) // 100 if a.get("steel") and cfg else a["n"]


def do_train(world, rules, n, o):
    nid = n["id"]
    steel = bool(o.get("steel"))
    if steel and steel_problem(world, rules, nid):
        return W.ev(world, nid, "Steel training failed: %s" % steel_problem(world, rules, nid))
    cost = train_cost(rules, steel)
    limits = {"barracks room": W.housing(world, rules, nid)[1] - W.soldiers(world, nid), "workers (one must stay)": n["workers"] - 1}
    for r, v in cost.items():
        if v > 0:
            limits[r] = n["resources"][r] // v
    count = max(0, min([o["n"]] + list(limits.values())))
    word = "steel soldiers" if steel else "soldiers"
    if count > 0:
        for r, v in cost.items():
            n["resources"][r] -= v * count
        n["workers"] -= count
        W.add_army(world, nid, n["capital"], count, steel=steel)
    left = o["n"] - count
    if left <= 0:
        return W.ev(world, nid, "You trained %d %s" % (count, word))
    why = min(limits, key=limits.get)       # the tightest limit
    W.ev(world, nid, "You trained %d of %d %s, limited by %s" % (count, o["n"], word, why))


def do_move(world, rules, n, o):
    nid = n["id"]
    a = next((x for x in world["armies"] if x["id"] == o.get("army") and x["owner"] == nid), None)
    t = world["tiles"].get(o.get("dest"))
    if a is None or t is None or (t["terrain"] == "water" and t["building"] != "port"):
        return W.ev(world, nid, "Move failed: bad army or destination")
    path = find_path(world, a["tile"], o["dest"])
    if crosses_water(world, path):          # sailing over ports costs a flat amount of wood for the boat
        fee = rules.get("port", {}).get("boat_wood", 0)
        if n["resources"].get("wood", 0) < fee:
            return W.ev(world, nid, "Army %d can't sail: a boat costs %d wood" % (a["id"], fee))
        n["resources"]["wood"] -= fee
        W.ev(world, nid, "A boat for army %d cost %d wood" % (a["id"], fee))
    count = o.get("n")
    if count is not None and 0 < count < a["n"]:        # send only part of the army (advanced mode)
        a["n"] -= count
        W.add_army(world, nid, a["tile"], count, dest=o["dest"] if o["dest"] != a["tile"] else None, steel=a.get("steel"))
        return
    a["dest"] = o["dest"] if o["dest"] != a["tile"] else None


# ---------- contested claims: two nations claim the same free tile in one night ----------

SOLDIER_RANGE = 3          # soldiers this close to a contested tile count in the duel


def find_contests(world, rules):
    """Pull claim orders for the same free tile by two or more nations out of the night. The tile stays free and is
    'contested' until each of them chooses to give it up or fight (see resolve_contests)."""
    claims = {}
    for o in world["orders"]:
        if o["type"] == "claim" and world["nations"][o["nation"]]["alive"]:
            claims.setdefault(o.get("tile"), set()).add(o["nation"])
    for k, ids in sorted(claims.items()):
        t = world["tiles"].get(k)
        if len(ids) < 2 or t is None or t["terrain"] == "water" or t["owner"] is not None or k in world.get("contested", {}):
            continue
        ids = sorted(ids)
        world.setdefault("contested", {})[k] = {"nations": ids, "choices": {}}
        world["orders"] = [o for o in world["orders"] if not (o["type"] == "claim" and o.get("tile") == k)]
        names = " and ".join(world["nations"][i]["name"] for i in ids)
        for i in ids:
            W.ev(world, i, "%s is contested between %s. Give it up or fight for it (in /play or /contest) before the next night; "
                 "no answer counts as fight." % (k, names))
        W.pub(world, "%s is contested between %s" % (k, names))


def soldiers_near(world, rules, nid, k):
    return sum(strength(rules, a) for a in world["armies"] if a["owner"] == nid and distance(a["tile"], k) <= SOLDIER_RANGE)


def lose_soldiers_near(world, nid, k, count):
    for a in sorted((a for a in world["armies"] if a["owner"] == nid and distance(a["tile"], k) <= SOLDIER_RANGE),
                    key=lambda a: (distance(a["tile"], k), a["id"])):
        if count <= 0:
            break
        t = min(a["n"], count)
        a["n"] -= t
        count -= t
    world["armies"] = [a for a in world["armies"] if a["n"] > 0]


def resolve_contests(world, rules, rng):
    """Start of a night: settle every contested tile. Nations that gave it up are out; one left gets it,
    several left fight a duel (a roll like combat, using the soldiers they have near the tile; the winner keeps
    the tile, the losers lose soldiers)."""
    done = world.setdefault("contested_done", [])
    for k in sorted(world.get("contested", {})):
        c = world["contested"].pop(k)
        names = world["nations"]
        left = [i for i in c["nations"] if world["nations"][i]["alive"] and c["choices"].get(str(i)) != "yield"]
        if not left:
            for i in c["nations"]:
                W.ev(world, i, "Nobody fought for %s, so it is free again" % k)
            W.pub(world, "%s: everyone gave it up, so it is free again" % k)
            done.append({"tile": k, "post": c.get("post"), "result": "Given up by everyone, free again."})
            continue
        winner = left[0]
        for other in left[1:]:
            a, d = soldiers_near(world, rules, winner, k), soldiers_near(world, rules, other, k)
            if a == 0 and d == 0:
                win, lose = (winner, other) if rng.random() < 0.5 else (other, winner)
            else:
                res = combat.fight(rules, rng, a, d, 0)
                win, lose = (winner, other) if res["attacker_wins"] else (other, winner)
                loss_a, loss_d = res["att_loss"], res["def_loss"]
                lose_soldiers_near(world, winner, k, loss_a)
                lose_soldiers_near(world, other, k, loss_d)
                W.ev(world, winner, "Duel for %s against %s: you lost %d soldiers" % (k, names[other]["name"], loss_a))
                W.ev(world, other, "Duel for %s against %s: you lost %d soldiers" % (k, names[winner]["name"], loss_d))
            W.ev(world, lose, "You lost the duel for %s to %s" % (k, names[win]["name"]))
            winner = win
        if len(left) > 1:
            W.pub(world, "%s won the fight for %s" % (names[winner]["name"], k))
        else:
            W.pub(world, "%s got %s: the others gave it up" % (names[winner]["name"], k))
        n = names[winner]
        before = world["tiles"][k]["owner"]
        do_claim(world, rules, n, {"tile": k})
        got = world["tiles"][k]["owner"] == winner and before is None
        done.append({"tile": k, "post": c.get("post"), "result": "%s %s." % (
            names[winner]["name"], "now owns it" if got else "won it but could not take it (cost or position)")})


def apply_orders(world, rules, rng):
    find_contests(world, rules)
    order = list(world["nations"])
    rng.shuffle(order)
    for n in order:
        if not n["alive"]:
            continue
        for o in [x for x in world["orders"] if x["nation"] == n["id"]]:
            {"claim": do_claim, "build": do_build, "upgrade": do_upgrade, "train": do_train, "move": do_move}[o["type"]](world, rules, n, o)
    world["orders"] = []


# ---------- movement and battle ----------

def attack(world, rules, rng, a, k):
    """Army `a` steps onto tile k. Fights any soldiers there; captures the tile if it is enemy land."""
    tile = world["tiles"][k]
    defenders = [d for d in world["armies"] if d["tile"] == k and d["owner"] != a["owner"]]
    owner = tile["owner"]
    me = world["nations"][a["owner"]]
    if owner is not None and owner != a["owner"] and world["nations"][owner]["protected_until"] > world["tick"]:
        a["dest"] = None
        return W.ev(world, a["owner"], "%s is protected for now - attack on %s called off" % (world["nations"][owner]["name"], k))
    if owner is None and not defenders:
        a["tile"] = k
        return
    if owner == a["owner"] and not defenders:
        a["tile"] = k
        return
    dn_real = sum(d["n"] for d in defenders)
    dn = sum(strength(rules, d) for d in defenders)
    an = strength(rules, a)
    terrain_only = world["tiles"][k]["terrain"]
    if owner is not None and owner != a["owner"]:
        bonus = combat.defense_bonus(world, rules, k)
    else:
        bonus = rules["terrain"][terrain_only]["defense_percent"]
    res = combat.fight(rules, rng, an, dn, bonus)
    att_real = min(a["n"], -(-res["att_loss"] * a["n"] // an)) if an and res["att_loss"] else 0   # losses are counted in strength
    a["n"] -= att_real
    res["att_loss"] = att_real
    left = res["def_loss"] * dn_real // dn if dn else 0
    for d in sorted(defenders, key=lambda d: d["id"]):
        t = min(d["n"], left)
        d["n"] -= t
        left -= t
    world["armies"] = [x for x in world["armies"] if x["n"] > 0]
    names = ", ".join(sorted(set(world["nations"][d["owner"]]["name"] for d in defenders))) or "nobody"
    if res["attacker_wins"]:
        for d in defenders:
            if d["n"] <= 0:
                continue
            home = sorted(x for x in neighbors(k) if x in world["tiles"] and world["tiles"][x]["owner"] == d["owner"])
            if home:
                d["tile"], d["dest"] = home[0], None
            else:
                d["n"] = 0
        world["armies"] = [x for x in world["armies"] if x["n"] > 0]
        a["tile"], a["dest"] = k, None
        if owner is not None and owner != a["owner"]:
            was_capital = tile["building"] == "capital"
            tile["owner"], tile["building"] = a["owner"], None
            if tile["terrain"] == "water":      # a captured port is wrecked: the water is nobody's again
                tile["owner"] = None
            W.ev(world, owner, "%s captured your tile %s" % (me["name"], k))
            W.pub(world, "%s captured %s from %s" % (me["name"], k, world["nations"][owner]["name"]))
            if was_capital and rules["combat"].get("capital_takes_all_land"):
                loser = world["nations"][owner]
                knock_out(world, loser, a["owner"])
                W.ev(world, a["owner"], "You captured the capital of %s and took all their land" % loser["name"])
                W.ev(world, owner, "Your capital fell, and with it everything you held")
        W.ev(world, a["owner"], "Won the battle at %s against %s (you lost %d)" % (k, names, res["att_loss"]))
        for d in defenders:
            W.ev(world, d["owner"], "Lost the battle at %s to %s" % (k, me["name"]))
    else:
        a["dest"] = None
        W.ev(world, a["owner"], "Lost the attack on %s (you lost %d)" % (k, res["att_loss"]))
        for d in defenders:
            W.ev(world, d["owner"], "You held %s against %s" % (k, me["name"]))
    world["armies"] = [x for x in world["armies"] if x["n"] > 0]


def move_armies(world, rules, rng):
    rank = {n["id"]: i for i, n in enumerate(sorted(world["nations"], key=lambda _: rng.random()))}
    for a in sorted([x for x in world["armies"] if x["dest"]], key=lambda x: (rank[x["owner"]], x["id"])):
        if a not in world["armies"]:
            continue
        for i in range(rules["movement"].get("army_steps_on_own_land", 1)):   # extra steps only over your own land
            if a not in world["armies"] or not a["dest"]:
                break
            step = next_step(world, a["tile"], a["dest"])
            if step is None:
                a["dest"] = None
                break
            own_land = world["tiles"][step]["owner"] == a["owner"] and world["tiles"][a["tile"]]["owner"] == a["owner"]
            attack(world, rules, rng, a, step)
            if a in world["armies"] and a["tile"] == a["dest"]:
                a["dest"] = None
            if not own_land:
                break
    merged = []
    for a in world["armies"]:
        same = next((m for m in merged if m["owner"] == a["owner"] and m["tile"] == a["tile"]
                     and m["dest"] == a["dest"] and bool(m.get("steel")) == bool(a.get("steel"))), None)
        if same:
            same["n"] += a["n"]
        else:
            merged.append(a)
    world["armies"] = merged


def oil_upkeep(world, rules):
    cfg = rules["oil_upkeep"]
    for n in world["nations"]:
        if not n["alive"]:
            continue
        away = [a for a in world["armies"]
                if a["owner"] == n["id"] and world["tiles"][a["tile"]]["owner"] != n["id"]]
        total = sum(a["n"] for a in away)
        if total == 0:
            continue
        need = math.ceil(total / cfg["soldiers_per_oil"])
        if n["resources"]["oil"] >= need:
            n["resources"]["oil"] -= need
            continue
        n["resources"]["oil"] = 0
        lost = 0
        for a in away:
            loss = max(1, a["n"] * cfg["shortfall_loss_percent"] // 100)
            loss = min(loss, a["n"])
            a["n"] -= loss
            lost += loss
        world["armies"] = [a for a in world["armies"] if a["n"] > 0]
        W.ev(world, n["id"], "No oil for your armies abroad: %d soldiers lost" % lost)


# ---------- economy ----------

def refined_good(rules, building):
    """The refined good a workshop building makes (tools for a toolworks), or None for any other building."""
    for good, spec in rules.get("refine", {}).items():
        if spec["building"] == building:
            return good
    return None


def tile_cap(world, rules, k):
    """Workers one tile holds: a workshop has a few job slots, land holds workers_per_tile."""
    t = world["tiles"][k]
    if refined_good(rules, t["building"]):
        return W.spec_at(rules["buildings"][t["building"]], W.level_of(t)).get("slots", rules["refine_slots"])
    return rules["workers_per_tile"]


def candidates(world, rules, k):
    t = world["tiles"][k]
    good = refined_good(rules, t["building"])
    if good:
        return [good]
    res = list(rules["terrain"][t["terrain"]]["produces"])
    if t["oil"]:
        res.append("oil")
    return res


def is_coastal(world, k):
    return any(world["tiles"][x]["terrain"] == "water" for x in neighbors(k) if x in world["tiles"])


def tile_plan(world, rules, n):
    """Where the workers go tonight, tile by tile: {tile: {resource: (workers, amount)}}.
    A tile never holds more than workers_per_tile workers, so that is also its most output a night.
    Advanced mode: the exact counts in n["assign"] go first. Everyone left works by priority, but only on
    resources with no count of their own (a resource set to 0 is off)."""
    nid = n["id"]
    room = {k: tile_cap(world, rules, k) for k in W.owned(world, nid)
            if world["tiles"][k]["building"] is None or refined_good(rules, world["tiles"][k]["building"])}
    plan = {}

    def put(k, r, cnt):
        amount = cnt * rules["yield_per_worker"][r]
        if r == "food" and is_coastal(world, k):
            amount += cnt * rules["coastal_food_bonus"]
        old = plan.setdefault(k, {}).get(r, (0, 0))
        plan[k][r] = (old[0] + cnt, old[1] + amount)

    workers = n["workers"]
    fixed = n.get("assign", {}) if n.get("mode") == "advanced" else {}
    for r in rules["resources"]:
        want = min(fixed.get(r, 0), workers)
        # tiles that can only make this resource first, so flexible tiles stay free for the others
        for k in sorted((k for k in room if r in candidates(world, rules, k)), key=lambda k: (len(candidates(world, rules, k)), k)):
            take = min(want, room[k])
            if take > 0:
                put(k, r, take)
                room[k] -= take
                want -= take
                workers -= take
    prio = {r: v for r, v in n["priorities"].items() if r not in fixed}
    if n.get("auto"):                                  # auto-refine: refine whenever the inputs are in store, never when they are not
        for r, spec in rules.get("refine", {}).items():
            if r in fixed:
                continue
            have = all(n["resources"].get(i, 0) >= v for i, v in spec["inputs"].items())
            prio[r] = max(prio.get(r, 0), 3) if have else 0
    weights, cands = {}, {}
    for k, left in room.items():
        c = [r for r in candidates(world, rules, k) if prio.get(r, 0) > 0]
        if c and left > 0:
            cands[k] = c
            weights[k] = max(prio[r] for r in c)
    for k, w in allocate(workers, weights, {k: room[k] for k in weights}).items():
        if w == 0:
            continue
        for r, cnt in allocate(w, {r: prio[r] for r in cands[k]}, w).items():
            if cnt:
                put(k, r, cnt)
    return plan


def work_plan(world, rules, n):
    """Totals for the whole nation: {resource: (workers, amount produced)}."""
    plan = {}
    for per in tile_plan(world, rules, n).values():
        for r, (cnt, amount) in per.items():
            got = plan.get(r, (0, 0))
            plan[r] = (got[0] + cnt, got[1] + amount)
    return plan


def burn_fuel(rules, res, workers):
    """Workshops burn fuel each night (rules "fuel"): coal first, then oil, which has more power. Returns the share (0 to 1) of the
    power needed that was found; with less than that the workshops make proportionally less."""
    cfg = rules.get("fuel")
    need = workers * cfg["per_worker"] if cfg else 0
    if need == 0:
        return 1
    power = 0
    for r in sorted(cfg["power"], key=lambda r: cfg["power"][r]):          # weakest fuel first
        use = min(res.get(r, 0), math.ceil((need - power) / cfg["power"][r]))
        if use > 0:
            res[r] -= use
            power += use * cfg["power"][r]
    return min(1, power / need)


def produce(world, rules, n):
    """Raw resources first, then workshops turn what is in the store into refined goods (never more than the inputs allow,
    and, where the ruleset has fuel, never more than the fuel burned allows)."""
    plan = work_plan(world, rules, n)
    res, refine = n["resources"], rules.get("refine", {})
    for r, (_, amount) in plan.items():
        if r not in refine:
            res[r] = res.get(r, 0) + amount
    share = burn_fuel(rules, res, sum(cnt for r, (cnt, _) in plan.items() if r in refine))
    n["made"] = 0                                    # factory output tonight (goods marked "output" in the ruleset)
    for r, (_, amount) in plan.items():
        if r in refine:
            inputs = refine[r]["inputs"]
            made = min([int(amount * share)] + [res.get(i, 0) // v for i, v in inputs.items()])
            for i, v in inputs.items():
                res[i] -= v * made
            res[r] = res.get(r, 0) + made
            if refine[r].get("output"):
                n["made"] += made


def eat_and_grow(world, rules, n):
    nid, res = n["id"], n["resources"]
    need = W.people(world, nid) * rules["eat_per_person"]
    used = min(res["food"], need)
    res["food"] -= used
    deficit = need - used
    value = rules["food_value"].get("livestock", 1)
    if deficit > 0 and res["livestock"] > 0:
        cattle = min(res["livestock"], math.ceil(deficit / value))
        res["livestock"] -= cattle
        deficit = max(0, deficit - cattle * value)
    if deficit > 0:
        deaths = max(1, deficit * rules["starvation_death_percent"] // 100)
        from_workers = min(n["workers"], deaths)
        n["workers"] -= from_workers
        kill_soldiers(world, nid, deaths - from_workers)
        W.ev(world, nid, "Famine: %d people starved" % deaths)
        return
    keep = need * rules["spoilage"]["keep_ticks"]
    if res["food"] > keep:
        res["food"] -= (res["food"] - keep) * rules["spoilage"]["percent"] // 100
    housing_w, housing_s = W.housing(world, rules, nid)
    if n["workers"] > housing_w:
        cut = max(1, (n["workers"] - housing_w) * rules["over_housing_shrink_percent"] // 100)
        n["workers"] -= cut
        W.ev(world, nid, "Not enough homes: %d workers left" % cut)
    else:
        grow = max(1, n["workers"] * rules["growth"]["percent"] // 100)
        per = rules["eat_per_person"] * rules["growth"]["reserve_ticks"]
        room = min(housing_w - n["workers"], res["food"] // per if per else grow)
        grow = max(0, min(grow, room))
        extra = max(0, min(res.get("provisions", 0), grow, room - grow))    # provisions: one spare peasant each, up to double growth
        if extra:
            res["provisions"] -= extra
            W.ev(world, nid, "Provisions fed faster growth: %d extra people" % extra)
        n["workers"] += grow + extra
    over = W.soldiers(world, nid) - housing_s
    if over > 0:
        cut = max(1, over * rules["over_housing_shrink_percent"] // 100)
        kill_soldiers(world, nid, cut)
        W.ev(world, nid, "Not enough barracks: %d soldiers left" % cut)


# ---------- end of tick ----------

def autopilot(world, rules):
    cfg = rules["autopilot"]
    for n in world["nations"]:
        if not n["alive"]:
            continue
        n["idle"] = 0 if n["active"] else n["idle"] + 1
        n["active"] = False
        if n["idle"] >= cfg["idle_ticks_before_decay"] and n["workers"] > 0:
            loss = max(1, n["workers"] * cfg["decay_percent"] // 100)
            n["workers"] -= loss
            W.ev(world, n["id"], "Your nation is idle: %d workers left" % loss)


def eliminate(world, rules):
    for n in world["nations"]:
        if not n["alive"]:
            continue
        if not W.owned(world, n["id"]) and W.soldiers(world, n["id"]) == 0 or W.people(world, n["id"]) == 0:
            knock_out(world, n, None)


def knock_out(world, n, taker):
    """Take a nation out of the game. Its land goes to `taker` (a nation id) or becomes nobody's."""
    n["alive"] = False
    for k, t in world["tiles"].items():
        if t["owner"] == n["id"]:
            t["owner"], t["building"] = taker, None
            if taker is not None and t["terrain"] == "water":      # ports are wrecked, as when one is captured
                t["owner"] = None
    world["armies"] = [a for a in world["armies"] if a["owner"] != n["id"]]
    for o in world["trades"]:
        if o["status"] == "open" and n["id"] in (o["from"], o["to"]):
            o["status"] = "cancelled"
    W.pub(world, "%s has been eliminated" % n["name"])


def run_tick(world, rules):
    if world["winner"] is not None:
        return
    rng = random.Random(world["seed"] * 1000003 + world["tick"])
    for n in world["nations"]:
        n["claims_this_tick"] = 0
    resolve_contests(world, rules, rng)
    apply_orders(world, rules, rng)
    move_armies(world, rules, rng)
    trade.move_caravans(world, rules)
    oil_upkeep(world, rules)
    for n in world["nations"]:
        if n["alive"]:
            produce(world, rules, n)
            eat_and_grow(world, rules, n)
    trade.deliver(world)
    trade.expire(world, rules)
    autopilot(world, rules)
    eliminate(world, rules)
    W.discover_oil(world)
    won = victory.check(world, rules)
    if won:
        world["winner"] = {"nation": won[0], "reason": won[1], "tick": world["tick"]}
        W.pub(world, "%s wins: %s" % (world["nations"][won[0]]["name"], won[1]))
    W.refresh_scarcity(world)
    world["tick"] += 1
