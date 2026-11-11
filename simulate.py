"""Run seeded games with scripted bots and print a balance report.

    python3 simulate.py                      # 16 players, radius 8, 75 ticks, 5 seeds
    python3 simulate.py --players 25 --radius 9 --seeds 10 --rules rules/population.json
"""
import argparse
import random
from collections import Counter

from population import ruleset, world as W, tick, victory, bot


def play(rules, players, radius, ticks, seed, mix):
    w = W.new_world(rules, ["N%d" % i for i in range(players)], radius, seed)
    prng = random.Random(seed)
    aggression = {i: mix[prng.randrange(len(mix))] for i in range(players)}
    for t in range(ticks):
        if w["winner"]:
            break
        for n in w["nations"]:
            if n["alive"]:
                bot.act(w, rules, n["id"], aggression[n["id"]], random.Random(seed * 997 + t * 31 + n["id"]))
        tick.run_tick(w, rules)
    return w, aggression


def summary(w, rules):
    alive = [n for n in w["nations"] if n["alive"]]
    land = sum(1 for t in w["tiles"].values() if t["terrain"] != "water")
    claimed = sum(1 for t in w["tiles"].values() if t["terrain"] != "water" and t["owner"] is not None)
    top = max(alive, key=lambda n: len(W.owned(w, n["id"]))) if alive else None
    return {
        "ticks": w["tick"], "winner": w["winner"], "alive": len(alive),
        "eliminated": len(w["nations"]) - len(alive),
        "land_claimed_pct": 100 * claimed // land,
        "top_land": len(W.owned(w, top["id"])) if top else 0,
        "avg_pop": sum(W.people(w, n["id"]) for n in alive) // max(1, len(alive)),
        "max_pop": max((W.people(w, n["id"]) for n in alive), default=0),
        "starved_out_by_tick_10": None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=16)
    ap.add_argument("--radius", type=int, default=8)
    ap.add_argument("--ticks", type=int, default=75)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--rules", default="rules/population.json")
    ap.add_argument("--mix", default="0,0.3,0.6", help="comma list of bot aggression levels to mix")
    args = ap.parse_args()
    rules = ruleset.load(args.rules)
    mix = [float(x) for x in args.mix.split(",")]
    reasons = Counter()
    print("players=%d radius=%d ticks=%d mix=%s" % (args.players, args.radius, args.ticks, mix))
    for seed in range(1, args.seeds + 1):
        w, _ = play(rules, args.players, args.radius, args.ticks, seed, mix)
        s = summary(w, rules)
        reason = s["winner"]["reason"] if s["winner"] else "no winner by tick %d" % args.ticks
        reasons[reason] += 1
        print("seed %2d: ticks=%2d alive=%2d/%d land_claimed=%3d%% top_land=%3d avg_pop=%4d max_pop=%4d | %s"
              % (seed, s["ticks"], s["alive"], args.players, s["land_claimed_pct"], s["top_land"],
                 s["avg_pop"], s["max_pop"], reason))
    print("outcomes:", dict(reasons))


if __name__ == "__main__":
    main()
