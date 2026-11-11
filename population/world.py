"""The game state: a plain dict that can be saved as JSON. Also map creation and expansion."""
import json
import random

from .hexes import neighbors, distance, within, ring


def ev(world, nid, text):
    """Private message for one nation, shown in its next report."""
    world["events"].setdefault(str(nid), []).append(text)


def pub(world, text):
    """Public line for the nightly report."""
    world["events"].setdefault("public", []).append(text)


# ---------- reading the state ----------

def owned(world, nid):
    return sorted(k for k, t in world["tiles"].items() if t["owner"] == nid)


def soldiers(world, nid):
    return sum(a["n"] for a in world["armies"] if a["owner"] == nid)


def people(world, nid):
    return world["nations"][nid]["workers"] + soldiers(world, nid)


def stock(nation):
    return sum(nation["resources"].values())


def housing(world, rules, nid):
    """(room for workers, room for soldiers) from the nation's buildings."""
    w = s = 0
    for t in world["tiles"].values():
        if t["owner"] != nid or t["building"] is None:
            continue
        spec = rules["capital"] if t["building"] == "capital" else rules["buildings"][t["building"]]
        w += spec.get("houses_workers", 0)
        s += spec.get("houses_soldiers", 0)
    return w, s


def discover_oil(world):
    """Any oil deposit on owned land counts as discovered from then on."""
    for t in world["tiles"].values():
        if t["oil"] and t["owner"] is not None:
            t["found"] = True


def oil_shown(world, t):
    """Whether players can see this tile's oil deposit on the map."""
    return t["oil"] and (not world["settings"].get("hidden_oil") or t.get("found", False))


def is_free_land(world, k):
    t = world["tiles"].get(k)
    return t is not None and t["terrain"] != "water" and t["owner"] is None


def add_army(world, nid, tile, n, dest=None):
    if dest is None:
        for a in world["armies"]:
            if a["owner"] == nid and a["tile"] == tile and a["dest"] is None:
                a["n"] += n
                return a
    a = {"id": world["next_army_id"], "owner": nid, "tile": tile, "n": n, "dest": dest}
    world["next_army_id"] += 1
    world["armies"].append(a)
    return a


# ---------- making the map ----------

def pick_terrain(rules, rng):
    weights = rules["terrain_weights"]
    x = rng.randrange(sum(weights.values()))
    for name, w in weights.items():
        if x < w:
            return name
        x -= w


def make_tiles(rules, rng, keys, tiles):
    """Create tiles for `keys`. Scatter some seed points, give each a terrain, and let every tile copy its
    nearest seed, so terrain forms patches (lakes, forests, ranges). Tiles that already exist act as seeds too,
    so a map expansion continues the patches instead of starting over. Hills then soften the edge of mountains."""
    keys = sorted(keys)
    seeds = [(k, tiles[k]["terrain"]) for k in tiles]
    for k in keys:
        if rng.random() < 0.12:
            seeds.append((k, pick_terrain(rules, rng)))
    if not seeds:
        seeds.append((keys[0], pick_terrain(rules, rng)))
    terrain = {}
    for k in keys:
        terrain[k] = min(seeds, key=lambda s: distance(k, s[0]) + rng.random() * 0.9)[1]
    for k in keys:  # a field next to a mountain becomes hills half the time
        if terrain[k] == "field" and any(terrain.get(n) == "mountain" for n in neighbors(k)) and rng.random() < 0.5:
            terrain[k] = "hills"
    for k in keys:
        land = terrain[k] != "water"
        oil = land and rng.randrange(rules["oil_deposit_one_in"]) == 0
        tiles[k] = {"terrain": terrain[k], "oil": oil, "owner": None, "building": None}


def new_world(rules, names, radius, seed):
    rng = random.Random(seed)
    world = {
        "ruleset": rules["name"], "ruleset_version": rules["version"],
        "seed": seed, "tick": 0, "radius": radius,
        "settings": {"fog": rules["fog_of_war"], "hidden_oil": rules.get("hidden_oil", True)},
        "tiles": {}, "nations": [], "armies": [], "orders": [],
        "trades": [], "shipments": [], "events": {}, "winner": None,
        "next_army_id": 1, "next_trade_id": 1,
    }
    make_tiles(rules, rng, within("0,0", radius), world["tiles"])
    for name in names:
        place_nation(world, rules, name, rng, list(world["tiles"]), dict(rules["start"]["resources"]))
    discover_oil(world)
    return world


def good_start(world, start, k, strict=True, min_land=None):
    t = world["tiles"].get(k)
    if t is None or t["terrain"] == "water" or t["owner"] is not None:
        return False
    need = start["min_land_neighbors"] if min_land is None else min_land
    if sum(1 for n in neighbors(k) if is_free_land(world, n)) < need:
        return False
    if not strict:
        return True
    near = [world["tiles"][x]["terrain"] for x in within(k, 2) if x in world["tiles"]]
    return (near.count("field") >= start["min_fields_near"]
            and near.count("forest") + near.count("hills") >= start["min_wood_stone_near"])


def found_nation(world, rules, name, k, resources, protected_until=0):
    nid = len(world["nations"])
    world["nations"].append({
        "id": nid, "name": name, "alive": True, "capital": k,
        "workers": rules["start"]["workers"], "resources": resources,
        "priorities": dict(rules["default_priorities"]),
        "idle": 0, "active": False, "claims_this_tick": 0,
        "hold": {"land": 0, "population": 0, "stock": 0},
        "protected_until": protected_until,
    })
    cap = world["tiles"][k]
    cap.update(terrain="field", oil=False, owner=nid, building="capital")
    ring_tiles = [n for n in neighbors(k) if is_free_land(world, n)]
    fields = [n for n in ring_tiles if world["tiles"][n]["terrain"] == "field"]
    for n in ring_tiles:
        if len(fields) >= rules["start"]["start_fields"]:
            break
        if n not in fields:
            world["tiles"][n]["terrain"] = "field"
            fields.append(n)
    for n in ring_tiles:
        world["tiles"][n]["owner"] = nid
    add_army(world, nid, k, rules["start"]["soldiers"])
    return nid


def place_nation(world, rules, name, rng, candidates, resources, protected_until=0):
    """Pick a random fair start. If the map is crowded, the rules are relaxed step by step:
    first the terrain checks, then the number of land neighbours, then the spacing."""
    start = rules["start"]
    capitals = [n["capital"] for n in world["nations"]]
    cands = sorted(candidates)
    rng.shuffle(cands)
    for spacing in range(start["min_spacing"], 1, -1):
        for min_land in range(start["min_land_neighbors"], 3, -1):
            for strict in (True, False):
                for k in cands:
                    if good_start(world, start, k, strict, min_land) and all(distance(k, c) >= spacing for c in capitals):
                        return found_nation(world, rules, name, k, resources, protected_until)
    raise ValueError("no room for another nation - expand the map first")


# ---------- admin changes while a game is running ----------

def expand_map(world, rules, rings=1):
    """Add outer rings of new land. Existing tiles never change."""
    for _ in range(rings):
        world["radius"] += 1
        rng = random.Random(world["seed"] * 7919 + world["radius"])
        make_tiles(rules, rng, ring(world["radius"]), world["tiles"])


def add_nation(world, rules, name):
    """Late joiner: placed on the outer rings, catch-up resources, a few ticks of protection."""
    rng = random.Random(world["seed"] * 31 + len(world["nations"]))
    outer = [k for k in world["tiles"] if distance("0,0", k) >= world["radius"] - 1]
    start = rules["start"]
    res = dict(start["resources"])
    alive = [n for n in world["nations"] if n["alive"]]
    if alive:
        for r in res:
            avg = sum(n["resources"][r] for n in alive) // len(alive)
            res[r] = max(res[r], avg * start["late_join_catchup_percent"] // 100)
    until = world["tick"] + start["late_join_protection_ticks"]
    nid = place_nation(world, rules, name, rng, outer, res, until)
    discover_oil(world)
    return nid


# ---------- saving ----------

def to_json(world):
    return json.dumps(world, sort_keys=True)


def from_json(text):
    return json.loads(text)
