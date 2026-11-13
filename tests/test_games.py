import time

import pytest

from population import quiet, stats
from population.game import GameError
from population.games import Games, NoGame, ThisServer, check_setting, current_server, remember_server, settings
from population.store import Store


def test_each_server_gets_its_own_game_file(tmp_path):
    games = Games(str(tmp_path))
    assert games.get(1) is None and games.get("") is None and games.get("abc") is None and games.ids() == []
    a = games.create(1)
    assert games.create(1) is a                               # asking again gives the same game
    b = games.create(2)
    a.open_signup("feudalism")
    assert b.phase == "none" and games.get(2).phase == "none"      # one server's game doesn't touch another's
    assert games.ids() == [1, 2] and (tmp_path / "1.db").exists()
    again = Games(str(tmp_path))                              # after a restart the game is still there
    assert again.get(1).phase == "signup"


def test_the_cap_stops_new_games_but_not_existing_ones(tmp_path):
    games = Games(str(tmp_path), max_games=2)
    games.create(1), games.create(2)
    with pytest.raises(GameError, match="limit of 2"):
        games.create(3)
    assert games.create(2) is games.get(2)


def test_this_server_stand_ins_follow_the_current_server(tmp_path):
    games = Games(str(tmp_path))
    games.create(1).open_signup("feudalism")
    games.create(2)
    game, store = ThisServer(games, "game"), ThisServer(games, "store")
    token = current_server.set(1)
    assert game.phase == "signup"
    store.set("x", 5)
    current_server.set(2)
    assert game.phase == "none" and store.get("x") is None
    current_server.set(3)                                     # a server with no game
    with pytest.raises(NoGame, match="/admin open"):
        game.phase
    current_server.set(None)                                  # a direct message
    with pytest.raises(NoGame, match="in a server"):
        store.get("x")
    current_server.reset(token)


def test_every_interaction_runs_with_its_server_set():
    seen = []

    class State:
        parsers = {"INTERACTION_CREATE": lambda data: seen.append(current_server.get())}

    remember_server(State)
    State.parsers["INTERACTION_CREATE"]({"guild_id": "42"})
    State.parsers["INTERACTION_CREATE"]({})                   # a DM has no guild
    assert seen == [42, None]


def test_settings_override_the_defaults_and_are_checked(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    defaults = {"tick_time": "02:00", "admin_role": "admin"}
    assert settings(store, defaults) == defaults
    store.set("settings", {"tick_time": "04:30"})
    assert settings(store, defaults) == {"tick_time": "04:30", "admin_role": "admin"}
    assert check_setting("tick_time", "04:30") == "04:30" and check_setting("dm_reports", "off") == "0"
    assert check_setting("tick_minutes", "") == "" and check_setting("tick_tz", "America/Denver")
    for name, bad in [("tick_time", "25:00"), ("tick_tz", "Mars/Base"), ("tick_minutes", "0"), ("dm_reports", "maybe")]:
        with pytest.raises(GameError):
            check_setting(name, bad)


def test_the_updater_waits_if_any_one_game_is_busy(tmp_path):
    (tmp_path / ".env").write_text("TICK_TZ=America/Denver\n")
    games = Games(str(tmp_path / "games"))
    for sid in (1, 2):
        g = games.create(sid)
        g.store.set("last_tick", time.time() - 3 * 3600)
        g.store.set("last_activity", time.time() - 3600)
    near = quiet.busy_reason(time.time(), 0, time.time() - 3 * 3600, 24, "02:00", "America/Denver") is not None
    if not near:
        assert quiet.main(str(tmp_path)) == 0
    games.get(2).store.set("last_activity", time.time())       # someone is playing in server 2
    assert quiet.main(str(tmp_path)) == 1


def test_stats_describe_the_games_and_warn_when_full(tmp_path):
    games = Games(str(tmp_path), max_games=2)
    a = games.create(10)
    a.store.set("server_name", "Test Server")
    a.store.set("last_tick_seconds", 90)
    games.create(11)
    stats.write_bot_file(str(tmp_path), 7)
    info = stats.gather(games)
    assert [r["id"] for r in info["games"]] == [10, 11] and info["bot"]["servers"] == 7
    out = stats.text(info)
    assert "Games: 2 of 2" in out and "Test Server (10)" in out and "Servers the bot is in: 7" in out
    problems = " ".join(stats.warnings(info))
    assert "game limit" in problems and "90 seconds" in problems


def test_factory_reset_erases_a_servers_game(tmp_path):
    from population.games import Games
    gs = Games(str(tmp_path / "games"))
    g = gs.create(5)
    g.open_signup("population")
    g.store.set("settings", {"tick_time": "05:00"})
    gs.factory_reset(5)
    assert gs.get(5) is None and gs.ids() == []
    fresh = gs.create(5)
    assert fresh.phase != "running" and fresh.store.get("settings", {}) == {}
