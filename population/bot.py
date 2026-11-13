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


def sites(world, nid):
    """Owned tiles to build on, starting with the most common terrain so homes and workshops don't cover the rare kinds."""
    own = W.owned(world, nid)
    kinds = [world["tiles"][k]["terrain"] for k in own]
    return sorted(own, key=lambda k: (-kinds.count(world["tiles"][k]["terrain"]), k))


def enemy_targets(world, nid):
    """Enemy tiles next to our land."""
    out = []
    for k in W.owned(world, nid):
        for n in neighbors(k):
            t = world["tiles"].get(n)
            if t and t["owner"] is not None and t["owner"] != nid and t["terrain"] != "water":
                out.append(n)
    return sorted(set(out))


CHAIN = ["toolworks", "kiln", "cokeworks", "forge", "sawmill", "factory"]   # toolworks first: the factory costs tools


RAW_TARGETS = {"wood": 80, "stone": 80, "ore": 30, "coal": 60, "food": 120}


def build_industry(world, rules, nid):
    """Work wood and stone first (every claim and workshop costs them), then build the next missing workshop in CHAIN, one at a time."""
    me = world["nations"][nid]
    me["priorities"]["livestock"] = 0                   # cattle share tiles with stone and coal, and nothing here needs them
    for r, target in RAW_TARGETS.items():               # work what is short, ease off what is piled up
        have = me["resources"][r]
        me["priorities"][r] = 5 if have < target / 2 else 2 if have < target else 0
    for good in rules["refine"]:
        me["priorities"][good] = 4 if good in ("bricks", "coke", "steel", "planks", "machine_parts") else 0
    kinds = [world["tiles"][k]["terrain"] for k in W.owned(world, nid)]
    if min(kinds.count(t) for t in ("forest", "hills", "mountain")) < 1:
        return                                          # no wood, stone or ore and coal income yet: keep claiming
    have = {world["tiles"][k]["building"] for k in W.owned(world, nid)}
    queued = {o.get("building") for o in world["orders"] if o["nation"] == nid and o["type"] == "build"}
    for b in CHAIN:
        if b in have:
            continue
        cost = rules["buildings"][b]["cost"]
        for r in cost:                                  # work hardest on what the next workshop still lacks
            if me["resources"][r] < cost[r]:
                me["priorities"][r] = 5
        if b not in queued:
            for k in sites(world, nid):
                if world["tiles"][k]["building"] is None and tick.can_pay(me["resources"], cost):
                    tick.queue(world, rules, nid, {"type": "build", "tile": k, "building": b})
                    break
        break


def act(world, rules, nid, aggression, rng, industrial=False):
    me = world["nations"][nid]
    res = me["resources"]
    housing_w, housing_s = W.housing(world, rules, nid)
    # 1. homes when workers are near the housing limit
    if me["workers"] >= housing_w - 2:
        for k in sites(world, nid):
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
    # 2b. industrial bots (simulate.py --industry) build the chain toward factories and work it
    if industrial and rules.get("refine"):
        build_industry(world, rules, nid)
    # 3. claim the frontier tile closest to our capital
    front = frontier(world, nid)
    if industrial and len(W.owned(world, nid)) >= 24:
        front = []                                      # enough land: save wood and stone for the chain
    if front and tick.can_pay(res, tick.claim_cost(world, rules, nid)):
        kinds = [world["tiles"][k]["terrain"] for k in W.owned(world, nid)]
        want = next((t for t in ("forest", "hills", "mountain") if kinds.count(t) < 2), None) if industrial else None   # the chain needs wood, stone, ore and coal
        goal = [k for k, t in world["tiles"].items() if t["terrain"] == want and t["owner"] is None] if want else []   # bots can see the whole map
        front.sort(key=lambda k: (world["tiles"][k]["terrain"] != want, min([distance(k, g) for g in goal] or [0]), distance(k, me["capital"]), k))
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
