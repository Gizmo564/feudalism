import time
from datetime import datetime
from zoneinfo import ZoneInfo

from population import quiet
from population.store import Store

TZ = "America/Denver"


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp()


def reason(now, last_activity, last_tick, **kw):
    return quiet.busy_reason(now, last_activity, last_tick, 24, "02:00", TZ, **kw)


def test_quiet_when_nobody_is_around_and_no_tick_is_near():
    assert reason(at(12, 15), at(12, 9), at(12, 2)) is None


def test_busy_when_someone_just_used_the_bot():
    assert "used the bot" in reason(at(12, 15), at(12, 14, 55), at(12, 2))
    assert reason(at(12, 15), at(12, 14, 52), at(12, 2), quiet_minutes=10) is not None
    assert reason(at(12, 15), at(12, 14, 52), at(12, 2), quiet_minutes=5) is None      # the wait can be shorter


def test_busy_around_the_tick():
    assert "just ran" in reason(at(12, 2, 5), at(12, 0), at(12, 2))
    assert "due within" in reason(at(12, 1, 55), at(12, 0), at(11, 2))            # the 02:00 tick is five minutes away
    assert reason(at(12, 1, 30), at(12, 0), at(11, 2)) is None                    # half an hour away is fine


def test_the_command_exits_by_whether_it_is_quiet(tmp_path):
    (tmp_path / ".env").write_text("TICK_TIME=02:00\nTICK_TZ=%s\n" % TZ)
    (tmp_path / "games").mkdir()
    store = Store(str(tmp_path / "games" / "555.db"))
    store.set("last_tick", time.time() - 3 * 3600)
    store.set("last_activity", time.time())
    assert quiet.main(str(tmp_path)) == 1                              # someone is using it right now
    store.set("last_activity", time.time() - 3600)
    now = datetime.now(ZoneInfo(TZ))
    near_tick = abs((now.hour * 60 + now.minute) - 120) < 15           # skip the check inside the tick's own window
    assert near_tick or quiet.main(str(tmp_path)) == 0
