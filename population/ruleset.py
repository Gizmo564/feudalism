"""Loads and checks a ruleset file. All game numbers live in the ruleset, not in code."""
import json

REQUIRED = [
    "name", "version", "resources", "terrain", "terrain_weights", "oil_deposit_one_in",
    "yield_per_worker", "coastal_food_bonus", "workers_per_tile", "default_priorities",
    "eat_per_person", "food_value", "spoilage", "growth", "over_housing_shrink_percent",
    "starvation_death_percent", "buildings", "capital", "start", "claim", "soldier",
    "combat", "movement", "oil_upkeep", "trade", "vision_radius", "fog_of_war",
    "autopilot", "victory",
]


def load(path):
    with open(path) as f:
        rules = json.load(f)
    check(rules)
    return rules


def check(rules):
    for k in REQUIRED:
        if k not in rules:
            raise ValueError("ruleset is missing '%s'" % k)
    res = set(rules["resources"])
    for r in res:
        if r not in rules["yield_per_worker"]:
            raise ValueError("no yield_per_worker for '%s'" % r)
        if r not in rules["start"]["resources"]:
            raise ValueError("no starting amount for '%s'" % r)
        if r not in rules["default_priorities"]:
            raise ValueError("no default priority for '%s'" % r)
    for name, t in rules["terrain"].items():
        for r in t["produces"]:
            if r not in res:
                raise ValueError("terrain '%s' produces unknown resource '%s'" % (name, r))
    for name in rules["terrain_weights"]:
        if name not in rules["terrain"]:
            raise ValueError("terrain_weights names unknown terrain '%s'" % name)
    for name, b in rules["buildings"].items():
        for r in b["cost"]:
            if r not in res:
                raise ValueError("building '%s' costs unknown resource '%s'" % (name, r))
        for up in b.get("upgrades", []):
            for r in up["cost"]:
                if r not in res:
                    raise ValueError("an upgrade of '%s' costs unknown resource '%s'" % (name, r))
    if not 0 <= rules.get("build", {}).get("rise_percent", 0) <= 200:
        raise ValueError("build.rise_percent must be 0 to 200")
    for r, v in rules["default_priorities"].items():
        if not isinstance(v, int) or not 0 <= v <= 5:
            raise ValueError("default priority for '%s' must be 0 to 5" % r)
    if not rules["combat"]["roll_percent"]:
        raise ValueError("combat.roll_percent cannot be empty")
