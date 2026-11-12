import pytest

from population import world as W
from population.game import Game, GameError, parse_pairs
from population.store import Store


@pytest.fixture
def game(tmp_path):
    g = Game(Store(str(tmp_path / "g.db")))
    g.open_signup("feudalism")
    for i, name in enumerate(["Aldmere", "Brask", "Corvain", "Dunmore"]):
        g.join(100 + i, name)
    g.start(6, seed=5)
    return g


def test_parse_pairs():
    assert parse_pairs("food 5, wood 3") == {"food": 5, "wood": 3}
    with pytest.raises(GameError):
        parse_pairs("food")
    with pytest.raises(GameError):
        parse_pairs("food x")


def test_signup_rules(tmp_path):
    g = Game(Store(str(tmp_path / "g.db")))
    with pytest.raises(GameError):
        g.join(1, "Early")
    g.open_signup("population")
    g.join(1, "Alpha")
    with pytest.raises(GameError):
        g.join(1, "Again")
    with pytest.raises(GameError):
        g.join(2, "alpha")
    with pytest.raises(GameError):
        g.open_signup("nonsense")


def test_status_and_orders(game):
    text = game.status(100)
    assert "Aldmere" in text and "Land: 7" in text
    assert "knights" in text
    game.priorities(100, "food 5, wood 1")
    assert "wood 1" in game.status(100)
    with pytest.raises(GameError):
        game.status(999)


def test_tick_snapshots_and_rollback(game):
    from population import advice
    game.claim(100, sorted(advice.free_frontier(game.world, 0))[0])
    report = game.run_tick()
    assert "Tick 1" in report and game.world["tick"] == 1
    game.rollback(0)
    assert game.world["tick"] == 0
    with pytest.raises(GameError):
        game.rollback(99)


def test_state_survives_restart(game, tmp_path):
    game.run_tick()
    again = Game(Store(str(tmp_path / "g.db")))
    assert again.phase == "running" and again.world["tick"] == 1 and again.players == game.players


def test_titles_only_in_feudalism(game, tmp_path):
    assert set(game.titles().values()) <= {"Lord", "Baron"} and len(game.titles()) == 4
    g = Game(Store(str(tmp_path / "p.db")))
    g.open_signup("population")
    g.join(1, "Alpha")
    g.join(2, "Beta")
    g.start(5, seed=1)
    assert g.titles() == {}


def test_trade_needs_market(game):
    with pytest.raises(GameError):
        game.trade_offer(100, "Brask", "wood 5", "stone 5")


def apply_now(game, key, value):
    """Queue a rule change and run the tick that puts it in force."""
    game.queue_rule(key, value)
    return game.run_tick()


def test_admin_actions(game):
    apply_now(game, "fog", True)
    assert "Standings are hidden" in game.standings()
    apply_now(game, "fog", False)
    assert "Aldmere" in game.standings()
    assert "radius 7" in game.expand(1)
    assert "joined" in game.add_player(555, "Latecomer")
    assert game.nid(555) == 4
    game.end()
    assert game.phase == "over"


def test_rule_change_waits_for_the_tick_and_is_announced(game):
    assert "next tick" in game.queue_rule("scarcity", 400)
    assert game.rules["claim"]["scarcity_percent"] != 400           # nothing yet
    row = next(r for r in game.rule_rows() if r["key"] == "scarcity")
    assert row["next"] == 400 and row["now"] != 400
    report = game.run_tick()
    assert "Rule change: Land price rise (%)" in report and game.rules["claim"]["scarcity_percent"] == 400
    assert game.pending_rules == {}


def test_rule_limits_and_cancel(game):
    with pytest.raises(GameError):
        game.queue_rule("win_share", 1)
    with pytest.raises(GameError):
        game.queue_rule("nonsense", 1)
    game.queue_rule("expiry", 7)
    assert "stays" in game.queue_rule("expiry", game.rules["trade"]["offer_expires_ticks"])
    assert game.pending_rules == {}


def test_hidden_oil_and_market_rules(game):
    apply_now(game, "hidden_oil", True)
    assert game.world["settings"]["hidden_oil"] is True
    apply_now(game, "hidden_oil", False)
    assert game.world["settings"]["hidden_oil"] is False
    apply_now(game, "market", False)
    assert game.rules["trade"]["requires_market"] is False


def test_rules_survive_rollback_and_restart(game, tmp_path):
    apply_now(game, "fog", True)
    apply_now(game, "workers", 6)
    game.rollback(0)                                         # the world goes back, the rules stay
    assert game.world["settings"]["fog"] is True and game.rules["workers_per_tile"] == 6
    game.queue_rule("expiry", 5)
    again = Game(Store(str(tmp_path / "g.db")))
    assert again.rules["workers_per_tile"] == 6 and again.pending_rules == {"expiry": 5}


def test_info_pages(game):
    from population import infopage
    for tab in infopage.TABS:
        text = infopage.as_text(game.info_page(100, tab))
        assert "Aldmere" in text
    overview = infopage.as_text(game.info_page(100, "overview"))
    for part in ("People", "Work", "Stock", "Land"):
        assert part in overview
    game.priorities(100, "food 5, wood 0, stone 0, ore 0, oil 0, livestock 0")
    work = dict((n, v) for n, v, _ in game.info_page(100, "overview")["fields"])["Work"]
    assert "food" in work and "wood" not in work          # nobody works on wood now


def test_priority_buttons_logic(game):
    assert "now 2" in game.set_priority(100, "food", 2)
    assert game.world["nations"][0]["priorities"]["food"] == 2
    with pytest.raises(GameError):
        game.set_priority(100, "food", 9)
    with pytest.raises(GameError):
        game.set_priority(100, "gold", 1)


def test_info_matches_what_the_tick_produces(game):
    from population import tick
    n = game.world["nations"][0]
    plan = tick.work_plan(game.world, game.rules, n)
    before = dict(n["resources"])
    tick.produce(game.world, game.rules, n)
    for res, (_, amount) in plan.items():
        assert n["resources"][res] == before[res] + amount


def test_orders_are_checked_with_helpful_messages(game):
    from population import advice
    w = game.world
    free = sorted(advice.free_frontier(w, 0))[0]
    far = next(k for k, t in w["tiles"].items() if t["owner"] is None and k not in advice.free_frontier(w, 0)
               and t["terrain"] != "water")
    with pytest.raises(GameError, match="isn't on the map"):
        game.claim(100, "99,99")
    with pytest.raises(GameError, match="doesn't touch your land"):
        game.claim(100, far)
    with pytest.raises(GameError, match="already yours"):
        game.claim(100, w["nations"][0]["capital"])
    assert "queued" in game.claim(100, free)
    other = sorted(advice.free_frontier(w, 0) - {free})[0]
    with pytest.raises(GameError, match="already have a claim queued"):
        game.claim(100, other)
    assert "Cleared 1" in game.cancel(100)
    assert "no queued" in game.cancel(100)
    with pytest.raises(GameError, match="isn't yours"):
        game.build(100, far, "home")
    with pytest.raises(GameError, match="Unknown building"):
        game.build(100, free, "castle")
    with pytest.raises(GameError, match="You have no army"):
        game.move(100, 99, free)
    with pytest.raises(GameError, match="isn't on the map"):
        game.move(100, 1, "99,99")


def test_second_order_counts_what_the_first_costs(game):
    from population import advice
    w = game.world
    mine = next(k for k in w["tiles"] if w["tiles"][k]["owner"] == 0 and w["tiles"][k]["building"] is None)
    w["nations"][0]["resources"].update(wood=12, stone=6)
    game.build(100, mine, "home")                          # costs 10 wood, 5 stone
    other = next(k for k in w["tiles"] if w["tiles"][k]["owner"] == 0 and w["tiles"][k]["building"] is None and k != mine)
    with pytest.raises(GameError, match="Not enough"):
        game.build(100, other, "home")


def test_training_limits_are_explained(game):
    game.world["nations"][0]["resources"]["ore"] = 0
    with pytest.raises(GameError, match="short of"):
        game.train(100, 3)
    game.world["nations"][0]["resources"]["ore"] = 50
    assert "queued" in game.train(100, 2)


def test_advice_names_real_problems(game):
    from population import advice
    w = game.world
    w["nations"][0]["resources"]["food"] = 0
    w["nations"][0]["priorities"]["food"] = 0
    text = " ".join(advice.advice(w, game.rules, 0, limit=10))
    assert "Food runs out" in text
    w["nations"][0]["workers"] = W.housing(w, game.rules, 0)[0]
    assert "Homes are full" in " ".join(advice.advice(w, game.rules, 0, limit=10))


def test_report_and_history_after_a_night(game):
    from population import advice
    assert "No night has passed" in game.report(100)
    game.run_tick()
    game.run_tick()
    text = game.report(100)
    assert "night 2" in text and "Stock:" in text
    rows = advice.history(game.world, 0)
    assert [r["t"] for r in rows] == [1, 2]
    assert len(advice.sparkline([1, 2, 3, 2])) == 4


def test_race_line_and_public_events(game):
    standings = game.standings()
    assert "To win:" in standings and "Leader" in standings
    game.run_tick()
    assert isinstance(game.public_events(), list)
    quiet = game.run_tick(split_log=True)
    assert "Tick 2" in quiet and "To win" in quiet


def test_help_pages_and_welcome(game):
    from population import guide
    for tab in guide.HELP_TABS:
        text = game.help(tab)
        assert 0 < len(text) < 1800                        # fits in one Discord message
    assert "peasant" in game.help("basics") and "Emperor" in game.help("winning")
    assert "Aldmere" in game.welcome("Aldmere")
    msgs = game.start_messages()
    assert set(msgs) == {"100", "101", "102", "103"} and "/zoom" in msgs["100"]


def test_tutorial_walks_through_the_basics():
    from population import advice
    from population.tutorial import Tutorial
    t = Tutorial(555, seed=3)
    g = t.game
    assert "Step 1" in t.hint()
    g.claim(555, sorted(advice.free_frontier(g.world, 0))[0])
    assert "Orders queued" in t.hint()
    t.night()
    assert "Step 2" in t.hint()
    mine = next(k for k in t.land() if g.world["tiles"][k]["building"] is None)
    g.world["nations"][0]["resources"].update(wood=50, stone=50)
    g.build(555, mine, "home")
    t.night()
    assert "Step 3" in t.hint()
    g.set_priority(555, "wood", 4)
    assert "Step 4" in t.hint()
    t.night()
    t.night()
    assert "Tutorial complete" in t.hint()
    assert g.world["tick"] == 4 and t.game.world["nations"][1]["alive"]


def test_pacts_are_records_only(game):
    msg = game.pact_propose(100, "Brask", "no attacks until night 30")
    assert "nothing enforces" in msg
    with pytest.raises(GameError):
        game.pact_accept(102, 1)                      # not for Corvain
    game.pact_accept(101, 1)
    assert "Pact 1 with Brask" in game.status(100) and "Pact 1 with Aldmere" in game.status(101)
    assert "Pact 1" in game.pact_list(100)
    game.pact_break(100, 1)                           # allowed, and it is recorded
    assert game.world["pacts"][0]["status"] == "broken" and game.world["pacts"][0]["broken_by"] == 0
    with pytest.raises(GameError):
        game.pact_break(100, 1)
    game.run_tick()
    assert any("broken" in e and "Aldmere" in e for e in game.public_events())
    assert game.pact_list(100) == "You have no pacts. Propose one with /pact propose."


def test_pact_input_checks(game):
    for bad in ("", "x" * 200):
        with pytest.raises(GameError):
            game.pact_propose(100, "Brask", bad)
    with pytest.raises(GameError):
        game.pact_propose(100, "Aldmere", "self pact")
    with pytest.raises(GameError):
        game.pact_propose(100, "Nobody", "hello")
    game.pact_propose(100, "Brask", "peace")
    game.pact_decline(100, 1)
    assert game.world["pacts"][0]["status"] == "withdrawn"


def test_open_offer_status_and_line(game):
    for nid in (0, 1):
        t = next(k for k, v in game.world["tiles"].items() if v["owner"] == nid and v["building"] is None)
        game.world["tiles"][t]["building"] = "market"
    tid, text = game.make_offer(100, "", "wood 5", "stone 2")
    assert "anyone with a Market" in text and "anyone with a Market" in game.offer_line(trade_find(game, tid))
    assert "Offer %d from Aldmere" % tid in game.status(101)
    with pytest.raises(GameError):
        game.trade_decline(101, tid)                  # only the sender can withdraw an open offer
    game.trade_decline(100, tid)
    assert "Offer %d from" % tid not in game.status(101)
    with pytest.raises(GameError):
        game.make_offer(100, "", "", "")


def trade_find(game, tid):
    from population import trade
    return trade.find(game.world, tid)


def test_private_offer_needs_a_named_nation(game):
    for nid in (0, 1):
        t = next(k for k, v in game.world["tiles"].items() if v["owner"] == nid and v["building"] is None)
        game.world["tiles"][t]["building"] = "market"
    with pytest.raises(GameError):
        game.make_offer(100, "", "wood 5", "", private=True)
    tid, text = game.make_offer(100, "Brask", "wood 5", "", private=True)
    assert "Private" in text and trade_find(game, tid)["private"]
    assert "(private)" in game.offer_line(trade_find(game, tid))
    assert "Offer %d from Aldmere" % tid in game.status(101)
    assert "Offer %d from" % tid not in game.status(102)


def test_help_works_before_any_game(tmp_path):
    from population.game import Game
    from population.store import Store
    fresh = Game(Store(str(tmp_path / "x.db")))
    assert fresh.rules is None and "peasant" in fresh.help("basics")


def test_economy_lists_only_what_your_land_makes(game):
    from population import infopage
    n = game.world["nations"][0]
    mine = [k for k in game.world["tiles"] if game.world["tiles"][k]["owner"] == 0]
    for k in mine:                                           # make everything you own plain field
        if not game.world["tiles"][k]["building"]:
            game.world["tiles"][k].update(terrain="field", oil=False)
    can = infopage.available(game.world, game.rules, 0)
    assert set(can) == {"food", "livestock"}
    eco = dict((a, b) for a, b, _ in game.info_page(100, "economy")["fields"])["Priorities"]
    assert "food" in eco and "wood" not in eco
    land = dict((a, b) for a, b, _ in game.info_page(100, "land")["fields"])
    assert "What your land can make" in land and "Stock" in land
    assert "food" in land["What your land can make"] and "wood" not in land["What your land can make"]
