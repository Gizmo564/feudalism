"""Logic behind the interactive map: panning, which tiles to offer, and what you can do on a tile.
No Discord code here, so it can be tested on its own."""
from . import advice, trade, views, world as W
from .hexes import parse, neighbors, distance, within
from .tick import build_cost, claim_cost, foreign_army_on, upgrade_of

DIRS = {"nw": (0, -1), "ne": (1, -1), "w": (-1, 0), "e": (1, 0), "sw": (-1, 1), "se": (0, 1)}


def shift(world, center, direction, steps=2):
    """Move the view up to `steps` tiles in a direction, stopping at the map edge."""
    dq, dr = DIRS[direction]
    q, r = parse(center)
    for n in range(steps, 0, -1):
        k = "%d,%d" % (q + dq * n, r + dr * n)
        if k in world["tiles"]:
            return k
    return center


def claimable(world, nid):
    return {n for k in W.owned(world, nid) for n in neighbors(k) if W.is_free_land(world, n)}


def pick_options(world, rules, nid, center, radius, limit=25):
    """Tiles in view for the dropdown: claimable first, then your own, then the rest. (key, description)."""
    info = {v["k"]: v for v in views.map_view(world, rules, nid)}
    can = claimable(world, nid)
    rows = []
    for k in within(center, radius):
        v = info.get(k)
        if v is None or v["terrain"] == "water":
            continue
        if k in can:
            rank, text = 0, "free - you can claim it"
        elif v["owner"] == nid:
            rank, text = 1, "yours" + (" - " + v["building"] if v["building"] else " - empty")
        elif not v["visible"]:
            rank, text = 3, "unknown"
        elif v["owner"] is None:
            rank, text = 2, "free land"
        else:
            rank, text = 2, "owned by " + world["nations"][v["owner"]]["name"]
        rows.append((rank, distance(center, k), k, "%s, %s" % (v["terrain"], text)))
    rows.sort()
    return [(k, text) for _, _, k, text in rows[:limit]]


def tile_card(world, rules, nid, k):
    """What a tile is and what you can do there: {"text", "can_claim", "builds", "can_move", "claim_cost",
    "claim_missing", "claim_queued", "build_cost", "build_missing"}. "missing" is what you can't pay yet."""
    info = {v["k"]: v for v in views.map_view(world, rules, nid)}
    v = info.get(k)
    if v is None:
        return {"text": "No such tile.", "can_claim": False, "builds": [], "can_move": False,
                "claim_cost": {}, "claim_missing": {}, "claim_queued": False, "build_cost": {}, "build_missing": {}, "upgrade": None}
    if not v["visible"]:
        return {"text": "**%s** - fogged, you can't see it." % k, "can_claim": False, "builds": [], "can_move": False,
                "claim_cost": {}, "claim_missing": {}, "claim_queued": False, "build_cost": {}, "build_missing": {}, "upgrade": None}
    lines = ["**%s** - %s" % (k, v["terrain"])]
    if v["oil"]:
        lines[0] += ", oil deposit"
    if v["owner"] is None:
        lines.append("Free land." if v["terrain"] != "water" else "Water - nobody can claim it.")
    else:
        lines.append("Owned by %s%s." % (world["nations"][v["owner"]]["name"], " (you)" if v["owner"] == nid else ""))
    if v["building"]:
        lines.append("Building: %s." % v["building"])
    for c in views.caravans_view(world, rules, nid):
        if c["tile"] == k:
            names = world["nations"]
            lines.append("Caravan: %s to %s%s." % (names[c["from"]]["name"], names[c["to"]]["name"],
                                                    ", carrying " + trade.cargo(c["res"]) if "res" in c else ""))
    can_claim = k in claimable(world, nid)
    have = advice.available(world, rules, nid)
    cost = claim_cost(world, rules, nid)
    if can_claim:
        lines.append("Claim cost: " + advice.fmt(cost) + ".")
    builds = []
    queued = {}
    for o in world["orders"]:
        if o["nation"] == nid and o["type"] == "build":
            queued[o["building"]] = queued.get(o["building"], 0) + 1
    prices = {b: build_cost(world, rules, nid, b, queued.get(b, 0)) for b in rules["buildings"]}
    if v["owner"] == nid and v["building"] is None and not foreign_army_on(world, k, nid):
        builds = list(rules["buildings"])
        lines.append("Build: " + "; ".join("%s (%s)" % (b, ", ".join("%d %s" % (n, r) for r, n in prices[b].items()))
                                            for b in builds) + ".")
    upgrade = None
    up = upgrade_of(rules, world["tiles"][k]) if v["owner"] == nid and not foreign_army_on(world, k, nid) else None
    if up:
        level = W.level_of(world["tiles"][k])
        queued_up = any(o["nation"] == nid and o["type"] == "upgrade" and o.get("tile") == k for o in world["orders"])
        upgrade = {"level": level + 1, "cost": up["cost"], "missing": advice.shortfall(have, up["cost"]), "queued": queued_up,
                   "gives": {key: val for key, val in up.items() if key != "cost"}}
        lines.append("Upgrade to level %d: %s." % (level + 1, ", ".join("%d %s" % (n, r) for r, n in up["cost"].items())))
    return {"text": "\n".join(lines), "can_claim": can_claim, "builds": builds, "can_move": v["terrain"] != "water",
            "claim_cost": cost, "claim_missing": advice.shortfall(have, cost),
            "claim_queued": advice.claim_queued(world, nid) >= rules["claim"]["per_tick"],
            "build_cost": {b: prices[b] for b in builds},
            "build_missing": {b: advice.shortfall(have, prices[b]) for b in builds},
            "upgrade": upgrade}


def my_armies(world, nid):
    return [a for a in world["armies"] if a["owner"] == nid]
