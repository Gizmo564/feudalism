import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from population import ruleset, world as W  # noqa: E402

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules", "population.json")


@pytest.fixture
def rules():
    """The engine tests' rules: Population's victory and wording with Feudalism's refined goods, ports and steel switched on,
    so one ruleset exercises every engine feature."""
    r = ruleset.load(RULES_PATH)
    f = ruleset.load(RULES_PATH.replace("population.json", "feudalism.json"))
    for k in ("resources", "yield_per_worker", "default_priorities", "refine_slots", "refine", "port", "steel_soldiers", "movement", "buildings"):
        r[k] = f[k]
    r["start"]["resources"] = f["start"]["resources"]
    r["trade"].update({k: f["trade"][k] for k in ("water", "sea_speed_percent")})
    r.pop("terms", None)
    ruleset.check(r)
    return r


@pytest.fixture
def world(rules):
    return W.new_world(rules, ["A", "B", "C", "D"], 6, 123)


class FixedRng:
    """Stand-in random generator: always picks the given index."""
    def __init__(self, *picks):
        self.picks = list(picks)

    def randrange(self, n):
        return self.picks.pop(0) if self.picks else 0


def blank_world(rules, radius=3, names=("A", "B")):
    """A small all-field map with two nations placed by hand, for exact rule tests."""
    w = W.new_world(rules, [], radius, 1)
    for t in w["tiles"].values():
        t.update(terrain="field", oil=False, owner=None, building=None)
    w["tiles"]["0,0"]["owner"] = None
    return w


@pytest.fixture
def feud_rules():
    return ruleset.load(RULES_PATH.replace("population.json", "feudalism.json"))
