"""Plain-language help for a player: what needs attention, how close anyone is to winning, and trends.
No Discord code here."""
import math

from . import tick, victory, world as W
from .hexes import neighbors

HISTORY_LENGTH = 12
LOW_FOOD_NIGHTS = 5


def committed(world, rules, nid):
    """What the player's queued claims and builds will cost tonight (orders run before production)."""
    cost = {}

    def add(c):
        for r, v in c.items():
            cost[r] = cost.get(r, 0) + v

    claims = 0
    for o in world["orders"]:
        if o["nation"] != nid:
            continue
        if o["type"] == "claim" and claims < rules["claim"]["per_tick"]:
            add(tick.claim_cost(world, rules, nid))
            claims += 1
        elif o["type"] == "build" and o.get("building") in rules["buildings"]:
            add(rules["buildings"][o["building"]]["cost"])
    return cost


def available(world, rules, nid):
    """Stock minus what queued orders already spend."""
    res = dict(world["nations"][nid]["resources"])
    for r, v in committed(world, rules, nid).items():
        res[r] = res.get(r, 0) - v
    return res


def claim_queued(world, nid):
    return sum(1 for o in world["orders"] if o["nation"] == nid and o["type"] == "claim")


def shortfall(res, cost):
    """The part of a cost you can't pay: {resource: missing amount}."""
    return {r: v - res.get(r, 0) for r, v in cost.items() if res.get(r, 0) < v}


def food_balance(world, rules, nid):
    """(net food per night, nights the stock lasts or None if it isn't running down)."""
    n = world["nations"][nid]
    plan = tick.work_plan(world, rules, n)
    need = W.people(world, nid) * rules["eat_per_person"]
    net = plan.get("food", (0, 0))[1] - need
    if net >= 0:
        return net, None
    value = rules["food_value"].get("livestock", 1)
    stock = n["resources"].get("food", 0) + n["resources"].get("livestock", 0) * value
    return net, stock // -net


def free_frontier(world, nid):
    return {m for k in W.owned(world, nid) for m in neighbors(k) if W.is_free_land(world, m)}


def abroad(world, nid):
    return sum(a["n"] for a in world["armies"]
               if a["owner"] == nid and world["tiles"][a["tile"]]["owner"] != nid)


def oil_needed(world, rules, nid):
    return math.ceil(abroad(world, nid) / rules["oil_upkeep"]["soldiers_per_oil"])


def fmt(res):
    return ", ".join("%d %s" % (v, k) for k, v in res.items() if v) or "nothing"


def advice(world, rules, nid, limit=3):
    """Short, ordered lines about what needs attention right now (most urgent first)."""
    n = world["nations"][nid]
    words = rules.get("terms", {})
    worker, soldier = words.get("worker", "worker"), words.get("soldier", "soldier")
    out = []
    net, nights = food_balance(world, rules, nid)
    if nights is not None and nights < LOW_FOOD_NIGHTS:
        out.append("Food runs out in about %d night%s. Raise your food priority or claim fields." % (
            nights, "" if nights == 1 else "s"))
    needed = oil_needed(world, rules, nid)
    if needed and n["resources"].get("oil", 0) < needed:
        out.append("Your armies abroad need %d oil tonight and you have %d. They will lose soldiers." % (
            needed, n["resources"].get("oil", 0)))
    hw, hs = W.housing(world, rules, nid)
    if n["workers"] >= hw:
        out.append("Homes are full: new %ss can't be born. Build a home." % worker)
    plan = tick.work_plan(world, rules, n)
    idle = n["workers"] - sum(c for c, _ in plan.values())
    if idle > 0:
        out.append("%d %ss have nothing to do. Raise a work priority (or claim land)." % (idle, worker))
    cost = tick.claim_cost(world, rules, nid)
    if (not claim_queued(world, nid) and free_frontier(world, nid)
            and not shortfall(available(world, rules, nid), cost)):
        out.append("You can afford a claim (%s). Open /zoom and pick an outlined tile." % fmt(cost))
    if W.soldiers(world, nid) > hs:
        out.append("More %ss than barracks room: some will leave. Build barracks." % soldier)
    return out[:limit] or ["All quiet. Nothing urgent."]


def race(world, rules, nid=None):
    """Lines about the win condition: what is needed, who leads, and (with nid) where you stand."""
    v = rules["victory"]
    sh = victory.shares(world, rules)
    need = {"land": victory.land_threshold(world, rules), "population": v["population_share_percent"],
            "stock": v["stock_share_percent"]}
    lines = []
    for metric, pct in need.items():
        if pct is None or not sh:
            continue
        lead = max(sh, key=lambda i: sh[i][metric])
        text = "To win: %d%% of all %s for %d nights." % (pct, metric, v["hold_ticks"])
        if nid is not None:
            text += " You: %d%%." % sh.get(nid, {}).get(metric, 0)
        if lead == nid:
            text += " You lead."
        else:
            text += " Leader: %s %d%%." % (world["nations"][lead]["name"], sh[lead][metric])
        held = world["nations"][nid]["hold"].get(metric, 0) if nid is not None and world["nations"][nid]["alive"] else 0
        if held:
            text += " Held %d/%d nights." % (held, v["hold_ticks"])
        if metric == "land" and v.get("land_share_decay_per_tick"):
            start = v.get("land_share_decay_start", 0)
            floor = v.get("land_share_floor_multiple", 1) * 100 // max(1, len(world["nations"]))
            if world["tick"] < start:
                text += " The bar starts falling after night %d." % start
            elif pct > floor:
                text += " The bar is falling %d%% a night (down to %d%%)." % (v["land_share_decay_per_tick"], floor)
        lines.append(text)
    return lines


# ---------- trends ----------

def record(world, rules):
    """Save one line of history per living nation. Called after each night."""
    hist = world.setdefault("history", {})
    for n in world["nations"]:
        if not n["alive"]:
            continue
        net, _ = food_balance(world, rules, n["id"])
        rows = hist.setdefault(str(n["id"]), [])
        rows.append({"t": world["tick"], "pop": W.people(world, n["id"]), "land": len(W.owned(world, n["id"])),
                     "food_net": net})
        del rows[:-HISTORY_LENGTH]


def history(world, nid):
    return world.get("history", {}).get(str(nid), [])


def sparkline(values):
    bars = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return bars[3] * len(values)
    return "".join(bars[round((v - lo) * 7 / (hi - lo))] for v in values)
