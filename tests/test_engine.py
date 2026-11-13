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
    rules["claim"]["share_percent"] = 0          # the old fixed price
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


def contest_world(rules):
    """Nations 0 and 1 both queue a claim of the free tile 0,0 for tonight."""
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
    return w


def test_same_claim_makes_the_tile_contested(rules):
    w = contest_world(rules)
    tick.apply_orders(w, rules, random.Random(2))
    assert w["tiles"]["0,0"]["owner"] is None and w["contested"]["0,0"]["nations"] == [0, 1]
    assert all(n["resources"]["wood"] == 100 for n in w["nations"])        # nobody paid yet
    assert any("contested" in e for e in w["events"]["0"]) and any("contested" in e for e in w["events"]["1"])


def test_contested_tile_one_gives_up_the_other_gets_it(rules):
    w = contest_world(rules)
    tick.apply_orders(w, rules, random.Random(2))
    w["contested"]["0,0"]["choices"] = {"0": "yield", "1": "fight"}
    tick.resolve_contests(w, rules, random.Random(2))
    assert w["tiles"]["0,0"]["owner"] == 1 and "0,0" not in w["contested"]
    assert w["nations"][1]["resources"]["wood"] < 100 and w["nations"][0]["resources"]["wood"] == 100


def test_contested_tile_everyone_gives_up(rules):
    w = contest_world(rules)
    tick.apply_orders(w, rules, random.Random(2))
    w["contested"]["0,0"]["choices"] = {"0": "yield", "1": "yield"}
    tick.resolve_contests(w, rules, random.Random(2))
    assert w["tiles"]["0,0"]["owner"] is None and not w["contested"]


def test_contested_tile_duel_goes_to_the_stronger_side(rules):
    wins = {0: 0, 1: 0}
    for seed in range(20):
        w = contest_world(rules)
        tick.apply_orders(w, rules, random.Random(seed))
        W.add_army(w, 0, "-1,0", 40)
        W.add_army(w, 1, "1,0", 2)
        tick.resolve_contests(w, rules, random.Random(seed))                    # nobody answered: both fight
        wins[w["tiles"]["0,0"]["owner"]] += 1
    assert wins[0] > wins[1]


def test_nobody_else_can_claim_a_contested_tile(rules):
    w = contest_world(rules)
    tick.apply_orders(w, rules, random.Random(2))
    from population.game import Game
    tick.queue(w, rules, 0, {"type": "claim", "tile": "0,0"})
    tick.apply_orders(w, rules, random.Random(3))
    assert w["tiles"]["0,0"]["owner"] is None


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
    assert w["nations"][0]["resources"]["wood"] == wood - rules["buildings"]["home"]["cost"]["wood"]


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
    assert [o["n"] for o in w["orders"]] == [1]   # the rest waits for the next night
    assert any("barracks room" in e for e in w["events"]["0"])


def test_waiting_train_order_finishes_when_room_opens(rules):
    w = two_nations(rules)
    tick.queue(w, rules, 0, {"type": "train", "n": 5})
    tick.apply_orders(w, rules, random.Random(1))
    w["tiles"]["-3,0"].update(owner=0, building="barracks")
    tick.apply_orders(w, rules, random.Random(1))
    assert W.soldiers(w, 0) == 11 and w["orders"] == []


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


# ---------- claim price: cheaper start, dearer as the map fills ----------

def test_claim_price_rises_with_scarcity_and_falls_when_map_grows(rules):
    w = two_nations(rules)
    W.refresh_scarcity(w)
    cheap = tick.claim_cost(w, rules, 0)
    for k, t in list(w["tiles"].items()):          # someone owns most of the map
        if t["terrain"] != "water" and t["owner"] is None and w["tiles"][k]["owner"] is None:
            t["owner"] = 1
    W.refresh_scarcity(w)
    full = tick.claim_cost(w, rules, 0)
    assert all(full[r] >= cheap[r] for r in cheap) and sum(full.values()) > sum(cheap.values())
    assert full["wood"] <= cheap["wood"] * (100 + rules["claim"]["scarcity_percent"]) // 100 + 1
    W.expand_map(w, rules, 2)                      # new land is free, so land gets cheaper again
    assert sum(tick.claim_cost(w, rules, 0).values()) < sum(full.values())


def test_share_price_follows_your_stock_with_a_floor(rules):
    c = rules["claim"]
    c.update(share_percent=20, share_stone_percent=20, share_per_tile=0, share_min={"wood": 3, "stone": 1}, scarcity_percent=0)
    w = two_nations(rules)
    res = w["nations"][0]["resources"]
    res.update(wood=100, stone=50)
    assert tick.claim_cost(w, rules, 0) == {"wood": 20, "stone": 10}
    res.update(wood=5, stone=3)                       # a small stock: the price drops to the floor, never to zero
    cost = tick.claim_cost(w, rules, 0)
    assert cost == {"wood": 3, "stone": 1} and tick.can_pay(res, cost)
    c.update(share_stone_percent=10)                  # stone has its own, lower share
    res.update(wood=100, stone=100)
    assert tick.claim_cost(w, rules, 0) == {"wood": 20, "stone": 10}
    c.update(share_per_tile=5)                        # the share grows with land, up to 90%
    res.update(wood=100, stone=100)
    owned = len(W.owned(w, 0))
    assert tick.claim_cost(w, rules, 0)["wood"] == 100 * (20 + 5 * owned) // 100
    c.update(share_per_tile=1000)
    assert tick.claim_cost(w, rules, 0)["wood"] == 90


def test_quoted_price_is_what_you_pay(rules):
    w = two_nations(rules)
    give(w, 0, wood=500, stone=500)
    W.refresh_scarcity(w)
    quote = tick.claim_cost(w, rules, 0)
    before = dict(w["nations"][0]["resources"])
    k = "0,-1"
    tick.queue(w, rules, 0, {"type": "claim", "tile": k})
    tick.apply_orders(w, rules, random.Random(1))
    for r, v in quote.items():
        assert before[r] - w["nations"][0]["resources"][r] == v


# ---------- per-tile production cap ----------

def test_tile_plan_matches_totals_and_respects_cap(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["workers"] = 40
    for k in neighbors(n["capital"]):
        if w["tiles"][k]["terrain"] != "water":
            w["tiles"][k]["owner"] = 0
    plan = tick.tile_plan(w, rules, n)
    assert all(sum(c for c, _ in per.values()) <= rules["workers_per_tile"] for per in plan.values())
    totals = {}
    for per in plan.values():
        for r, (c, a) in per.items():
            got = totals.get(r, (0, 0))
            totals[r] = (got[0] + c, got[1] + a)
    assert totals == tick.work_plan(w, rules, n)


# ---------- open offers ----------

def test_open_offer_goes_to_first_nation_with_a_market(rules):
    w = market_world(rules)
    w["tiles"]["-3,1"].update(owner=0)
    tid = trade.offer(w, rules, 0, None, {"resources": {"wood": 10}}, {"resources": {"stone": 5}})
    with pytest.raises(ValueError):
        trade.accept(w, rules, tid, 0)               # not your own
    trade.accept(w, rules, tid, 1)
    o = trade.find(w, tid)
    assert o["status"] == "accepted" and o["to"] == 1
    with pytest.raises(ValueError):
        trade.accept(w, rules, tid, 1)               # only once


def test_open_offer_needs_the_senders_market_and_a_market_to_accept(rules):
    w = two_nations(rules)
    with pytest.raises(ValueError):
        trade.offer(w, rules, 0, None, {"resources": {"wood": 5}}, {})
    w["tiles"]["-3,0"].update(owner=0, building="market")
    tid = trade.offer(w, rules, 0, None, {"resources": {"wood": 5}}, {})
    with pytest.raises(ValueError):
        trade.accept(w, rules, tid, 1)               # no market
    trade.decline(w, tid, 0)
    assert trade.find(w, tid)["status"] == "withdrawn"


# ---------- caravans ----------

def caravan_world(rules, third=False):
    rules["trade"]["caravans"] = True
    w = market_world(rules)
    if third:
        W.found_nation(w, rules, "C", "0,3", dict(rules["start"]["resources"]))
    return w


def run_caravans(w, rules, ticks):
    for _ in range(ticks):
        tick.trade.move_caravans(w, rules)
        w["tick"] += 1


def test_caravans_replace_shipments_and_carry_goods_over_land(rules):
    w = caravan_world(rules)
    wood0, stone0 = w["nations"][0]["resources"]["wood"], w["nations"][1]["resources"]["stone"]
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {"resources": {"stone": 5}})
    trade.accept(w, rules, tid, 1)
    assert w["shipments"] == [] and len(w["caravans"]) == 2
    assert w["nations"][0]["resources"]["wood"] == wood0 - 10              # the goods left at once
    run_caravans(w, rules, 1)
    assert [c["tile"] for c in w["caravans"]] == ["0,0", "0,0"]            # 2 tiles a tick, on the way
    assert w["nations"][1]["resources"]["wood"] == rules["start"]["resources"]["wood"]
    run_caravans(w, rules, 1)
    assert w["caravans"] == []
    assert w["nations"][1]["resources"]["wood"] == rules["start"]["resources"]["wood"] + 10
    assert w["nations"][0]["resources"]["stone"] == rules["start"]["resources"]["stone"] + 5
    assert any("caravan arrived" in e for e in w["events"]["1"])


def test_caravan_can_be_captured_by_a_third_nation(rules):
    w = caravan_world(rules, third=True)
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {})
    trade.accept(w, rules, tid, 1)
    W.add_army(w, 2, "0,0", 3)
    wood_c = w["nations"][2]["resources"]["wood"]
    run_caravans(w, rules, 1)
    assert w["caravans"] == []
    assert w["nations"][2]["resources"]["wood"] == wood_c + 10
    assert w["nations"][1]["resources"]["wood"] == rules["start"]["resources"]["wood"]      # never arrived
    assert any("captured" in e for e in w["events"]["0"]) and any("captured" in e for e in w["events"]["1"])
    assert any("captured a caravan" in e for e in w["events"]["2"])


def test_an_army_arriving_on_a_waiting_caravan_captures_it(rules):
    w = caravan_world(rules, third=True)
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {})
    trade.accept(w, rules, tid, 1)
    run_caravans(w, rules, 1)                                              # now standing on 0,0
    W.add_army(w, 2, "0,0", 3)
    run_caravans(w, rules, 1)
    assert w["caravans"] == [] and w["nations"][2]["resources"]["wood"] >= rules["start"]["resources"]["wood"] + 10


def test_the_two_traders_armies_do_not_capture(rules):
    w = caravan_world(rules)
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {})
    trade.accept(w, rules, tid, 1)
    W.add_army(w, 1, "0,0", 3)
    W.add_army(w, 0, "1,0", 3)
    run_caravans(w, rules, 2)
    assert w["nations"][1]["resources"]["wood"] == rules["start"]["resources"]["wood"] + 10


def test_no_land_route_blocks_a_caravan_trade_but_not_land_or_offers(rules):
    w = caravan_world(rules)
    for k, t in w["tiles"].items():
        if k.startswith("0,") or k.startswith("-0,"):
            t["terrain"] = "water"
    wood0 = w["nations"][0]["resources"]["wood"]
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {})
    with pytest.raises(ValueError, match="no land route"):
        trade.accept(w, rules, tid, 1)
    assert w["nations"][0]["resources"]["wood"] == wood0 and w["caravans"] == []        # nothing changed
    assert trade.find(w, tid)["status"] == "open"


def test_caravan_speed_and_old_worlds(rules):
    w = caravan_world(rules)
    rules["movement"]["trade_tiles_per_tick"] = 1
    del w["caravans"]                                                       # a world saved before caravans existed
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {})
    trade.accept(w, rules, tid, 1)
    run_caravans(w, rules, 1)
    assert w["caravans"][0]["tile"] == "-1,0"
    import json
    json.dumps(w)                                                           # still saveable


def test_who_sees_a_caravan(rules):
    w = caravan_world(rules, third=True)
    tid = trade.offer(w, rules, 0, 1, {"resources": {"wood": 10}}, {})
    trade.accept(w, rules, tid, 1)
    mine = views.caravans_view(w, rules, 0)
    assert mine[0]["res"] == {"wood": 10}
    assert views.caravans_view(w, rules, 1)[0]["res"] == {"wood": 10}
    rules["vision_radius"] = 1
    assert views.caravans_view(w, rules, 2) == []                           # far away: unseen
    W.add_army(w, 2, "-2,1", 1)
    seen = views.caravans_view(w, rules, 2)
    assert len(seen) == 1 and "res" not in seen[0]                          # seen, but not what it carries


# ---------- rising prices and upgrades ----------

def test_each_building_costs_more_than_the_last(rules):
    w = two_nations(rules)
    base = rules["buildings"]["home"]["cost"]["wood"]
    assert tick.build_cost(w, rules, 0, "home")["wood"] == base
    w["tiles"]["-3,0"].update(owner=0, building="home")
    rise = rules["build"]["rise_percent"]
    assert tick.build_cost(w, rules, 0, "home")["wood"] == -(-base * (100 + rise) // 100)
    assert tick.build_cost(w, rules, 0, "barracks") == rules["buildings"]["barracks"]["cost"]   # other kinds unaffected
    assert tick.build_cost(w, rules, 0, "home", queued=1)["wood"] > tick.build_cost(w, rules, 0, "home")["wood"]


def test_upgrade_raises_housing_and_costs(rules):
    w = two_nations(rules)
    k = "-3,0"
    w["tiles"][k].update(owner=0, building="home")
    w["nations"][0]["resources"].update(wood=500, stone=500, tools=50)
    before = W.housing(w, rules, 0)[0]
    up = tick.upgrade_of(rules, w["tiles"][k])
    tick.queue(w, rules, 0, {"type": "upgrade", "tile": k})
    tick.apply_orders(w, rules, random.Random(1))
    assert W.level_of(w["tiles"][k]) == 2
    assert W.housing(w, rules, 0)[0] == before - rules["buildings"]["home"]["houses_workers"] + up["houses_workers"]
    assert w["nations"][0]["resources"]["wood"] == 500 - up["cost"]["wood"]


def test_upgrade_stops_at_top_level_and_skips_capital(rules):
    w = two_nations(rules)
    k = "-3,0"
    w["tiles"][k].update(owner=0, building="home", level=1 + len(rules["buildings"]["home"]["upgrades"]))
    assert tick.upgrade_of(rules, w["tiles"][k]) is None
    assert tick.upgrade_of(rules, w["tiles"][w["nations"][0]["capital"]]) is None


def test_upgrade_fails_without_resources(rules):
    w = two_nations(rules)
    k = "-3,0"
    w["tiles"][k].update(owner=0, building="home")
    w["nations"][0]["resources"].update(wood=0, stone=0)
    tick.queue(w, rules, 0, {"type": "upgrade", "tile": k})
    tick.apply_orders(w, rules, random.Random(1))
    assert W.level_of(w["tiles"][k]) == 1


# ---------- map generator ----------

def test_default_radius_grows_with_players():
    sizes = [W.default_radius(p) for p in (4, 8, 12, 16, 24)]
    assert sizes == sorted(sizes) and W.default_radius(1) == 5 and W.default_radius(16) == 11


def test_generated_map_has_every_terrain_and_is_reachable(rules):
    for seed in (1, 2, 3):
        w = W.new_world(rules, ["A", "B", "C", "D"], 8, seed)
        kinds = {t["terrain"] for t in w["tiles"].values()}
        assert {"field", "forest", "hills", "mountain", "water"} <= kinds
        assert len(w["tiles"]) == 3 * 8 * 9 + 1


def refinery_world(rules, building, **stock):
    """Nation 0 owns a workshop on -3,0 with plenty of workers and the given stock."""
    w = two_nations(rules)
    w["tiles"]["-3,0"].update(owner=0, building=building)
    n = w["nations"][0]
    n["workers"] = 12
    n["resources"].update(stock)
    return w, n


def test_workshop_needs_a_worker_and_inputs(rules):
    w, n = refinery_world(rules, "toolworks", wood=10, stone=10, tools=0)
    n["priorities"].update(tools=5, food=0, wood=0, stone=0, ore=0, oil=0, livestock=0)
    plan = tick.work_plan(w, rules, n)
    assert plan["tools"][0] == rules["refine_slots"]                        # only a few job slots per workshop
    tick.produce(w, rules, n)
    assert n["resources"]["tools"] == rules["refine_slots"]
    assert n["resources"]["wood"] == 10 - rules["refine_slots"] and n["resources"]["stone"] == 10 - rules["refine_slots"]


def test_workshop_makes_nothing_without_inputs(rules):
    w, n = refinery_world(rules, "forge", ore=0, oil=0, tools=0, steel=0)
    n["priorities"].update(steel=5)
    tick.produce(w, rules, n)
    assert n["resources"]["steel"] == 0


def test_no_workshop_no_refined_goods(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["resources"].update(wood=50, stone=50)
    n["priorities"].update(tools=5)
    assert "tools" not in tick.work_plan(w, rules, n)


def test_building_a_workshop_turns_its_priority_on(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["resources"].update(wood=500, stone=500, ore=50)
    assert n["priorities"]["steel"] == 0
    w["tiles"]["-3,0"].update(owner=0)
    tick.queue(w, rules, 0, {"type": "build", "tile": "-3,0", "building": "forge"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"]["-3,0"]["building"] == "forge" and n["priorities"]["steel"] > 0


def test_provisions_speed_up_growth(rules):
    w = two_nations(rules)
    n = w["nations"][0]
    n["workers"] = 10
    n["resources"].update(food=500, provisions=0)
    tick.eat_and_grow(w, rules, n)
    plain = n["workers"] - 10
    n["workers"] = 10
    n["resources"].update(food=500, provisions=50)
    tick.eat_and_grow(w, rules, n)
    assert n["workers"] - 10 > plain and n["resources"]["provisions"] < 50


def strait(rules, width=1):
    """A map that is all water except land tile a (nation 0's, with a market) and land tile c, `width` water tiles away in a line."""
    from population.hexes import key, parse
    w = two_nations(rules)
    for t in w["tiles"].values():
        t.update(terrain="water", owner=None, building=None, oil=False)
    a = "-3,0"
    (aq, ar), (nq, nr) = parse(a), parse("-2,0")
    water = [key(aq + (nq - aq) * i, ar + (nr - ar) * i) for i in range(1, width + 1)]
    c = key(aq + (nq - aq) * (width + 1), ar + (nr - ar) * (width + 1))
    w["tiles"][a].update(terrain="field", owner=0, building="market")
    w["tiles"][c].update(terrain="field")
    w["armies"] = []
    w["nations"][0]["resources"].update(wood=500, stone=500, tools=20, bricks=20)
    return w, a, water[0], c


def test_port_bridges_one_water_tile(rules):
    w, a, b, c = strait(rules)
    assert tick.next_step(w, a, c) is None                      # no way across open water
    tick.queue(w, rules, 0, {"type": "build", "tile": b, "building": "port"})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["tiles"][b]["building"] == "port" and w["tiles"][b]["owner"] == 0
    assert tick.next_step(w, a, c) == b and tick.find_path(w, a, c) == [b, c]
    assert tick.crosses_water(w, [b, c])


def test_port_needs_market_and_your_land_next_to_it(rules):
    w, a, b, c = strait(rules)
    assert tick.port_problem(w, rules, 0, b) is None
    assert "water" in tick.port_problem(w, rules, 0, c)           # a port goes on water
    w["tiles"][a]["building"] = None
    assert "market" in tick.port_problem(w, rules, 0, b)
    w["tiles"][a]["building"] = "market"
    w["tiles"][a]["owner"] = None
    assert "touch" in tick.port_problem(w, rules, 0, b)


def test_two_wide_water_needs_two_ports(rules):
    w, a, b, c = strait(rules, width=2)
    far = tick.find_path(w, a, c)
    assert far is None
    from population.hexes import neighbors
    water2 = next(k for k in neighbors(b) if c in neighbors(k) and w["tiles"][k]["terrain"] == "water")
    assert "touch" in tick.port_problem(w, rules, 0, water2)     # the first port has to come first
    w["tiles"][b].update(owner=0, building="port")
    assert tick.port_problem(w, rules, 0, water2) is None
    w["tiles"][water2].update(owner=0, building="port")
    assert tick.find_path(w, a, c) == [b, water2, c]


def test_crossing_costs_a_flat_boat_fee(rules):
    w, a, b, c = strait(rules)
    w["tiles"][b].update(owner=0, building="port")
    n = w["nations"][0]
    n["resources"]["wood"] = 100
    w["armies"].append({"id": 99, "owner": 0, "tile": a, "n": 3, "dest": None})
    tick.queue(w, rules, 0, {"type": "move", "army": 99, "dest": c})
    tick.apply_orders(w, rules, random.Random(1))
    assert n["resources"]["wood"] == 100 - rules["port"]["boat_wood"]
    assert w["armies"][0]["dest"] == c


def test_land_routes_cost_no_boat(rules):
    w, a, b, c = strait(rules)
    w["tiles"][b].update(owner=0, building="port")
    w["tiles"][b].update(terrain="field", building=None)           # now it is just land
    n = w["nations"][0]
    n["resources"]["wood"] = 100
    w["armies"].append({"id": 99, "owner": 0, "tile": a, "n": 3, "dest": None})
    tick.queue(w, rules, 0, {"type": "move", "army": 99, "dest": c})
    tick.apply_orders(w, rules, random.Random(1))
    assert n["resources"]["wood"] == 100


def test_no_wood_no_boat(rules):
    w, a, b, c = strait(rules)
    w["tiles"][b].update(owner=0, building="port")
    w["nations"][0]["resources"]["wood"] = 1
    w["armies"].append({"id": 99, "owner": 0, "tile": a, "n": 3, "dest": None})
    tick.queue(w, rules, 0, {"type": "move", "army": 99, "dest": c})
    tick.apply_orders(w, rules, random.Random(1))
    assert w["armies"][0]["dest"] is None and w["nations"][0]["resources"]["wood"] == 1


def test_a_captured_port_is_wrecked(rules):
    w, a, b, c = strait(rules)
    w["tiles"][b].update(owner=0, building="port")
    for n in w["nations"]:
        n["protected_until"] = 0
    attacker = {"id": 98, "owner": 1, "tile": c, "n": 500, "dest": b}
    w["armies"].append(attacker)
    tick.attack(w, rules, random.Random(1), attacker, b)
    assert w["tiles"][b]["owner"] is None and w["tiles"][b]["building"] is None


# ---------- trade across water ----------

def watery(rules, mode):
    r = dict(rules, trade=dict(rules["trade"], water=mode))
    w, a, b, c = strait(r)
    for nid, k in ((0, a), (1, c)):
        w["nations"][nid]["capital"] = k
        w["tiles"][k].update(owner=nid, terrain="field")
    w["tiles"][a]["building"] = "market"
    w["tiles"][c]["building"] = "market"
    return r, w, b


def test_water_trade_is_open_by_default(rules):
    r, w, b = watery(rules, 0)
    assert trade.water_problem(w, r, 0, 1) is None


def test_water_trade_needs_a_port_on_one_side(rules):
    r, w, b = watery(rules, 1)
    assert "port" in trade.water_problem(w, r, 0, 1)
    w["tiles"][b].update(owner=0, building="port")
    assert trade.water_problem(w, r, 0, 1) is None


def test_water_trade_port_to_port_needs_both(rules):
    from population.hexes import neighbors
    r, w, b = watery(rules, 2)
    w["tiles"][b].update(owner=0, building="port")
    assert "both" in trade.water_problem(w, r, 0, 1)
    other = next(k for k in neighbors(w["nations"][1]["capital"]) if w["tiles"][k]["terrain"] == "water")
    w["tiles"][other].update(owner=1, building="port")
    assert trade.water_problem(w, r, 0, 1) is None


# ---------- steel soldiers ----------

def test_steel_soldiers_need_a_level_two_barracks_and_steel(rules):
    w = two_nations(rules)
    w["tiles"]["-3,0"].update(owner=0, building="barracks")
    assert "level" in tick.steel_problem(w, rules, 0)
    w["tiles"]["-3,0"]["level"] = 2
    assert tick.steel_problem(w, rules, 0) is None
    n = w["nations"][0]
    n["workers"] = 20
    n["resources"].update(food=100, ore=10, steel=3)
    tick.queue(w, rules, 0, {"type": "train", "n": 5, "steel": True})
    tick.apply_orders(w, rules, random.Random(1))
    steel = [a for a in w["armies"] if a["owner"] == 0 and a.get("steel")]
    assert sum(a["n"] for a in steel) == 3 and n["resources"]["steel"] == 0       # only as many as the steel allowed


def test_steel_soldiers_can_be_switched_off(rules):
    off = dict(rules, steel_soldiers=dict(rules["steel_soldiers"], enabled=False))
    w = two_nations(off)
    assert "off" in tick.steel_problem(w, off, 0)


def test_steel_armies_fight_stronger_and_stay_apart(rules):
    plain = {"n": 10, "owner": 0}
    steel = {"n": 10, "owner": 0, "steel": True}
    assert tick.strength(rules, plain) == 10
    assert tick.strength(rules, steel) == 10 * (100 + rules["steel_soldiers"]["bonus_percent"]) // 100
    w = two_nations(rules)
    W.add_army(w, 0, "-3,0", 4)
    W.add_army(w, 0, "-3,0", 4, steel=True)
    assert len([a for a in w["armies"] if a["owner"] == 0 and a["tile"] == "-3,0"]) == 2


def test_upgraded_workshop_has_more_job_slots(rules):
    w, n = refinery_world(rules, "toolworks", wood=500, stone=500, tools=10)
    k = "-3,0"
    assert tick.tile_cap(w, rules, k) == rules["refine_slots"]
    n["resources"].update(wood=500, stone=500)
    tick.queue(w, rules, 0, {"type": "upgrade", "tile": k})
    tick.apply_orders(w, rules, random.Random(1))
    assert W.level_of(w["tiles"][k]) == 2 and tick.tile_cap(w, rules, k) == rules["refine_slots"] + 1


def test_goods_cross_water_faster_by_port(rules):
    r, w, b = watery(rules, 0)
    a, c = w["nations"][0]["capital"], w["nations"][1]["capital"]
    land = r["movement"]["trade_tiles_per_tick"]
    assert trade.speed_between(w, r, a, c) == land                     # no port: no route over water, so the plain speed
    w["tiles"][b].update(owner=0, building="port")
    assert trade.speed_between(w, r, a, c) == land * r["trade"]["sea_speed_percent"] // 100 == 3


def test_armies_take_extra_steps_only_over_own_land(rules):
    w = two_nations(rules)
    rules["movement"]["army_steps_on_own_land"] = 2
    for k in ("-2,0", "-1,0", "0,0"):
        w["tiles"][k].update(owner=0, terrain="field")
    W.add_army(w, 0, "-3,0", 3, dest="0,0")
    tick.move_armies(w, rules, random.Random(1))
    assert w["armies"][-1]["tile"] == "-1,0"          # two tiles over own land
    rules["movement"]["army_steps_on_own_land"] = 1
    w["armies"][-1].update(tile="-3,0", dest="0,0")
    tick.move_armies(w, rules, random.Random(1))
    assert w["armies"][-1]["tile"] == "-2,0"
