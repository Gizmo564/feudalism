"""The rules an admin may change while the game runs: a short table of switches and numbers.
No Discord code here. Rules are stored in the game's ruleset, so they survive restarts and rollbacks."""
from . import world as W

# key, label, kind, path into the ruleset, default if missing, (lo, hi, step), what it does
RULES = [
    ("fog", "Fog of war", "switch", ("fog_of_war",), False, None,
     "Players only see land and armies near them."),
    ("hidden_oil", "Hidden oil", "switch", ("hidden_oil",), True, None,
     "Oil stays hidden until someone claims the tile."),
    ("market", "Market needed to trade", "switch", ("trade", "requires_market"), True, None,
     "Both sides of a trade need a market."),
    ("caravans", "Caravans", "switch", ("trade", "caravans"), False, None,
     "Traded resources walk between the capitals as caravans that enemy armies can capture. Off: goods arrive safely after a delay."),
    ("trade_speed", "Trade speed (tiles per tick)", "number", ("movement", "trade_tiles_per_tick"), 2, (1, 6, 1),
     "How fast traded goods (or caravans) travel between capitals."),
    ("win_share", "Win share (% of land)", "number", ("victory", "land_share_percent"), 40, (5, 100, 5),
     "Land an empire needs to win (it can still fall later in the game)."),
    ("scarcity", "Land price rise (%)", "number", ("claim", "scarcity_percent"), 0, (0, 1000, 50),
     "How much dearer land is once the whole map is claimed (200 means triple; 0 turns it off)."),
    ("expiry", "Trade offer expiry (nights)", "number", ("trade", "offer_expires_ticks"), 3, (1, 10, 1),
     "How long an offer stays open."),
    ("tick_hours", "Hours between ticks", "number", ("tick_hours",), 24, (1, 48, 1),
     "24 means once a day at the usual time. Any other number means that many hours after the last tick."),
    ("workers", "Workers per tile", "number", ("workers_per_tile",), 5, (1, 10, 1),
     "How many workers one tile holds. Changes the whole economy, so change it with care."),
]
BY_KEY = {r[0]: r for r in RULES}
RISKY = {"workers"}


def applies(rules, key):
    if key == "win_share":
        return "ranks" in rules and rules["victory"].get("land_share_percent") is not None
    return True


def keys(rules):
    return [r[0] for r in RULES if applies(rules, r[0])]


def label(key):
    return BY_KEY[key][1]


def current(world, rules, key):
    """The value now. Before the game starts there is no world yet, so the ruleset is the only source."""
    if world is not None and key in ("fog", "hidden_oil"):
        return bool(world["settings"][key])
    node = rules
    for step in BY_KEY[key][3][:-1]:
        node = node[step]
    return node.get(BY_KEY[key][3][-1], BY_KEY[key][4])


def check(rules, key, value):
    """Return the value as the right type, or raise ValueError saying what is allowed."""
    if key not in BY_KEY or not applies(rules, key):
        raise ValueError("this mode has no rule '%s'" % key)
    kind, limits = BY_KEY[key][2], BY_KEY[key][5]
    if kind == "switch":
        return bool(value)
    lo, hi, _ = limits
    if not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError("%s must be from %d to %d" % (label(key), lo, hi))
    return value


def apply(world, rules, key, value):
    node = rules
    for step in BY_KEY[key][3][:-1]:
        node = node[step]
    node[BY_KEY[key][3][-1]] = value
    if world is None:
        return
    if key == "fog":
        world["settings"]["fog"] = value
    elif key == "hidden_oil":
        world["settings"]["hidden_oil"] = value
        W.discover_oil(world)


def sync_world(world, rules):
    """After a rollback the world holds old switch values; put the current rules back."""
    world["settings"]["fog"] = bool(rules["fog_of_war"])
    world["settings"]["hidden_oil"] = bool(rules.get("hidden_oil", True))
    W.discover_oil(world)


def show(value):
    return ("on" if value else "off") if isinstance(value, bool) else str(value)
