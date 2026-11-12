"""How many nights until a nation owns one of every building? Used to tune building prices.

    python3 pacing.py 12 8       # 12 players, and a stand-in for trading: up to 8 of a spare resource swapped a night

The bot builds the next missing kind each night, claims land toward what it is short of, and trades the way
a modest player would. Aim: a median around 14 nights. Nations that can never get a resource (no trade) show as "never".
"""
TRADE = 0
import sys, statistics
from population import ruleset, world as W, tick, bot

def run(rules, players, radius, seed, nights=60):
    w = W.new_world(rules, ["N%d" % i for i in range(players)], radius, seed)
    want = list(rules["buildings"])
    done = {}
    for night in range(1, nights + 1):
        for n in w["nations"]:
            nid = n["id"]
            if not n["alive"]: continue
            res = n["resources"]
            if TRADE:   # stand-in for trading: swap up to TRADE of a surplus (food, livestock first) for something short, 1 for 1
                for b in want:
                    if b in {t["building"] for t in w["tiles"].values() if t["owner"] == nid}: continue
                    for r, v in tick.build_cost(w, rules, nid, b).items():
                        short = v - res.get(r, 0)
                        if short > 0:
                            for give in ("food", "livestock", "ore", "stone", "wood"):
                                if give == r: continue
                                spare = res.get(give, 0) - (40 if give == "food" else 20)
                                amt = min(TRADE, short, spare)
                                if amt > 0:
                                    res[give] -= amt; res[r] = res.get(r, 0) + amt; break
                    break
            # build the next missing kind (cheapest-to-afford order as listed), one per night
            have = {t["building"] for t in w["tiles"].values() if t["owner"] == nid}
            queued = {o["building"] for o in w["orders"] if o["nation"] == nid and o["type"] == "build"}
            for b in want:
                if b in have or b in queued: continue
                cost = tick.build_cost(w, rules, nid, b)
                free = [k for k in W.owned(w, nid) if w["tiles"][k]["building"] is None and w["tiles"][k]["terrain"] != "water"]
                if free and tick.can_pay(res, cost):
                    tick.queue(w, rules, nid, {"type": "build", "tile": free[-1], "building": b}); break
            front = bot.frontier(w, nid)
            need = None
            for b in want:
                if b not in have:
                    need = tick.build_cost(w, rules, nid, b); break
            claim = tick.claim_cost(w, rules, nid)
            lack = [r for r, v in (need or {}).items() if res.get(r, 0) < v]
            def score(k):
                t = w["tiles"][k]["terrain"]
                prod = rules["terrain"][t]["produces"]
                return (0 if any(r in lack for r in prod) else 1, bot.distance(k, n["capital"]), k)
            after = {r: res.get(r, 0) - claim.get(r, 0) for r in set(res) | set(claim)}
            if front and tick.can_pay(res, claim) and (need is None or tick.can_pay(after, need) or lack):
                front.sort(key=score)
                try: tick.queue(w, rules, nid, {"type": "claim", "tile": front[0]})
                except Exception: pass
            if W.soldiers(w, nid) < 6 + 0:
                pass
        tick.run_tick(w, rules)
        for n in w["nations"]:
            nid = n["id"]
            if nid in done: continue
            have = {t["building"] for t in w["tiles"].values() if t["owner"] == nid}
            if all(b in have for b in want): done[nid] = night
    return done, w

if __name__ == "__main__":
    rules = ruleset.load("rules/population.json")
    players = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    TRADE = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    radius = W.default_radius(players)
    allv, miss = [], 0
    for seed in range(1, 9):
        d, w = run(rules, players, radius, seed)
        allv += list(d.values()); miss += len(w["nations"]) - len(d)
    allv.sort()
    print("players", players, "radius", radius, "finished", len(allv), "never", miss)
    if allv: print("median", statistics.median(allv), "p25", allv[len(allv)//4], "p75", allv[3*len(allv)//4])
