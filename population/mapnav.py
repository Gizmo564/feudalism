"""Logic behind the interactive map: panning, which tiles to offer, and what you can do on a tile.
No Discord code here, so it can be tested on its own."""
from . import views, world as W
from .hexes import parse, neighbors, distance, within
from .tick import claim_cost, foreign_army_on

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
    """What a tile is and what you can do there: {"text", "can_claim", "builds", "can_move"}."""
    info = {v["k"]: v for v in views.map_view(world, rules, nid)}
    v = info.get(k)
    if v is None:
        return {"text": "No such tile.", "can_claim": False, "builds": [], "can_move": False}
    if not v["visible"]:
        return {"text": "**%s** - fogged, you can't see it." % k, "can_claim": False, "builds": [], "can_move": False}
    lines = ["**%s** - %s" % (k, v["terrain"])]
    if v["oil"]:
        lines[0] += ", oil deposit"
    if v["owner"] is None:
        lines.append("Free land." if v["terrain"] != "water" else "Water - nobody can claim it.")
    else:
        lines.append("Owned by %s%s." % (world["nations"][v["owner"]]["name"], " (you)" if v["owner"] == nid else ""))
    if v["building"]:
        lines.append("Building: %s." % v["building"])
    can_claim = k in claimable(world, nid)
    if can_claim:
        cost = claim_cost(world, rules, nid)
        lines.append("Claim cost: " + ", ".join("%d %s" % (n, r) for r, n in cost.items() if n) + ".")
    builds = []
    if v["owner"] == nid and v["building"] is None and not foreign_army_on(world, k, nid):
        builds = list(rules["buildings"])
        lines.append("Build: " + "; ".join("%s (%s)" % (b, ", ".join("%d %s" % (n, r) for r, n in rules["buildings"][b]["cost"].items()))
                                            for b in builds) + ".")
    return {"text": "\n".join(lines), "can_claim": can_claim, "builds": builds, "can_move": v["terrain"] != "water"}


def my_armies(world, nid):
    return [a for a in world["armies"] if a["owner"] == nid]
