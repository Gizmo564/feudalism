"""Trades between nations. Land and buildings move at once; resources travel and arrive later."""
import math

from .hexes import distance
from . import world as W


def has_market(world, nid):
    return any(t["owner"] == nid and t["building"] == "market" for t in world["tiles"].values())


def check_side(world, rules, nid, side):
    for r, n in side.get("resources", {}).items():
        if r not in rules["resources"] or not isinstance(n, int) or n < 0:
            raise ValueError("bad amount of %s" % r)
        if world["nations"][nid]["resources"][r] < n:
            raise ValueError("%s does not have %d %s" % (world["nations"][nid]["name"], n, r))
    for k in side.get("tiles", []):
        t = world["tiles"].get(k)
        if t is None or t["owner"] != nid:
            raise ValueError("%s does not own tile %s" % (world["nations"][nid]["name"], k))
        if t["building"] == "capital":
            raise ValueError("a capital cannot be traded")
        if any(a["tile"] == k and a["owner"] == nid for a in world["armies"]):
            raise ValueError("tile %s has soldiers on it" % k)


def need_markets(world, rules, a, b):
    if rules["trade"]["requires_market"]:
        for nid in (a, b):
            if not has_market(world, nid):
                raise ValueError("%s needs a Market to trade" % world["nations"][nid]["name"])


def offer(world, rules, frm, to, give, get):
    """`frm` gives `give` and asks `to` for `get`. Each is {"resources": {...}, "tiles": [...]}."""
    if frm == to:
        raise ValueError("you cannot trade with yourself")
    for nid in (frm, to):
        if not world["nations"][nid]["alive"]:
            raise ValueError("that nation has been eliminated")
    need_markets(world, rules, frm, to)
    check_side(world, rules, frm, give)
    o = {"id": world["next_trade_id"], "from": frm, "to": to, "give": give, "get": get,
         "tick": world["tick"], "status": "open"}
    world["next_trade_id"] += 1
    world["trades"].append(o)
    W.ev(world, to, "%s offered you a trade (offer %d)" % (world["nations"][frm]["name"], o["id"]))
    return o["id"]


def find(world, tid):
    for o in world["trades"]:
        if o["id"] == tid:
            return o
    return None


def accept(world, rules, tid, by):
    o = find(world, tid)
    if o is None or o["status"] != "open":
        raise ValueError("that offer is not open")
    if o["to"] != by:
        raise ValueError("that offer is not for you")
    need_markets(world, rules, o["from"], o["to"])
    check_side(world, rules, o["from"], o["give"])
    check_side(world, rules, o["to"], o["get"])
    speed = rules["movement"]["trade_tiles_per_tick"]
    dist = distance(world["nations"][o["from"]]["capital"], world["nations"][o["to"]]["capital"])
    travel = max(1, math.ceil(dist / speed))
    for sender, receiver, side in ((o["from"], o["to"], o["give"]), (o["to"], o["from"], o["get"])):
        res = {r: n for r, n in side.get("resources", {}).items() if n > 0}
        for r, n in res.items():
            world["nations"][sender]["resources"][r] -= n
        if res:
            world["shipments"].append({"to": receiver, "res": res, "arrive": world["tick"] + travel - 1})
        for k in side.get("tiles", []):
            world["tiles"][k]["owner"] = receiver
    o["status"] = "accepted"
    W.ev(world, o["from"], "Trade %d accepted. Resources arrive in %d tick(s)." % (tid, travel))
    W.ev(world, o["to"], "Trade %d done. Resources arrive in %d tick(s)." % (tid, travel))


def decline(world, tid, by):
    o = find(world, tid)
    if o is None or o["status"] != "open" or by not in (o["from"], o["to"]):
        raise ValueError("that offer is not open")
    o["status"] = "declined"


def deliver(world):
    left = []
    for s in world["shipments"]:
        if s["arrive"] > world["tick"]:
            left.append(s)
            continue
        n = world["nations"][s["to"]]
        if n["alive"]:
            for r, v in s["res"].items():
                n["resources"][r] += v
            W.ev(world, s["to"], "A shipment arrived: " + ", ".join("%d %s" % (v, r) for r, v in s["res"].items()))
    world["shipments"] = left


def expire(world, rules):
    for o in world["trades"]:
        if o["status"] == "open" and world["tick"] - o["tick"] >= rules["trade"]["offer_expires_ticks"]:
            o["status"] = "expired"
