"""Is the bot quiet right now? The automatic updater asks this before it restarts the bot.
Run it as `python -m population.quiet`: it exits 0 when quiet, 1 (and says why) when it is busy."""
import os
import sys
import time

from . import schedule
from .store import Store


def busy_reason(now, last_activity, last_tick, hours, tick_time, tz, test_minutes="", quiet_minutes=10, tick_margin=10):
    """Why it is a bad moment to restart, or None. Bad moments: someone used the bot in the last quiet_minutes,
    or a tick ran in the last tick_margin minutes or is due within the next tick_margin minutes."""
    if now - last_activity < quiet_minutes * 60:
        return "someone used the bot in the last %d minutes" % quiet_minutes
    if now - last_tick < tick_margin * 60:
        return "a tick just ran"
    if schedule.is_due(now + tick_margin * 60, last_tick, hours, tick_time, tz, test_minutes):
        return "a tick is due within %d minutes" % tick_margin
    return None


def read_env(path):
    """KEY=VALUE lines of the .env file (no quotes or exports; that is all the bot's own file ever holds)."""
    out = {}
    if os.path.exists(path):
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                out[k] = v
    return out


def main(app_dir):
    env = read_env(os.path.join(app_dir, ".env"))
    db = env.get("DB_PATH", "game.db")
    store = Store(db if os.path.isabs(db) else os.path.join(app_dir, db))
    rules = store.get("rules") or {}
    why = busy_reason(
        time.time(), store.get("last_activity", 0), store.get("last_tick", 0), rules.get("tick_hours", 24),
        env.get("TICK_TIME", "02:00"), env.get("TICK_TZ", "America/Denver"), env.get("TICK_MINUTES", "").strip(),
        int(env.get("AUTO_UPDATE_QUIET_MINUTES", "10") or 10))
    if why:
        print("busy: " + why)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
