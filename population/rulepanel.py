"""The rules an admin may change while the game runs: a short table of switches and numbers.
No Discord code here. Rules are stored in the game's ruleset, so they survive restarts and rollbacks."""
from . import world as W

# key, label, kind, path into the ruleset, default if missing, (lo, hi, step), what it does, tab it is shown on
RULES = [
    ("fog", "Fog of war", "switch", ("fog_of_war",), False, None,
     "Players only see land and armies near them.", "game"),
    ("hidden_oil", "Hidden oil", "switch", ("hidden_oil",), True, None,
     "Oil stays hidden until someone claims the tile.", "game"),
    ("market", "Market needed to trade", "switch", ("trade", "requires_market"), True, None,
     "Both sides of a trade need a market.", "trade"),
    ("caravans", "Caravans", "switch", ("trade", "caravans"), False, None,
     "Traded resources walk between the capitals as caravans that enemy armies can capture. Off: goods arrive safely after a delay.", "trade"),
    ("trade_speed", "Trade speed (tiles per tick)", "number", ("movement", "trade_tiles_per_tick"), 2, (1, 6, 1),
     "How fast traded goods (or caravans) travel between capitals.", "trade"),
    ("trade_water", "Trade across water", "number", ("trade", "water"), 0, (0, 2, 1),
     "0: water doesn't matter. 1: when the capitals are split by water, one side needs a port (port to coast). 2: both sides need a port (port to port).", "trade"),
    ("sea_speed", "Sea trade speed (% of land)", "number", ("trade", "sea_speed_percent"), 150, (50, 400, 25),
     "How fast goods travel when the route between the capitals crosses water over ports. 150 is one and a half times the trade speed.", "trade"),
    ("army_steps", "Army steps a night on own land", "number", ("movement", "army_steps_on_own_land"), 1, (1, 4, 1),
     "How many tiles an army can move in a night while it travels over your own land. It always stops after the first tile that isn't yours.", "game"),
    ("capital_takes_all", "Capturing a capital takes all land", "switch", ("combat", "capital_takes_all_land"), False, None,
     "The nation that captures a capital takes the rest of that nation's land too, and the loser is out of the game.", "game"),
    ("steel_soldiers", "Steel soldiers", "switch", ("steel_soldiers", "enabled"), True, None,
     "Players with a level 2 barracks can train steel soldiers: they cost steel and fight 25% stronger.", "game"),
    ("win_share", "Win share (% of land)", "number", ("victory", "land_share_percent"), 40, (5, 100, 5),
     "Land an empire needs to win (it can still fall later in the game).", "game"),
    ("scarcity", "Land price rise (%)", "number", ("claim", "scarcity_percent"), 0, (0, 1000, 50),
     "How much dearer land is once the whole map is claimed (200 means triple; 0 turns it off).", "land"),
    ("rise_percent", "Building price rise (%)", "number", ("build", "rise_percent"), 15, (0, 100, 5),
     "Each building of a kind you already own makes the next of that kind this much dearer (of its base price).", "land"),
    ("share_percent", "Claim price (% of your stock)", "number", ("claim", "share_percent"), 0, (0, 90, 5),
     "Claiming costs this share of the wood and stone you hold, so a nation with little stone still pays little. 0 means the old fixed price.", "land"),
    ("share_stone_percent", "Claim price: stone (% of stock)", "number", ("claim", "share_stone_percent"), 10, (0, 90, 5),
     "Like the claim price share, but for stone only, so stone can be priced lower than wood.", "land"),
    ("share_per_tile", "Extra % per tile owned", "number", ("claim", "share_per_tile"), 0, (0, 10, 1),
     "The share rises by this much for every tile you own (the total never passes 90%). Only used when the claim price share is on.", "land"),
    ("share_min_wood", "Cheapest claim: wood", "number", ("claim", "share_min", "wood"), 3, (1, 20, 1),
     "The least wood a claim ever costs. Only used when the claim price share is on.", "land"),
    ("share_min_stone", "Cheapest claim: stone", "number", ("claim", "share_min", "stone"), 1, (0, 20, 1),
     "The least stone a claim ever costs. At 1 a nation can claim as long as it has any stone; at 0 claims never need stone. Only used when the claim price share is on.", "land"),
    ("expiry", "Trade offer expiry (nights)", "number", ("trade", "offer_expires_ticks"), 3, (1, 10, 1),
     "How long an offer stays open.", "trade"),
    ("tick_hours", "Hours between ticks", "number", ("tick_hours",), 24, (1, 48, 1),
     "24 means once a day at the usual time. Any other number means that many hours after the last tick.", "game"),
    ("workers", "Workers per tile", "number", ("workers_per_tile",), 5, (1, 10, 1),
     "How many workers one tile holds. Changes the whole economy, so change it with care.", "game"),
    ("start_workers", "Starting workers", "number", ("start", "workers"), 12, (1, 100, 1),
     "Workers a new nation starts with. Only nations created after the change are affected.", "start"),
    ("start_soldiers", "Starting soldiers", "number", ("start", "soldiers"), 6, (0, 50, 1),
     "Soldiers a new nation starts with. Only nations created after the change are affected.", "start"),
]
# starting stock: one rule per resource
for _res in ("food", "wood", "stone", "ore", "oil", "coal", "livestock"):
    RULES.append(("start_" + _res, "Starting " + _res, "number", ("start", "resources", _res), 0, (0, 1000, 5),
                  "How much %s a new nation starts with. Only nations created after the change are affected." % _res, "start"))
# the tabs of the admin's panel, in order
TABS = [("game", "Game"), ("land", "Land"), ("trade", "Trade"), ("start", "Start")]
BY_KEY = {r[0]: r for r in RULES}
RISKY = {"workers"}
# rules the panel hides until the admin presses "Show advanced rules"
ADVANCED = {"hidden_oil", "caravans", "trade_speed", "scarcity", "share_stone_percent", "share_per_tile", "share_min_wood",
            "share_min_stone", "workers", "start_ore", "start_oil", "start_livestock"}


def applies(rules, key):
    if key in ("trade_water", "sea_speed"):
        return "port" in rules.get("buildings", {})
    if key == "steel_soldiers":
        return "steel_soldiers" in rules
    if key == "start_coal":
        return "coal" in rules["resources"]
    if key == "win_share":
        return "ranks" in rules and rules["victory"].get("land_share_percent") is not None
    return True


def keys(rules, tab=None):
    return [r[0] for r in RULES if applies(rules, r[0]) and (tab is None or r[7] == tab)]


def tab_of(key):
    return BY_KEY[key][7]


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
