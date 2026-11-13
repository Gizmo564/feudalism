"""Win checks. Thresholds are shares of the whole world, so they scale with map size and player count."""
from . import world as W


def shares(world, rules):
    alive = [n for n in world["nations"] if n["alive"]]
    land_total = sum(1 for t in world["tiles"].values() if t["terrain"] != "water") or 1
    pop = {n["id"]: W.people(world, n["id"]) for n in alive}
    stk = {n["id"]: W.stock(n) for n in alive}
    pop_total = sum(pop.values()) or 1
    stk_total = sum(stk.values()) or 1
    made = {n["id"]: n.get("made", 0) for n in alive}            # factory output tonight (Industrialism)
    made_total = sum(made.values()) or 1
    out = {}
    for n in alive:
        i = n["id"]
        out[i] = {"land": 100 * len(W.owned(world, i)) // land_total,
                  "population": 100 * pop[i] // pop_total,
                  "stock": 100 * stk[i] // stk_total,
                  "stock_amount": stk[i],
                  "output": 100 * made[i] // made_total,
                  "output_amount": made[i]}
    return out


def land_threshold(world, rules):
    """Land share (percent) needed to win. It can fall as the game goes on so a game always finishes:
    it starts at land_share_percent, drops land_share_decay_per_tick points each tick after
    land_share_decay_start, and never goes below land_share_floor_multiple x a fair share (but never above the starting value)."""
    v = rules["victory"]
    pct = v["land_share_percent"]
    decay = v.get("land_share_decay_per_tick", 0)
    if pct is None or not decay:
        return pct
    late = max(0, world["tick"] - v.get("land_share_decay_start", 0))
    floor = v.get("land_share_floor_multiple", 1) * 100 // max(1, len(world["nations"]))
    return min(pct, max(floor, pct - decay * late))


def check(world, rules):
    """Update hold counters. Returns (nation id, reason) when someone has won, else None."""
    v = rules["victory"]
    alive = [n for n in world["nations"] if n["alive"]]
    if v["last_nation_standing"] and len(alive) == 1 and len(world["nations"]) > 1:
        return alive[0]["id"], "last nation standing"
    sh = shares(world, rules)
    need = {"land": land_threshold(world, rules), "population": v["population_share_percent"],
            "stock": v["stock_share_percent"], "output": v.get("output_share_percent")}
    winner = None
    for n in alive:
        for metric, pct in need.items():
            if pct is None:
                continue
            ok = sh[n["id"]][metric] >= pct
            if metric == "stock":
                ok = ok and sh[n["id"]]["stock_amount"] >= v["stock_min"]
            if metric == "output":
                ok = ok and sh[n["id"]]["output_amount"] >= v.get("output_min", 1)
            n["hold"][metric] = n["hold"].get(metric, 0) + 1 if ok else 0
            if winner is None and n["hold"][metric] >= v["hold_ticks"]:
                winner = (n["id"], "%d%% of the world's %s held for %d ticks" % (pct, metric, v["hold_ticks"]))
    return winner


def standings(world, rules):
    """Everyone's land and population, best first. Used for admin /end and the final report."""
    rows = []
    for n in world["nations"]:
        rows.append({"nation": n["id"], "name": n["name"], "alive": n["alive"],
                     "land": len(W.owned(world, n["id"])), "population": W.people(world, n["id"])})
    return sorted(rows, key=lambda r: (-r["land"], -r["population"]))


def end_game(world, rules):
    """Admin ending: the leader on land (then population) wins."""
    top = [r for r in standings(world, rules) if r["alive"]][0]
    world["winner"] = {"nation": top["nation"], "reason": "game ended by an admin", "tick": world["tick"]}
    return standings(world, rules)
