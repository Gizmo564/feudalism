import os

import pytest

import simulate
from population import ruleset, tick, victory, world as W
from conftest import blank_world

PATH = os.path.join(os.path.dirname(__file__), "..", "rules", "industrialism.json")


@pytest.fixture
def rules():
    return ruleset.load(PATH)


def shop(rules, building, **stock):
    """Nation 0 owns one workshop with 12 workers and the given stock; nothing else produces."""
    w = blank_world(rules)
    for name, k in (("A", "-2,0"), ("B", "2,0")):
        W.found_nation(w, rules, name, k, dict(rules["start"]["resources"]))
    w["tiles"]["-3,0"].update(owner=0, building=building)
    n = w["nations"][0]
    n["workers"] = 12
    n["resources"].update({r: 0 for r in rules["resources"]})
    n["resources"].update(stock)
    n["priorities"] = {r: 0 for r in rules["resources"]}
    return w, n


def test_every_workshop_is_a_building_and_every_input_a_resource(rules):
    for good, spec in rules["refine"].items():
        assert spec["building"] in rules["buildings"] and good in rules["resources"]
        assert all(i in rules["resources"] for i in spec["inputs"])


def test_coal_comes_from_hills_and_mountains(rules):
    assert "coal" in rules["terrain"]["hills"]["produces"] and "coal" in rules["terrain"]["mountain"]["produces"]


def test_factory_burns_coal_and_makes_machine_parts(rules):
    w, n = shop(rules, "factory", steel=10, planks=10, coal=10)
    n["priorities"]["machine_parts"] = 5
    tick.produce(w, rules, n)
    slots = rules["refine_slots"]
    assert n["resources"]["machine_parts"] == slots and n["resources"]["coal"] == 10 - slots   # 1 power per coal
    assert n["made"] == slots


def test_oil_has_three_times_the_power_of_coal(rules):
    w, n = shop(rules, "factory", steel=10, planks=10, oil=1)
    n["priorities"]["machine_parts"] = 5
    tick.produce(w, rules, n)
    assert n["resources"]["oil"] == 0 and n["resources"]["machine_parts"] == 3      # one oil = 3 power = 3 workers' fuel


def test_no_fuel_means_no_output(rules):
    w, n = shop(rules, "factory", steel=10, planks=10)
    n["priorities"]["machine_parts"] = 5
    tick.produce(w, rules, n)
    assert n["resources"]["machine_parts"] == 0 and n["made"] == 0


def test_coke_then_steel_chain(rules):
    w, n = shop(rules, "cokeworks", coal=20)
    n["priorities"]["coke"] = 5
    tick.produce(w, rules, n)
    assert n["resources"]["coke"] > 0
    assert rules["refine"]["steel"]["inputs"] == {"ore": 1, "coke": 1}


def test_factory_output_share_wins_after_the_hold(rules):
    w = blank_world(rules)
    for name, k in (("A", "-2,0"), ("B", "2,0")):
        W.found_nation(w, rules, name, k, dict(rules["start"]["resources"]))
    rules["victory"].update(land_share_percent=None, population_share_percent=None, stock_share_percent=None, output_min=5)
    w["nations"][0]["made"], w["nations"][1]["made"] = 20, 2
    won = None
    for _ in range(rules["victory"]["hold_ticks"]):
        won = victory.check(w, rules)
    assert won and won[0] == 0 and "output" in won[1]
    w["nations"][0]["made"] = 3                                            # under output_min: the count starts over
    assert victory.check(w, rules) is None and w["nations"][0]["hold"]["output"] == 0


def test_a_short_simulated_game_runs(rules):
    w, _ = simulate.play(rules, 6, 5, 12, 1, [0, 0.3])
    assert w["tick"] >= 1


def test_auto_refine_works_only_when_inputs_are_in_store(rules):
    w, n = shop(rules, "cokeworks", coal=20)
    n["auto"] = True
    n["priorities"].update(coke=0)                       # no priority at all: auto still works the shop
    tick.produce(w, rules, n)
    assert n["resources"]["coke"] > 0
    w, n = shop(rules, "cokeworks")                      # no coal: the workers are not wasted on it
    n["auto"] = True
    n["priorities"].update(food=1)
    assert "coke" not in tick.work_plan(w, rules, n)
    w, n = shop(rules, "cokeworks", coal=20)             # off: priority 0 means idle
    n["auto"] = False
    assert "coke" not in tick.work_plan(w, rules, n)
