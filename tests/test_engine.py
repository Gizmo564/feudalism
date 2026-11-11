import copy
import random

import pytest

from population import world as W, tick, combat, trade, victory, views, ruleset
from population.hexes import neighbors, distance, within
from conftest import FixedRng, blank_world


def two_nations(rules, a_tile="-2,0", b_tile="2,0"):
    w = blank_world(rules)
    for name, k in (("A", a_tile), ("B", b_tile)):
        W.found_nation(w, rules, name, k, dict(rules["start"]["resources"]))
    return w


def give(w, nid, **res):
    w["nations"][nid]["resources"].update(res)


# ---------- map ----------

def test_ruleset_is_valid(rules):
    ruleset.check(rules)


def test_tile_counts_by_radius(rules):
    for r, n in ((5, 91), (6, 127), (7, 169), (8, 217), (9, 271)):
        assert len(W.new_world(rules, [], r, 1)["tiles"]) == n


def test_starts_are_fair_and_spaced(rules):
    w = W.new_world(rules, ["n%d" % i for i in range(16)], 8, 5)
    caps = [n["capital"] for n in w["nations"]]
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            assert distance(a, b) >= 2
        ring = [k for k in neighbors(a) if k in w["tiles"] and w["tiles"][k]["owner"] == i]
        assert sum(1 for k in ring if w["tiles"][k]["terrain"] == "field") >= rules["start"]["start_fields"]


def test_map_generation_is_deterministic(rules):
    a = W.new_world(rules, ["x", "y"], 6, 9)
    b = W.new_world(rules, ["x", "y"], 6, 9)
    assert W.to_json(a) == W.to_json(b)


def test_expand_map_keeps_existing_tiles(rules, world):
    before = copy.deepcopy(world["tiles"])
    W.expand_map(world, rules, 1)
    assert world["radius"] == 7 and len(world["tiles"]) == 169
    for k, t in before.items():
        assert world["tiles"][k] == t


def test_late_join_on_outer_ring_with_protection(rules, world):
    W.expand_map(world, rules, 2)
    nid = W.add_nation(world, rules, "Late")
    assert distance("0,0", world["nations"][nid]["capital"]) >= world["radius"] - 1
    assert world["nations"][nid]["protected_until"] > world["tick"]


# ---------- claiming ----------

def test_claim_adjacent_costs_wood(rules):
    w = two_nations(rules)
    owned = len(W.owned(w, 0))
    tick.queue(w, rules, 0, {"type": "claim", "tile": "0,-1"})
    wood = w["nations"][0]["resources"]["wood"]
    tick.apply_orders(w, rules, random.Random(1))
    cost = tick.claim_cost(w, rules, 0)
    assert w["tiles"]["0,-1"]["owner"] == 0
    assert w["nations"][0]["resources"]["wood"] == wood - (rules["claim"]["base"]["wood"] + rules["claim"]["per_tile"]["wood"] * owned)
    assert cost["wood"] > rules["claim"]["base"]["wood"]


def test_claim_must_touch_own_land(rules):
    w = two_nations(rules)
    tick.queue(w, rules, 0, {"type": "claim", "tile": "0,3"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"]["0,3"]["owner"] is None


def test_claim_water_fails(rules):
    w = two_nations(rules)
    w["tiles"]["0,-1"]["terrain"] = "water"
    tick.queue(w, rules, 0, {"type": "claim", "tile": "0,-1"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"]["0,-1"]["owner"] is None


def test_claim_limit_one_per_tick(rules):
    w = two_nations(rules)
    give(w, 0, wood=500, stone=500)
    tick.queue(w, rules, 0, {"type": "claim", "tile": "0,-1"})
    tick.queue(w, rules, 0, {"type": "claim", "tile": "-1,1"})
    tick.apply_orders(w, rules, random.Random(1))
    claimed = [k for k in ("0,-1", "-1,1") if w["tiles"][k]["owner"] == 0]
    assert len(claimed) == 1


def test_claim_needs_resources(rules):
    w = two_nations(rules)
    give(w, 0, wood=0)
    tick.queue(w, rules, 0, {"type": "claim", "tile": "0,-1"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"]["0,-1"]["owner"] is None


def test_claim_contest_gives_tile_to_one_nation(rules):
    w = blank_world(rules)
    W.found_nation(w, rules, "A", "-2,0", dict(rules["start"]["resources"]))
    W.found_nation(w, rules, "B", "2,0", dict(rules["start"]["resources"]))
    for nid in (0, 1):
        give(w, nid, wood=100, stone=100)
        w["tiles"]["0,0"]["owner"] = None
    w["tiles"]["-1,0"]["owner"] = 0
    w["tiles"]["1,0"]["owner"] = 1
    for nid in (0, 1):
        tick.queue(w, rules, nid, {"type": "claim", "tile": "0,0"})
    tick.apply_orders(w, rules, random.Random(2))
    assert w["tiles"]["0,0"]["owner"] in (0, 1)
    assert sum(n["resources"]["wood"] < 100 for n in w["nations"]) == 1


# ---------- buildings ----------

def test_build_home_costs_and_housing(rules):
    w = two_nations(rules)
    k = "-3,0"
    w["tiles"][k]["owner"] = 0
    before = W.housing(w, rules, 0)[0]
    wood = w["nations"][0]["resources"]["wood"]
    tick.queue(w, rules, 0, {"type": "build", "tile": k, "building": "home"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"][k]["building"] == "home"
    assert W.housing(w, rules, 0)[0] == before + 10
    assert w["nations"][0]["resources"]["wood"] == wood - 10


def test_one_building_per_tile(rules):
    w = two_nations(rules)
    k = "-3,0"
    w["tiles"][k].update(owner=0, building="market")
    give(w, 0, wood=100, stone=100)
    tick.queue(w, rules, 0, {"type": "build", "tile": k, "building": "home"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"][k]["building"] == "market"


def test_cannot_build_on_foreign_tile(rules):
    w = two_nations(rules)
    k = "2,-1"
    w["tiles"][k]["owner"] = 1
    tick.queue(w, rules, 0, {"type": "build", "tile": k, "building": "home"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"][k]["building"] is None


def test_building_tile_does_not_produce(rules):
    w = two_nations(rules)
    nation = w["nations"][0]
    for k in W.owned(w, 0):
        w["tiles"][k]["building"] = "wall"
    before = dict(nation["resources"])
    tick.produce(w, rules, nation)
    assert nation["resources"] == before


# ---------- training ----------

def test_train_converts_workers_and_pays(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    w["tiles"]["-3,0"].update(owner=0, building="barracks")
    tick.queue(w, rules, 0, {"type": "train", "n": 3})
    workers, ore, food = n["workers"], n["resources"]["ore"], n["resources"]["food"]
    tick.apply_orders(w, rules, random.Random(1))
    assert n["workers"] == workers - 3
    assert W.soldiers(w, 0) == 6 + 3
    assert n["resources"]["ore"] == ore - 3 and n["resources"]["food"] == food - 6


def test_train_limited_by_barracks_room(rules):
    w = two_nations(rules)
    tick.queue(w, rules, 0, {"type": "train", "n": 5})
    tick.apply_orders(w, rules, random.Random(1))
    assert W.soldiers(w, 0) == 10  # capital holds 10 soldiers, 6 already there


# ---------- economy ----------

def test_allocate_is_proportional_and_capped():
    out = tick.allocate(10, {"a": 5, "b": 5}, 5)
    assert out == {"a": 5, "b": 5}
    out = tick.allocate(12, {"a": 5, "b": 1}, 5)
    assert out == {"a": 5, "b": 5}
    assert all(v <= 5 for v in out.values())
    out = tick.allocate(6, {"a": 3, "b": 3}, 5)
    assert sum(out.values()) == 6


def test_priorities_all_equal_same_result(rules):
    w1 = two_nations(rules)
    w2 = copy.deepcopy(w1)
    tick.set_priorities(w1, rules, 0, {r: 1 for r in rules["resources"]})
    tick.set_priorities(w2, rules, 0, {r: 5 for r in rules["resources"]})
    tick.produce(w1, rules, w1["nations"][0])
    tick.produce(w2, rules, w2["nations"][0])
    assert w1["nations"][0]["resources"] == w2["nations"][0]["resources"]


def test_priority_zero_turns_resource_off(rules):
    w = two_nations(rules)
    for k in W.owned(w, 0):
        w["tiles"][k]["terrain"] = "forest"
    n = w["nations"][0]
    tick.set_priorities(w, rules, 0, {"wood": 0})
    before = n["resources"]["wood"]
    tick.produce(w, rules, n)
    assert n["resources"]["wood"] == before


def test_bad_priority_rejected(rules):
    w = two_nations(rules)
    with pytest.raises(ValueError):
        tick.set_priorities(w, rules, 0, {"food": 9})
    with pytest.raises(ValueError):
        tick.set_priorities(w, rules, 0, {"gold": 3})


def test_growth_limited_by_housing(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["workers"] = 20  # capital houses 20
    give(w, 0, food=500)
    tick.eat_and_grow(w, rules, n)
    assert n["workers"] == 20


def test_growth_when_room_and_food(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    give(w, 0, food=500)
    tick.eat_and_grow(w, rules, n)
    assert n["workers"] == 12 + 1


def test_over_housing_shrinks(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["workers"] = 30
    give(w, 0, food=500)
    tick.eat_and_grow(w, rules, n)
    assert n["workers"] == 30 - (10 * 20 // 100)


def test_starvation_kills(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    give(w, 0, food=0, livestock=0)
    people = W.people(w, 0)
    tick.eat_and_grow(w, rules, n)
    assert W.people(w, 0) == people - people * 50 // 100


def test_livestock_feeds_people(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    give(w, 0, food=0, livestock=20)
    people = W.people(w, 0)
    tick.eat_and_grow(w, rules, n)
    assert W.people(w, 0) >= people
    assert n["resources"]["livestock"] == 20 - people // 2


def test_spoilage_only_above_keep_level(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    need = W.people(w, 0)
    give(w, 0, food=need * 11)  # after eating, exactly 10 ticks' worth is left: nothing spoils
    tick.eat_and_grow(w, rules, n)
    assert n["resources"]["food"] == need * 10
    give(w, 0, food=1000)
    tick.eat_and_grow(w, rules, n)
    assert n["resources"]["food"] < 1000


# ---------- combat ----------

def test_fight_math_exact(rules):
    # attacker 10 rolls 120, defender 10 rolls 80 with +50%: 1200 vs 10*150*80//100 = 1200 -> tie -> defender
    res = combat.fight(rules, FixedRng(4, 0), 10, 10, 50)
    assert res["att_strength"] == 12 and res["def_strength"] == 12
    assert res["attacker_wins"] is False


def test_fight_attacker_wins_and_casualties(rules):
    res = combat.fight(rules, FixedRng(4, 0), 20, 10, 0)
    assert res["attacker_wins"] is True
    assert res["def_loss"] == min(10, 20 * 120 * 50 // 10000)
    assert res["att_loss"] == 10 * 80 * 50 // 10000


def test_fight_no_defenders(rules):
    assert combat.fight(rules, FixedRng(), 5, 0, 0)["attacker_wins"] is True


def test_defense_bonus_terrain_and_buildings(rules):
    w = two_nations(rules)
    w["tiles"]["-3,0"].update(terrain="mountain", building="wall")
    assert combat.defense_bonus(w, rules, "-3,0") == 50 + 20
    assert combat.defense_bonus(w, rules, "-2,0") == 0 + 50  # capital


def test_capture_destroys_building_and_moves_in(rules):
    w = two_nations(rules)
    k = "1,0"
    w["tiles"][k].update(owner=1, building="home")
    w["armies"] = [a for a in w["armies"] if a["owner"] == 0]
    w["armies"][0].update(n=40, tile="0,0")
    tick.queue(w, rules, 0, {"type": "move", "army": w["armies"][0]["id"], "dest": k})
    tick.apply_orders(w, rules, random.Random(1))
    tick.move_armies(w, rules, random.Random(1))
    assert w["tiles"][k]["owner"] == 0 and w["tiles"][k]["building"] is None
    assert w["armies"][0]["tile"] == k


def test_defenders_retreat_when_they_lose(rules):
    w = two_nations(rules)
    k = "1,0"
    w["tiles"][k].update(owner=1, building=None)
    w["tiles"]["2,0"].update(owner=1)
    atk = next(a for a in w["armies"] if a["owner"] == 0)
    atk.update(n=120, tile="0,0")
    dfd = next(a for a in w["armies"] if a["owner"] == 1)
    dfd.update(n=100, tile=k)
    tick.attack(w, rules, FixedRng(4, 0), atk, k)
    assert atk["tile"] == k
    assert dfd in w["armies"] and dfd["tile"] != k and dfd["n"] == 28


def test_attack_that_fails_stays_put(rules):
    w = two_nations(rules)
    k = "1,0"
    w["tiles"][k].update(owner=1)
    atk = next(a for a in w["armies"] if a["owner"] == 0)
    atk.update(n=2, tile="0,0")
    dfd = next(a for a in w["armies"] if a["owner"] == 1)
    dfd.update(n=50, tile=k)
    tick.attack(w, rules, random.Random(1), atk, k)
    assert w["tiles"][k]["owner"] == 1
    assert atk not in w["armies"] or atk["tile"] == "0,0"


def test_protected_nation_cannot_be_attacked(rules):
    w = two_nations(rules)
    w["nations"][1]["protected_until"] = w["tick"] + 3
    k = "1,0"
    w["tiles"][k].update(owner=1)
    atk = next(a for a in w["armies"] if a["owner"] == 0)
    atk.update(n=100, tile="0,0")
    tick.attack(w, rules, random.Random(1), atk, k)
    assert w["tiles"][k]["owner"] == 1 and atk["tile"] == "0,0"


def test_army_walks_one_tile_per_tick_and_avoids_water(rules):
    w = two_nations(rules)
    atk = next(a for a in w["armies"] if a["owner"] == 0)
    atk.update(tile="0,0", dest="0,3", n=5)
    w["tiles"]["0,1"]["terrain"] = "water"
    tick.move_armies(w, rules, random.Random(1))
    assert distance(atk["tile"], "0,0") == 1
    assert w["tiles"][atk["tile"]]["terrain"] != "water"


def test_oil_upkeep_pays_then_penalises(rules):
    w = two_nations(rules)
    atk = next(a for a in w["armies"] if a["owner"] == 0)
    atk.update(tile="0,0", n=10)
    give(w, 0, oil=2)
    tick.oil_upkeep(w, rules)
    assert w["nations"][0]["resources"]["oil"] == 0 and atk["n"] == 10
    tick.oil_upkeep(w, rules)
    assert atk["n"] == 8


def test_no_upkeep_on_own_land(rules):
    w = two_nations(rules)
    give(w, 0, oil=0)
    tick.oil_upkeep(w, rules)
    assert W.soldiers(w, 0) == 6


# ---------- trade ----------

def test_trade_needs_markets(rules):
    w = two_nations(rules)
    with pytest.raises(ValueError):
        trade.offer(w, rules, 0, 1, {"resources": {"wood": 5}}, {"resources": {"stone": 5}})


def market_world(rules):
    w = two_nations(rules)
    w["tiles"]["-3,0"].update(owner=0, building="market")
    w["tiles"]["3,0"].update(owner=1, building="market")
    return w


def test_trade_resources_arrive_after_travel(rules):
    w = market_world(rules)
    wood0, stone1 = w["nations"][0]["resources"]["wood"], w["nations"][1]["resources"]["stone"]
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {"resources": {"stone": 5}})
    trade.accept(w, rules, tid, 1)
    assert w["nations"][0]["resources"]["wood"] == wood0 - 10
    dist = distance("-2,0", "2,0")
    travel = -(-dist // 2)
    arrivals = []
    for i in range(travel):
        before = w["nations"][1]["resources"]["wood"]
        trade.deliver(w)
        arrivals.append(w["nations"][1]["resources"]["wood"] - before)
        w["tick"] += 1
    assert arrivals[-1] == 10 and sum(arrivals[:-1]) == 0


def test_trade_land_is_instant_with_building(rules):
    w = market_world(rules)
    w["tiles"]["-1,1"].update(owner=0, building="home")
    tid = trade.offer(w, rules, 0, 1, {"tiles": ["-1,1"]}, {"resources": {"stone": 1}})
    trade.accept(w, rules, tid, 1)
    assert w["tiles"]["-1,1"]["owner"] == 1 and w["tiles"]["-1,1"]["building"] == "home"


def test_cannot_trade_capital_or_missing_goods(rules):
    w = market_world(rules)
    with pytest.raises(ValueError):
        trade.offer(w, rules, 0, 1, {"tiles": ["-2,0"]}, {})
    with pytest.raises(ValueError):
        trade.offer(w, rules, 0, 1, {"resources": {"wood": 10 ** 6}}, {})


def test_only_recipient_can_accept(rules):
    w = market_world(rules)
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 1}}, {})
    with pytest.raises(ValueError):
        trade.accept(w, rules, tid, 0)


def test_offers_expire(rules):
    w = market_world(rules)
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 1}}, {})
    w["tick"] += rules["trade"]["offer_expires_ticks"]
    trade.expire(w, rules)
    assert trade.find(w, tid)["status"] == "expired"


# ---------- victory, elimination, autopilot ----------

def test_land_share_victory_after_hold_ticks(rules):
    w = two_nations(rules)
    for k, t in w["tiles"].items():
        t["owner"] = 0
    need = rules["victory"]["hold_ticks"]
    result = None
    for _ in range(need):
        result = victory.check(w, rules)
    assert result is not None and result[0] == 0


def test_no_victory_below_threshold(rules):
    w = two_nations(rules)
    assert victory.check(w, rules) is None


def test_last_nation_standing(rules):
    w = two_nations(rules)
    w["nations"][1]["alive"] = False
    assert victory.check(w, rules) == (0, "last nation standing")


def test_elimination_when_no_land_or_soldiers(rules):
    w = two_nations(rules)
    for k, t in w["tiles"].items():
        if t["owner"] == 1:
            t["owner"] = None
    w["armies"] = [a for a in w["armies"] if a["owner"] != 1]
    tick.eliminate(w, rules)
    assert w["nations"][1]["alive"] is False


def test_autopilot_decay_after_idle_ticks(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["idle"] = rules["autopilot"]["idle_ticks_before_decay"] - 1
    n["active"] = False
    n["workers"] = 100
    tick.autopilot(w, rules)
    assert n["workers"] == 95


def test_active_nation_not_decayed(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["idle"] = 10
    n["active"] = True
    n["workers"] = 100
    tick.autopilot(w, rules)
    assert n["workers"] == 100 and n["idle"] == 0


def test_end_game_standings(rules):
    w = two_nations(rules)
    standings = victory.end_game(w, rules)
    assert w["winner"]["nation"] in (0, 1) and len(standings) == 2


# ---------- fog of war ----------

def test_fog_off_shows_everything(rules):
    w = two_nations(rules)
    v = views.map_view(w, rules, 0)
    assert all(t["visible"] for t in v)
    assert len(views.public_standings(w, rules, 0)) == 2


def test_fog_on_hides_far_land_and_other_standings(rules):
    w = two_nations(rules)
    w["settings"]["fog"] = True
    v = {t["k"]: t for t in views.map_view(w, rules, 0)}
    assert v["-2,0"]["visible"] and v["-2,0"]["owner"] == 0
    assert not v["3,0"]["visible"] and v["3,0"]["owner"] is None
    assert len(views.public_standings(w, rules, 0)) == 1
    assert all(a["owner"] == 0 for a in views.armies_view(w, rules, 0))


# ---------- whole-tick behaviour ----------

def test_full_game_is_deterministic(rules):
    def play():
        w = W.new_world(rules, ["n%d" % i for i in range(8)], 6, 77)
        for _ in range(20):
            tick.run_tick(w, rules)
        return W.to_json(w)
    assert play() == play()


def test_state_survives_json_round_trip(rules):
    w = W.new_world(rules, ["a", "b", "c"], 5, 3)
    for _ in range(3):
        tick.run_tick(w, rules)
    again = W.from_json(W.to_json(w))
    tick.run_tick(w, rules)
    tick.run_tick(again, rules)
    assert W.to_json(w) == W.to_json(again)


def test_orders_cleared_after_tick(rules):
    w = two_nations(rules)
    tick.queue(w, rules, 0, {"type": "claim", "tile": "-3,0"})
    tick.run_tick(w, rules)
    assert w["orders"] == [] and w["tick"] == 1


def test_no_ticks_after_winner(rules):
    w = two_nations(rules)
    w["winner"] = {"nation": 0, "reason": "x", "tick": 0}
    tick.run_tick(w, rules)
    assert w["tick"] == 0


def test_eliminated_cannot_act(rules):
    w = two_nations(rules)
    w["nations"][1]["alive"] = False
    with pytest.raises(ValueError):
        tick.queue(w, rules, 1, {"type": "claim", "tile": "3,0"})


def test_rank_titles_follow_land_share(feud_rules):
    rules = feud_rules
    w = W.new_world(rules, ["a", "b", "c", "d"], 8, 1)
    assert views.rank_title(w, rules, 0) == "Lord"
    land = [k for k, t in w["tiles"].items() if t["terrain"] != "water"]
    for k in land[: len(land) * 25 // 100]:
        w["tiles"][k]["owner"] = 0
    assert views.rank_title(w, rules, 0) == "King"


def test_population_mode_has_no_titles(rules):
    w = two_nations(rules)
    assert "ranks" not in rules
    assert views.rank_title(w, rules, 0) is None


def test_feudalism_ruleset_loads_and_emperor_needs_forty_percent():
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules", "feudalism.json")
    fr = ruleset.load(path)
    w = W.new_world(fr, ["a", "b", "c", "d"], 8, 1)
    land = [k for k, t in w["tiles"].items() if t["terrain"] != "water"]
    for k in land[: len(land) * 45 // 100]:
        w["tiles"][k]["owner"] = 0
    assert views.rank_title(w, fr, 0) == "Emperor"
    won = None
    for _ in range(fr["victory"]["hold_ticks"]):
        won = victory.check(w, fr)
    assert won is not None and won[0] == 0
    assert fr["victory"]["population_share_percent"] is None


def test_empire_threshold_falls_late_but_not_below_floor(feud_rules):
    rules = feud_rules
    w = W.new_world(rules, ["n%d" % i for i in range(16)], 8, 1)
    v = rules["victory"]
    assert victory.land_threshold(w, rules) == v["land_share_percent"]
    w["tick"] = v["land_share_decay_start"] + 5
    assert victory.land_threshold(w, rules) == v["land_share_percent"] - 5
    w["tick"] = 500
    assert victory.land_threshold(w, rules) == int(v["land_share_floor_multiple"] * 100) // 16


def test_hidden_oil_shows_only_after_land_is_claimed(rules):
    w = W.new_world(rules, ["a", "b"], 5, 4)
    w["settings"]["hidden_oil"] = True
    spot = next(k for k, t in w["tiles"].items() if t["oil"] and t["owner"] is None)
    shown = lambda: {v["k"]: v for v in views.map_view(w, rules, 0)}[spot]["oil"]
    assert shown() is False
    w["tiles"][spot]["owner"] = 1
    W.discover_oil(w)
    assert shown() is True
    w["tiles"][spot]["owner"] = None  # still known once found
    assert shown() is True


def test_oil_visible_when_not_hidden(rules):
    w = W.new_world(rules, ["a", "b"], 5, 4)
    assert w["settings"]["hidden_oil"] is True            # hidden is the default
    w["settings"]["hidden_oil"] = False
    spot = next(k for k, t in w["tiles"].items() if t["oil"] and t["owner"] is None)
    assert {v["k"]: v for v in views.map_view(w, rules, 0)}[spot]["oil"] is True
