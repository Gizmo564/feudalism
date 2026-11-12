"""What a player is allowed to see. With fog off, land is public. With fog on, only land near you."""
from .hexes import within
from . import world as W, victory


def visible_keys(world, rules, nid):
    radius = rules["vision_radius"]
    seen = set()
    for k in W.owned(world, nid):
        seen.update(within(k, radius))
    for a in world["armies"]:
        if a["owner"] == nid:
            seen.update(within(a["tile"], radius))
    return seen & set(world["tiles"])


def map_view(world, rules, nid):
    """One entry per tile. Terrain is always shown; owner and building only when visible."""
    fog = world["settings"]["fog"]
    vis = visible_keys(world, rules, nid)
    out = []
    for k, t in sorted(world["tiles"].items()):
        shown = (not fog) or k in vis
        out.append({"k": k, "terrain": t["terrain"], "oil": shown and W.oil_shown(world, t), "visible": shown,
                    "owner": t["owner"] if shown else None,
                    "building": t["building"] if shown else None})
    return out


def armies_view(world, rules, nid):
    """Your own armies always; other nations' armies only on tiles you can see."""
    vis = visible_keys(world, rules, nid)
    return [a for a in world["armies"] if a["owner"] == nid or a["tile"] in vis]


def caravans_view(world, rules, nid):
    """Your own caravans (with their cargo), and other nations' caravans only on tiles you can see (no cargo)."""
    vis = visible_keys(world, rules, nid)
    out = []
    for c in world.get("caravans", []):
        if nid in (c["from"], c["to"]):
            out.append(dict(c))
        elif c["tile"] in vis:
            out.append({k: v for k, v in c.items() if k != "res"})
    return out


def public_standings(world, rules, nid):
    """Land and population for every nation with fog off; just your own with fog on."""
    rows = []
    for n in world["nations"]:
        if world["settings"]["fog"] and n["id"] != nid:
            continue
        rows.append({"nation": n["id"], "name": n["name"], "alive": n["alive"],
                     "rank": rank_title(world, rules, n["id"]),
                     "land": len(W.owned(world, n["id"])), "population": W.people(world, n["id"])})
    return rows


def rank_title(world, rules, nid):
    """Title earned by the share of the world's land a nation holds. Cosmetic for now."""
    if "ranks" not in rules:
        return None
    land = sum(1 for t in world["tiles"].values() if t["terrain"] != "water") or 1
    share = 100 * len(W.owned(world, nid)) // land
    title = rules["ranks"][0]["title"]
    for r in rules["ranks"]:
        need = victory.land_threshold(world, rules) if r.get("empire") else r["min_land_percent"]
        if need is not None and share >= need:
            title = r["title"]
    return title
