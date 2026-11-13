"""The game state: a plain dict that can be saved as JSON. Also map creation and expansion."""
import json
import math
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


def level_of(tile):
    """A building's level: 1 when built, up to the number of upgrades it has plus one."""
    return tile.get("level", 1)


def spec_at(spec, level):
    """A building's numbers at a level. Each upgrade replaces the housing numbers (its own cost is not part of the spec)."""
    out = dict(spec)
    if level > 1:
        out.update({k: v for k, v in spec["upgrades"][level - 2].items() if k != "cost"})
    return out


def housing(world, rules, nid):
    """(room for workers, room for soldiers) from the nation's buildings."""
    w = s = 0
    for t in world["tiles"].values():
        if t["owner"] != nid or t["building"] is None:
            continue
        spec = rules["capital"] if t["building"] == "capital" else spec_at(rules["buildings"][t["building"]], level_of(t))
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


def claimed_pct(world):
    """Percent of all land tiles that someone owns."""
    land = [t for t in world["tiles"].values() if t["terrain"] != "water"]
    return 100 * sum(1 for t in land if t["owner"] is not None) // max(1, len(land))


def refresh_scarcity(world):
    """Remember how full the map is. Claim prices use this number, which only changes at night or when the
    map or the player list changes, so the price you are quoted is the price you pay."""
    world["claimed_pct"] = claimed_pct(world)


def is_free_land(world, k):
    t = world["tiles"].get(k)
    return t is not None and t["terrain"] != "water" and t["owner"] is None


def add_army(world, nid, tile, n, dest=None, steel=False):
    """Steel soldiers fight in armies of their own, so an army is all steel or all plain."""
    if dest is None:
        for a in world["armies"]:
            if a["owner"] == nid and a["tile"] == tile and a["dest"] is None and bool(a.get("steel")) == bool(steel):
                a["n"] += n
                return a
    a = {"id": world["next_army_id"], "owner": nid, "tile": tile, "n": n, "dest": dest}
    if steel:
        a["steel"] = True
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


def grow_tiles(rules, rng, keys, tiles):
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


def default_radius(players):
    """How big the map is when nobody picks a size: about 22 tiles per nation (16 nations -> radius 11)."""
    return max(5, round(math.sqrt(22 * players / 3)))


PATCH_PERCENT = 4   # how many tiles are patch seeds: fewer seeds means bigger patches
REACH = 8        # every dry tile has hills, forest and a mountain within this many tiles, so nobody is locked out


def exact_mix(rng, n, weights):
    """n terrain names in the proportions of `weights`, shuffled."""
    total = sum(weights.values())
    out = []
    for name, w in weights.items():
        out += [name] * (n * w // total)
    names = sorted(weights, key=lambda k: -weights[k])
    i = 0
    while len(out) < n:
        out.append(names[i % len(names)])
        i += 1
    rng.shuffle(out)
    return out


def walk(rng, kset, free, start, length, keep):
    """A wandering line of free tiles that tends to keep going the same way (a river or a mountain range)."""
    cells, here, last = [start], start, None
    for _ in range(length):
        options = [n for n in neighbors(here) if n in kset and n in free and n not in cells]
        if not options:
            break
        here = last if last in options and rng.random() < keep else rng.choice(options)
        last = here
        cells.append(here)
    return cells


def blob(rng, kset, free, start, size):
    """A rounded patch of free tiles (a lake)."""
    cells = {start}
    while len(cells) < size:
        edge = sorted(n for c in cells for n in neighbors(c) if n in kset and n in free and n not in cells)
        if not edge:
            break
        cells.add(rng.choice(edge))
    return cells


def paint(rng, kset, free, terrain, name, want, shape):
    """Lay down `name` tiles in shapes until there are `want` of them."""
    got = 0
    while got < want and free:
        for k in shape(rng, kset, free, rng.choice(sorted(free))):
            if k in free:
                terrain[k] = name
                free.discard(k)
                got += 1


def river_or_lake(rng, kset, free, start):
    return walk(rng, kset, free, start, rng.randint(7, 13), 0.65) if rng.random() < 0.6 else blob(rng, kset, free, start, rng.randint(4, 8))


def mountain_range(rng, kset, free, start):
    cells = walk(rng, kset, free, start, rng.randint(5, 10), 0.75)
    return cells + [n for c in cells for n in neighbors(c) if n in kset and n in free and rng.random() < 0.3]


def give_reach(rng, kset, terrain, kinds):
    """A dry tile with no hills (or forest, or mountain) within REACH tiles gets a small outcrop of it nearby."""
    for kind in kinds:
        for _ in range(80):
            bad = [k for k in sorted(kset) if terrain[k] != "water"
                   and not any(terrain.get(x) == kind for x in within(k, REACH) if x in kset)]
            if not bad:
                break
            spots = [x for x in within(rng.choice(bad), 2) if x in kset and terrain[x] not in ("water", "mountain")]
            if spots:
                spot = rng.choice(spots)
                terrain[spot] = kind
                more = [n for n in neighbors(spot) if n in kset and terrain[n] not in ("water", "mountain")]
                if more and rng.random() < 0.7:
                    terrain[rng.choice(more)] = kind


def make_tiles(rules, rng, keys, tiles):
    """Create the map. Water (rivers, lakes) goes down first, then mountain ranges, then big patches of hills, forest and
    field in even amounts. Finally anywhere that is too far from some terrain gets a small outcrop of it, so
    every nation can reach everything but must trade for what is not at home. If tiles already exist (a map
    expansion) the old patches simply continue instead."""
    if tiles:
        return grow_tiles(rules, rng, keys, tiles)
    keys = sorted(keys)
    kset, free, terrain = set(keys), set(keys), {}
    weights = rules["terrain_weights"]
    total = sum(weights.values())
    paint(rng, kset, free, terrain, "water", len(keys) * weights.get("water", 0) // total, river_or_lake)
    paint(rng, kset, free, terrain, "mountain", len(keys) * weights.get("mountain", 0) // total, mountain_range)
    mix = {name: w for name, w in weights.items() if name not in ("water", "mountain") and w > 0}
    rest = sorted(free)
    if rest and mix:
        count = max(2, len(rest) * PATCH_PERCENT // 100)
        seeds = list(zip(rng.sample(rest, min(count, len(rest))), exact_mix(rng, count, mix)))
        for k in rest:
            terrain[k] = min(seeds, key=lambda s: distance(k, s[0]) + rng.random() * 0.9)[1]
    for k in rest:  # a field next to a mountain becomes hills half the time
        if terrain.get(k) == "field" and any(terrain.get(n) == "mountain" for n in neighbors(k)) and rng.random() < 0.5:
            terrain[k] = "hills"
    give_reach(rng, kset, terrain, [t for t in ("mountain", "hills", "forest") if weights.get(t)])
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
        "trades": [], "shipments": [], "caravans": [], "events": {}, "winner": None,
        "next_army_id": 1, "next_trade_id": 1, "next_caravan_id": 1,
    }
    make_tiles(rules, rng, within("0,0", radius), world["tiles"])
    for name in names:
        place_nation(world, rules, name, rng, list(world["tiles"]), dict(rules["start"]["resources"]))
    discover_oil(world)
    refresh_scarcity(world)
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
        "priorities": dict(rules["default_priorities"]), "auto": rules.get("auto_refine", False),
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
    refresh_scarcity(world)


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
    refresh_scarcity(world)
    return nid


# ---------- saving ----------

def to_json(world):
    return json.dumps(world, sort_keys=True)


def from_json(text):
    return json.loads(text)
