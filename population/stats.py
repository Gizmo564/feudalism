"""How busy is the bot's box? Run `python -m population.stats` (or `feudalism games`).
The bot writes games/_bot.json every few minutes with what only it knows: servers joined and its own memory use."""
import json
import os
import shutil
import sys
import time

from .games import Games
from .quiet import read_env


def bot_memory_mb():
    "Resident memory of this process, from /proc (Linux only)."
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def write_bot_file(folder, servers):
    path = os.path.join(folder, "_bot.json")
    with open(path + ".tmp", "w") as f:
        json.dump({"at": time.time(), "servers": servers, "memory_mb": bot_memory_mb()}, f)
    os.replace(path + ".tmp", path)


def memory_total_available_mb():
    got = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            got[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return got.get("MemTotal", 0), got.get("MemAvailable", 0)


def gather(games, now=None):
    now = now or time.time()
    rows = []
    for server_id, g in games.all():
        rows.append({"id": server_id, "name": g.store.get("server_name", "?"), "phase": g.phase,
                     "tick": g.world["tick"] if g.world else 0, "players": len(g.players),
                     "tick_seconds": g.store.get("last_tick_seconds", 0), "mb": os.path.getsize(games.path(server_id)) / 1e6,
                     "last_activity": g.store.get("last_activity", 0)})
    bot = {}
    try:
        bot = json.load(open(os.path.join(games.folder, "_bot.json")))
    except (OSError, ValueError):
        pass
    total, free = memory_total_available_mb()
    disk = shutil.disk_usage(games.folder)
    return {"games": rows, "cap": games.max_games, "bot": bot, "now": now, "ram_total": total, "ram_free": free,
            "disk_free_gb": disk.free / 1e9, "disk_total_gb": disk.total / 1e9,
            "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else 0, "cores": os.cpu_count() or 1}


def warnings(info):
    "Plain-language reasons to think about a bigger box. Empty means plenty of room."
    out = []
    n = len(info["games"])
    if n >= info["cap"]:
        out.append("At the game limit (%d). Raise MAX_GAMES if the box can take more." % info["cap"])
    if info["ram_total"] and info["ram_free"] < 0.15 * info["ram_total"]:
        out.append("Memory is getting tight: %d MB free of %d MB." % (info["ram_free"], info["ram_total"]))
    if info["load"] > info["cores"]:
        out.append("The CPU is busier than its %d cores (load %.1f)." % (info["cores"], info["load"]))
    slow = max([r["tick_seconds"] for r in info["games"]] or [0])
    if slow > 60:
        out.append("A tick took %d seconds. Many games ticking at once will queue up." % slow)
    if info["disk_total_gb"] and info["disk_free_gb"] < 0.1 * info["disk_total_gb"]:
        out.append("Disk is almost full: %.0f GB free." % info["disk_free_gb"])
    return out


def text(info):
    lines = ["**Games: %d of %d allowed**" % (len(info["games"]), info["cap"])]
    bot = info["bot"]
    old = info["now"] - bot.get("at", 0) > 900
    if bot and not old:
        lines.append("Servers the bot is in: %d. Bot memory: %d MB." % (bot["servers"], bot["memory_mb"]))
    else:
        lines.append("The bot has not reported lately (is it running?).")
    lines.append("Box: memory %d MB free of %d, load %.1f on %d cores, disk %.0f GB free of %.0f." % (
        info["ram_free"], info["ram_total"], info["load"], info["cores"], info["disk_free_gb"], info["disk_total_gb"]))
    for r in info["games"]:
        quiet = int((info["now"] - r["last_activity"]) / 3600) if r["last_activity"] else None
        lines.append("- %s (%d): %s, night %d, %d players, tick took %ds, %.1f MB, %s" % (
            r["name"], r["id"], r["phase"], r["tick"], r["players"], r["tick_seconds"], r["mb"],
            "no use yet" if quiet is None else "last used %dh ago" % quiet))
    problems = warnings(info)
    lines += ["", "Time to look at a bigger box or a second one:"] + ["- " + p for p in problems] if problems else \
        ["", "Plenty of room."]
    return "\n".join(lines)


if __name__ == "__main__":
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = read_env(os.path.join(app, ".env"))
    folder = env.get("GAMES_DIR", "games")
    print(text(gather(Games(folder if os.path.isabs(folder) else os.path.join(app, folder), int(env.get("MAX_GAMES", "20") or 20)))))
