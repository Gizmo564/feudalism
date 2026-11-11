"""Simple scripted players, used only to test balance. Each bot has a personality (aggression 0-1)."""
from . import world as W, tick
from .hexes import neighbors, distance


def frontier(world, nid):
    """Free land tiles that touch the nation's land."""
    out = set()
    for k in W.owned(world, nid):
        for n in neighbors(k):
            if W.is_free_land(world, n):
                out.add(n)
    return sorted(out)


def enemy_targets(world, nid):
    """Enemy tiles next to our land."""
    out = []
    for k in W.owned(world, nid):
        for n in neighbors(k):
            t = world["tiles"].get(n)
            if t and t["owner"] is not None and t["owner"] != nid and t["terrain"] != "water":
                out.append(n)
    return sorted(set(out))


def act(world, rules, nid, aggression, rng):
    me = world["nations"][nid]
    res = me["resources"]
    housing_w, housing_s = W.housing(world, rules, nid)
    # 1. homes when workers are near the housing limit
    if me["workers"] >= housing_w - 2:
        for k in W.owned(world, nid):
            t = world["tiles"][k]
            if t["building"] is None and tick.can_pay(res, rules["buildings"]["home"]["cost"]):
                tick.queue(world, rules, nid, {"type": "build", "tile": k, "building": "home"})
                return_early = True
                break
    # 2. barracks for aggressive bots
    if aggression > 0.3 and W.soldiers(world, nid) >= housing_s - 1:
        for k in W.owned(world, nid):
            t = world["tiles"][k]
            if t["building"] is None and tick.can_pay(res, rules["buildings"]["barracks"]["cost"]):
                tick.queue(world, rules, nid, {"type": "build", "tile": k, "building": "barracks"})
                break
    # 3. claim the frontier tile closest to our capital
    front = frontier(world, nid)
    if front and tick.can_pay(res, tick.claim_cost(world, rules, nid)):
        front.sort(key=lambda k: (distance(k, me["capital"]), k))
        tick.queue(world, rules, nid, {"type": "claim", "tile": front[0]})
    # 4. train soldiers
    if aggression > 0.3 and W.soldiers(world, nid) < housing_s:
        tick.queue(world, rules, nid, {"type": "train", "n": 3})
    # 5. attack the weakest visible neighbour
    if aggression > 0.0 and rng.random() < aggression:
        targets = enemy_targets(world, nid)
        armies = [a for a in world["armies"] if a["owner"] == nid and a["n"] >= 8]
        if targets and armies:
            army = max(armies, key=lambda a: a["n"])
            weakest = min(targets, key=lambda k: (sum(d["n"] for d in world["armies"] if d["tile"] == k), k))
            tick.queue(world, rules, nid, {"type": "move", "army": army["id"], "dest": weakest})
    # 6. bring armies home if they wandered off and have nowhere to go
    for a in world["armies"]:
        if a["owner"] == nid and a["dest"] is None and world["tiles"][a["tile"]]["owner"] != nid and rng.random() < 0.5:
            tick.queue(world, rules, nid, {"type": "move", "army": a["id"], "dest": me["capital"]})
