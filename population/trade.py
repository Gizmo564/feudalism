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


def water_problem(world, rules, a, b):
    """A sentence if the two nations may not trade because water separates their capitals and the rules want ports."""
    mode = rules["trade"].get("water", 0)
    if not mode:
        return None
    from .tick import crosses_water, find_path
    path = find_path(world, world["nations"][a]["capital"], world["nations"][b]["capital"])
    if path is not None and not crosses_water(world, path):
        return None                                        # a land route: no ports needed

    def has_port(nid):
        return any(t["owner"] == nid and t["building"] == "port" for t in world["tiles"].values())
    if (has_port(a) or has_port(b)) if mode == 1 else (has_port(a) and has_port(b)):
        return None
    return ("water splits your capitals, and trade across it needs %s" %
            ("a port on one side" if mode == 1 else "a port on both sides"))


def speed_between(world, rules, start, dest):
    """Tiles a night goods travel from start to dest. A route that crosses water by ports goes sea_speed_percent as fast."""
    from .tick import crosses_water, find_path
    speed = rules["movement"]["trade_tiles_per_tick"]
    if crosses_water(world, find_path(world, start, dest)):
        speed = max(1, speed * rules["trade"].get("sea_speed_percent", 100) // 100)
    return speed


def need_markets(world, rules, a, b):
    if rules["trade"]["requires_market"]:
        for nid in (a, b):
            if not has_market(world, nid):
                raise ValueError("%s needs a Market to trade" % world["nations"][nid]["name"])
    problem = water_problem(world, rules, a, b)
    if problem:
        raise ValueError(problem)


def offer(world, rules, frm, to, give, get):
    """`frm` gives `give` and asks `to` for `get`. Each is {"resources": {...}, "tiles": [...]}.
    to=None is an open offer: the first nation with a Market that accepts it gets the deal."""
    if frm == to:
        raise ValueError("you cannot trade with yourself")
    for nid in (frm, to):
        if nid is not None and not world["nations"][nid]["alive"]:
            raise ValueError("that nation has been eliminated")
    need_markets(world, rules, frm, frm if to is None else to)
    if not give.get("resources") and not give.get("tiles") and not get.get("resources") and not get.get("tiles"):
        raise ValueError("an offer needs something in it")
    check_side(world, rules, frm, give)
    o = {"id": world["next_trade_id"], "from": frm, "to": to, "give": give, "get": get,
         "tick": world["tick"], "status": "open"}
    world["next_trade_id"] += 1
    world["trades"].append(o)
    if to is not None:
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
    if o["to"] is None:
        if by == o["from"]:
            raise ValueError("that is your own offer")
        if not world["nations"][by]["alive"]:
            raise ValueError("your nation has been eliminated")
        need_markets(world, rules, o["from"], by)
        check_side(world, rules, o["from"], o["give"])
        check_side(world, rules, by, o["get"])
        o["to"] = by
    else:
        if o["to"] != by:
            raise ValueError("that offer is not for you")
        need_markets(world, rules, o["from"], o["to"])
        check_side(world, rules, o["from"], o["give"])
        check_side(world, rules, o["to"], o["get"])
    cap_from, cap_to = world["nations"][o["from"]]["capital"], world["nations"][o["to"]]["capital"]
    speed = speed_between(world, rules, cap_from, cap_to)
    dist = distance(cap_from, cap_to)
    travel = max(1, math.ceil(dist / speed))
    caravans = rules["trade"].get("caravans", False)
    sides = ((o["from"], o["to"], o["give"]), (o["to"], o["from"], o["get"]))
    if caravans and any(side.get("resources") for _, _, side in sides) and not land_route(world, o["from"], o["to"]):
        raise ValueError("there is no land route between the two capitals, so caravans cannot carry the goods")
    for sender, receiver, side in sides:
        res = {r: n for r, n in side.get("resources", {}).items() if n > 0}
        for r, n in res.items():
            world["nations"][sender]["resources"][r] -= n
        if res and caravans:
            send_caravan(world, sender, receiver, res)
        elif res:
            world["shipments"].append({"to": receiver, "res": res, "arrive": world["tick"] + travel - 1})
        for k in side.get("tiles", []):
            world["tiles"][k]["owner"] = receiver
    o["status"] = "accepted"
    how = "A caravan carries the resources (%d tile(s) a tick)." % speed if caravans else "Resources arrive in %d tick(s)." % travel
    W.ev(world, o["from"], "Trade %d accepted. %s" % (tid, how))
    W.ev(world, o["to"], "Trade %d done. %s" % (tid, how))


# ---------- caravans: goods that walk from one capital to the other and can be captured ----------

def land_route(world, a, b):
    from .tick import next_step                       # tick imports this module, so import it late
    start, dest = world["nations"][a]["capital"], world["nations"][b]["capital"]
    return start == dest or next_step(world, start, dest) is not None


def send_caravan(world, sender, receiver, res):
    cid = world.get("next_caravan_id", 1)
    world["next_caravan_id"] = cid + 1
    world.setdefault("caravans", []).append({
        "id": cid, "from": sender, "to": receiver, "res": res,
        "tile": world["nations"][sender]["capital"], "dest": world["nations"][receiver]["capital"]})


def cargo(res):
    return ", ".join("%d %s" % (v, r) for r, v in res.items())


def raider(world, c):
    """The nation of a foreign army standing on the caravan's tile, if any (not either side of the trade)."""
    for a in world["armies"]:
        if a["tile"] == c["tile"] and a["owner"] not in (c["from"], c["to"]) and world["nations"][a["owner"]]["alive"]:
            return a["owner"]
    return None


def capture(world, c, nid):
    for r, v in c["res"].items():
        world["nations"][nid]["resources"][r] += v
    W.ev(world, nid, "Your army captured a caravan at %s carrying %s." % (c["tile"], cargo(c["res"])))
    for side in (c["from"], c["to"]):
        W.ev(world, side, "A caravan carrying %s was captured at %s by %s." % (cargo(c["res"]), c["tile"], world["nations"][nid]["name"]))


def move_caravans(world, rules):
    """Run after the armies have moved. Each caravan walks up to the trade speed along the land path;
    an enemy army on a tile it reaches captures it. Reaching the other capital delivers the goods."""
    from .tick import next_step, find_path, STATIONS
    on_rails = lambda k: world["tiles"][k]["building"] in STATIONS        # rails of any nation: lines of different nations connect
    left = []
    for c in world.get("caravans", []):
        speed = speed_between(world, rules, c["tile"], c["dest"])
        nid = raider(world, c)
        steps, rail_left = 0, rules["movement"].get("rail_steps", 0)
        while nid is None and c["tile"] != c["dest"] and steps < speed:
            by_rail = find_path(world, c["tile"], c["dest"], on_rails) if rail_left > 0 else None
            step = by_rail[0] if by_rail else next_step(world, c["tile"], c["dest"])      # a connected rail line is a train ride
            if step is None:
                break
            c["tile"] = step
            if by_rail:
                rail_left -= 1                  # train steps are free; the rest of the trip is the usual caravan walk
            else:
                steps += 1
            nid = raider(world, c)
        if nid is not None:
            capture(world, c, nid)
        elif c["tile"] == c["dest"]:
            n = world["nations"][c["to"]]
            if n["alive"]:
                for r, v in c["res"].items():
                    n["resources"][r] += v
                W.ev(world, c["to"], "A caravan arrived carrying %s." % cargo(c["res"]))
            W.ev(world, c["from"], "Your caravan reached %s." % n["name"])
        elif steps == 0:                              # no way forward: hand the goods back
            n = world["nations"][c["from"]]
            for r, v in c["res"].items():
                n["resources"][r] += v
            W.ev(world, c["from"], "Your caravan could not reach its goal and came home with %s." % cargo(c["res"]))
        else:
            left.append(c)
    world["caravans"] = left


def decline(world, tid, by):
    o = find(world, tid)
    if o is None or o["status"] != "open" or by not in (o["from"], o["to"]):
        raise ValueError("that offer is not open")
    o["status"] = "withdrawn" if by == o["from"] else "declined"


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
