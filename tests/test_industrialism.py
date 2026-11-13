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


def two_armies_world(rules):
    w = blank_world(rules, radius=6)
    for name, k in (("A", "-5,0"), ("B", "5,0")):
        W.found_nation(w, rules, name, k, dict(rules["start"]["resources"]))
    for x in range(-5, 5):                                     # a long strip of A's land along the row
        w["tiles"]["%d,0" % x].update(owner=0)
    return w


def test_rails_let_armies_cover_more_ground(rules):
    import random
    w = two_armies_world(rules)
    a = W.add_army(w, 0, "-5,0", 5)
    a["dest"] = "3,0"
    tick.move_armies(w, rules, random.Random(1))
    assert a["tile"] == "-3,0"                                  # 2 steps a night on your own land
    w = two_armies_world(rules)
    for x in range(-5, 4):
        w["tiles"]["%d,0" % x]["building"] = "rail" if x != -5 else "capital"
    w["tiles"]["-5,0"]["building"] = "rail"
    a = W.add_army(w, 0, "-5,0", 5)
    a["dest"] = "3,0"
    tick.move_armies(w, rules, random.Random(1))
    assert a["tile"] == "3,0"                                   # 8 tiles in one night: 6 free rail steps plus 2


def test_rail_cost_never_rises_but_other_buildings_do(rules):
    w = two_armies_world(rules)
    first = tick.build_cost(w, rules, 0, "rail")
    for x in range(-4, -1):
        w["tiles"]["%d,0" % x]["building"] = "rail"
    assert tick.build_cost(w, rules, 0, "rail") == first
    w["tiles"]["-1,0"]["building"] = "home"
    assert tick.build_cost(w, rules, 0, "home") != rules["buildings"]["home"]["cost"]


def test_tanks_cost_more_and_fight_like_four_soldiers(rules):
    w = two_armies_world(rules)
    assert "engineworks" in tick.unit_problem(w, rules, 0, "tank")
    w["tiles"]["-4,0"].update(building="engineworks")
    assert tick.unit_problem(w, rules, 0, "tank") is None
    cost = tick.train_cost(rules, unit="tank")
    assert cost["engines"] == 1 and cost["steel"] == 2 and cost["ore"] == 1
    n = w["nations"][0]
    n["resources"].update(engines=2, steel=10, ore=10, food=50)
    n["workers"] = 20
    w["tiles"]["-3,0"].update(building="barracks")
    tick.queue(w, rules, 0, {"type": "train", "n": 5, "unit": "tank"})
    tick.apply_orders(w, rules, __import__("random").Random(1))
    tanks = [a for a in w["armies"] if a.get("unit") == "tank"]
    assert sum(a["n"] for a in tanks) == 2                                  # only two engines
    assert tick.strength(rules, tanks[0]) == 8                              # each tank counts as four


def test_boats_burn_coal(rules):
    assert tick.boat_cost(rules) == {"coal": 2}


def test_artillery_ignores_walls_and_the_capital_bonus(rules):
    import random
    w = two_armies_world(rules)
    w["tiles"]["-1,0"].update(owner=1, building="wall")
    w["tiles"]["-1,0"]["level"] = 1
    plain = W.add_army(w, 0, "-2,0", 10)
    gun = W.add_army(w, 0, "-2,0", 10, unit="artillery")
    seen = []
    orig = tick.combat.fight
    tick.combat.fight = lambda rules, rng, an, dn, bonus: seen.append(bonus) or orig(rules, rng, an, dn, bonus)
    try:
        tick.attack(w, rules, random.Random(1), plain, "-1,0")
        w["tiles"]["-1,0"].update(owner=1, building="wall")
        gun["tile"] = "-2,0"
        tick.attack(w, rules, random.Random(1), gun, "-1,0")
    finally:
        tick.combat.fight = orig
    assert seen[0] > seen[1] == 0                           # the wall counted against soldiers, not against guns


def test_trains_run_along_rails_only(rules):
    import random
    w = two_armies_world(rules)
    for x in range(-5, 3):
        w["tiles"]["%d,0" % x].update(owner=0, building="rail" if x != -5 else "capital")
    t = W.add_army(w, 0, "-5,0", 3, unit="train")
    t["dest"] = "2,0"
    tick.move_armies(w, rules, random.Random(1))
    assert t["tile"] == "2,0"                               # 7 tiles in one night, more than a soldier walks
    w["tiles"]["0,0"].update(building=None)                 # cut the line: no rail route, so the train cannot be ordered across
    assert tick.find_path(w, "-5,0", "2,0", tick.rails(w)) is None


def test_auto_refine_stops_when_the_good_piles_up(rules):
    w, n = shop(rules, "cokeworks", coal=100, coke=rules["auto_refine_cap"])
    n["auto"] = True
    assert "coke" not in tick.work_plan(w, rules, n)             # enough coke in store: the coal is kept for other uses
    w, n = shop(rules, "factory", steel=50, planks=50, coal=50, machine_parts=500)
    n["auto"] = True
    assert "machine_parts" in tick.work_plan(w, rules, n)         # factory output is the win condition, so it never stops


def test_caravans_ride_connected_rails_even_across_nations(rules):
    from population import trade
    w = two_armies_world(rules)
    for x in range(-5, 6):                                     # one unbroken line: A's rails, then B's rails
        w["tiles"]["%d,0" % x].update(owner=0 if x < 0 else 1, building="rail")
    w["tiles"]["-5,0"]["building"], w["tiles"]["5,0"]["building"] = "capital", "capital"
    trade.send_caravan(w, 0, 1, {"wood": 5})
    trade.move_caravans(w, rules)
    assert w["caravans"][0]["tile"] == "3,0"                   # 8 tiles in a night: 6 by train, 2 on foot
    trade.move_caravans(w, rules)
    assert not w["caravans"] and w["nations"][1]["resources"]["wood"] >= 5
    w2 = two_armies_world(rules)
    trade.send_caravan(w2, 0, 1, {"wood": 5})
    trade.move_caravans(w2, rules)
    assert w2["caravans"][0]["tile"] == "-3,0"                 # without rails it walks 2 tiles


def test_a_train_crosses_foreign_rails_without_capturing_them(rules):
    import random
    w = two_armies_world(rules)
    for x in range(-5, 6):
        w["tiles"]["%d,0" % x].update(owner=0 if x < 0 else 1, building="rail")
    w["armies"] = []                                           # no soldiers anywhere to block the line
    t = W.add_army(w, 0, "-5,0", 2, unit="train")
    t["dest"] = "5,0"
    tick.move_armies(w, rules, random.Random(1))
    assert t["tile"] == "5,0"                                  # 10 tiles on a line that runs into nation B
    assert all(w["tiles"]["%d,0" % x]["owner"] == (0 if x < 0 else 1) for x in range(-5, 6))


def test_a_train_carries_cargo_and_soldiers(rules, tmp_path):
    from population.game import Game, GameError
    from population.store import Store
    g = Game(Store(str(tmp_path / "t.db")))
    g.open_signup("industrialism")
    g.join(1, "Aldmere")
    g.join(2, "Brask")
    g.start(6, seed=3)
    w = g.world
    w["armies"] = []
    a, b = w["nations"][0], w["nations"][1]
    t = W.add_army(w, 0, a["capital"], 2, unit="train")
    a["resources"]["wood"] = 100
    g.load(1, t["id"], "wood", 500)
    assert t["cargo"]["wood"] == 80 and a["resources"]["wood"] == 20      # 2 train units hold 40 each
    t["tile"] = b["capital"]                                             # parked at B's station
    before = b["resources"]["wood"]
    g.unload(1, t["id"])
    assert b["resources"]["wood"] == before + 80 and not t["cargo"]
    t["tile"] = a["capital"]
    soldiers = W.add_army(w, 0, a["capital"], 15)
    g.board(1, t["id"], soldiers["id"])
    assert t["pax"] == 15 and W.soldiers(w, 0) >= 15 + 2
    with pytest.raises(GameError):
        g.board(1, t["id"], W.add_army(w, 0, a["capital"], 10)["id"])    # only 20 seats
    g.disembark(1, t["id"])
    assert not t["pax"]
