"""Orders and the nightly tick. All randomness comes from one seeded generator per tick."""
import math
import random
from collections import deque

from . import world as W
from . import combat, trade, victory
from .hexes import neighbors

ORDER_TYPES = ("claim", "build", "upgrade", "train", "move")


# ---------- player actions (stored now, carried out at the next tick) ----------

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


# ---------- helpers ----------

def allocate(workers, weights, cap):
    """Spread whole workers over keys in proportion to weights, at most `cap` each."""
    alloc = {k: 0 for k in weights}
    remaining = workers
    active = set(k for k, w in weights.items() if w > 0)
    while remaining > 0 and active:
        tw = sum(weights[k] for k in active)
        given = 0
        rem = []
        for k in sorted(active):
            num = remaining * weights[k]
            take = min(num // tw, cap - alloc[k])
            alloc[k] += take
            given += take
            rem.append((-(num % tw), k))
        remaining -= given
        for _, k in sorted(rem):
            if remaining == 0:
                break
            if alloc[k] < cap:
                alloc[k] += 1
                remaining -= 1
        active = set(k for k in active if alloc[k] < cap)
    return alloc


def next_step(world, start, dest):
    """First tile on the shortest land path from start to dest, or None."""
    if start == dest:
        return None
    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == dest:
            break
        for n in neighbors(cur):
            t = world["tiles"].get(n)
            if t is None or t["terrain"] == "water" or n in prev:
                continue
            prev[n] = cur
            q.append(n)
    if dest not in prev:
        return None
    step = dest
    while prev[step] != start:
        step = prev[step]
    return step


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
    if t is None or t["owner"] != nid:
        return W.ev(world, nid, "Build on %s failed: not your tile" % k)
    if t["building"] is not None:
        return W.ev(world, nid, "Build on %s failed: it already has a building" % k)
    if foreign_army_on(world, k, nid):
        return W.ev(world, nid, "Build on %s failed: foreign soldiers are there" % k)
    cost = build_cost(world, rules, nid, b)
    if not can_pay(n["resources"], cost):
        return W.ev(world, nid, "Build %s failed: not enough resources (need %s)" % (b, cost))
    pay(n["resources"], cost)
    t["building"] = b
    t.pop("level", None)
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


def do_train(world, rules, n, o):
    nid = n["id"]
    cost = rules["soldier"]["train_cost"]
    room = W.housing(world, rules, nid)[1] - W.soldiers(world, nid)
    count = min(o["n"], room, n["workers"] - 1)
    for r, v in cost.items():
        if v > 0:
            count = min(count, n["resources"][r] // v)
    if count <= 0:
        return W.ev(world, nid, "Training failed: need barracks room, resources and workers")
    for r, v in cost.items():
        n["resources"][r] -= v * count
    n["workers"] -= count
    W.add_army(world, nid, n["capital"], count)
    W.ev(world, nid, "You trained %d soldiers (asked for %d)" % (count, o["n"]))


def do_move(world, rules, n, o):
    nid = n["id"]
    a = next((x for x in world["armies"] if x["id"] == o.get("army") and x["owner"] == nid), None)
    t = world["tiles"].get(o.get("dest"))
    if a is None or t is None or t["terrain"] == "water":
        return W.ev(world, nid, "Move failed: bad army or destination")
    a["dest"] = o["dest"] if o["dest"] != a["tile"] else None


def apply_orders(world, rules, rng):
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
    dn = sum(d["n"] for d in defenders)
    terrain_only = world["tiles"][k]["terrain"]
    if owner is not None and owner != a["owner"]:
        bonus = combat.defense_bonus(world, rules, k)
    else:
        bonus = rules["terrain"][terrain_only]["defense_percent"]
    res = combat.fight(rules, rng, a["n"], dn, bonus)
    a["n"] -= res["att_loss"]
    left = res["def_loss"]
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
            tile["owner"], tile["building"] = a["owner"], None
            W.ev(world, owner, "%s captured your tile %s" % (me["name"], k))
            W.pub(world, "%s captured %s from %s" % (me["name"], k, world["nations"][owner]["name"]))
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
        step = next_step(world, a["tile"], a["dest"])
        if step is None:
            a["dest"] = None
            continue
        attack(world, rules, rng, a, step)
        if a in world["armies"] and a["tile"] == a["dest"]:
            a["dest"] = None
    merged = []
    for a in world["armies"]:
        same = next((m for m in merged if m["owner"] == a["owner"] and m["tile"] == a["tile"]
                     and m["dest"] == a["dest"]), None)
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

def candidates(world, rules, k):
    t = world["tiles"][k]
    res = list(rules["terrain"][t["terrain"]]["produces"])
    if t["oil"]:
        res.append("oil")
    return res


def is_coastal(world, k):
    return any(world["tiles"][x]["terrain"] == "water" for x in neighbors(k) if x in world["tiles"])


def tile_plan(world, rules, n):
    """Where the workers go tonight, tile by tile: {tile: {resource: (workers, amount)}}.
    A tile never holds more than workers_per_tile workers, so that is also its most output a night."""
    nid, prio = n["id"], n["priorities"]
    weights, cands = {}, {}
    for k in W.owned(world, nid):
        if world["tiles"][k]["building"] is not None:
            continue
        c = [r for r in candidates(world, rules, k) if prio[r] > 0]
        if c:
            cands[k] = c
            weights[k] = max(prio[r] for r in c)
    plan = {}
    per_tile = allocate(n["workers"], weights, rules["workers_per_tile"])
    for k, w in per_tile.items():
        if w == 0:
            continue
        split = allocate(w, {r: prio[r] for r in cands[k]}, w)
        for r, cnt in split.items():
            if cnt == 0:
                continue
            amount = cnt * rules["yield_per_worker"][r]
            if r == "food" and is_coastal(world, k):
                amount += cnt * rules["coastal_food_bonus"]
            plan.setdefault(k, {})[r] = (cnt, amount)
    return plan


def work_plan(world, rules, n):
    """Totals for the whole nation: {resource: (workers, amount produced)}."""
    plan = {}
    for per in tile_plan(world, rules, n).values():
        for r, (cnt, amount) in per.items():
            got = plan.get(r, (0, 0))
            plan[r] = (got[0] + cnt, got[1] + amount)
    return plan


def produce(world, rules, n):
    for r, (_, amount) in work_plan(world, rules, n).items():
        n["resources"][r] += amount


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
        grow = min(grow, housing_w - n["workers"], res["food"] // per if per else grow)
        n["workers"] += max(0, grow)
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
            n["alive"] = False
            for k, t in world["tiles"].items():
                if t["owner"] == n["id"]:
                    t["owner"], t["building"] = None, None
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
