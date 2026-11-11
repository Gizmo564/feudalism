import pytest

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
    game.claim(100, "0,0")
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


def test_admin_actions(game):
    assert "on" in game.set_fog(True)
    assert "Standings are hidden" in game.standings()
    game.set_fog(False)
    assert "Aldmere" in game.standings()
    assert "radius 7" in game.expand(1)
    assert "joined" in game.add_player(555, "Latecomer")
    assert game.nid(555) == 4
    game.end()
    assert game.phase == "over"


def test_set_empire(game):
    assert "30%" in game.set_empire(30)
    assert game.rules["victory"]["land_share_percent"] == 30
    with pytest.raises(GameError):
        game.set_empire(1)


def test_hidden_oil_command(game):
    assert "hidden" in game.set_hidden_oil(True)
    assert game.world["settings"]["hidden_oil"] is True
    assert "visible" in game.set_hidden_oil(False)
