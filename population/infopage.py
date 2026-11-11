"""The /info page as plain data (title, description, fields, footer), one dict per tab.
The Discord bot turns each dict into an embed. No Discord code here."""
from . import advice, render, tick, victory, views, world as W

TABS = ("overview", "economy", "land", "military")
ICON = {"food": "\U0001f33e", "wood": "\U0001fab5", "stone": "\U0001faa8", "ore": "⛏️",
        "oil": "\U0001f6e2️", "livestock": "\U0001f404"}
SQUARE = {"food": "\U0001f7e9", "wood": "\U0001f7eb", "stone": "⬜", "ore": "\U0001f7e6",
          "oil": "⬛", "livestock": "\U0001f7e8"}
WIDTH = 10


def icon(res):
    return ICON.get(res, "•")


def bar(value, cap, width=WIDTH):
    filled = min(width, round(width * value / cap)) if cap > 0 else 0
    return "█" * filled + "░" * (width - filled)


def stacked(plan, workers):
    """One row of coloured squares showing how the workforce is split (largest remainder rounding).
    Idle workers show as empty squares."""
    if not workers or not plan:
        return "\u2591" * WIDTH
    raw = {r: WIDTH * c / workers for r, (c, _) in plan.items()}
    cells = {r: int(x) for r, x in raw.items()}
    target = round(sum(raw.values()))
    for r in sorted(raw, key=lambda r: raw[r] - cells[r], reverse=True)[: max(0, target - sum(cells.values()))]:
        cells[r] += 1
    out = "".join(SQUARE.get(r, "\u2b1c") * cells[r] for r in sorted(cells, key=lambda r: -plan[r][0]))
    return out + "\u2591" * (WIDTH - sum(cells.values()))


def pct(part, whole):
    return "%d%%" % (100 * part // whole) if whole else "0%"


def dots(value, top=5):
    return "●" * value + "○" * (top - value)


def trend_text(world, rules, nid):
    """Food change vs last night, and a population sparkline."""
    rows = advice.history(world, nid)
    parts = []
    net, _ = advice.food_balance(world, rules, nid)
    if len(rows) >= 2:
        parts.append("Food %+d a night (was %+d)" % (net, rows[-2]["food_net"]))
    else:
        parts.append("Food %+d a night" % net)
    if len(rows) >= 2:
        parts.append("Population %s %d" % (advice.sparkline([r["pop"] for r in rows]), rows[-1]["pop"]))
    return " | ".join(parts)


def page(world, rules, nid, tab="overview", resource="food"):
    n = world["nations"][nid]
    words = rules.get("terms", {})
    worker, soldier = words.get("worker", "worker"), words.get("soldier", "soldier")
    title = n["name"] + (" (%s)" % views.rank_title(world, rules, nid) if views.rank_title(world, rules, nid) else "")
    out = {"title": "%s - tick %d" % (title, world["tick"]), "color": render.nation_colours(nid)[0],
           "description": "", "fields": [], "footer": ""}
    if not n["alive"]:
        out["description"] = "Your nation has fallen. You are now a spectator."
        return out
    hw, hs = W.housing(world, rules, nid)
    soldiers = W.soldiers(world, nid)
    people = n["workers"] + soldiers
    need = people * rules["eat_per_person"]
    plan = tick.work_plan(world, rules, n)
    income = {r: plan.get(r, (0, 0))[1] for r in rules["resources"]}
    mine = W.owned(world, nid)
    land_total = sum(1 for t in world["tiles"].values() if t["terrain"] != "water")
    threshold = victory.land_threshold(world, rules)
    race = advice.race(world, rules, nid)

    def stock_lines():
        return "\n".join("%s %s **%d**%s" % (icon(r), r, n["resources"].get(r, 0),
                                              " (+%d)" % income[r] if income[r] else "") for r in rules["resources"])

    if tab == "overview":
        out["description"] = "\n".join("• " + line for line in advice.advice(world, rules, nid))
        out["fields"] = [
            ("People", "%s\n`%s` %d/%d\n%s\n`%s` %d/%d\nTotal %d, eating %d %s a night" % (
                worker.capitalize(), bar(n["workers"], hw), n["workers"], hw, soldier.capitalize(),
                bar(soldiers, hs), soldiers, hs, people, need, icon("food")), True),
            ("Work", "%s\n%s" % (stacked(plan, n["workers"]), "\n".join(
                "%s %s %d (%s)" % (icon(r), r, c, pct(c, n["workers"]))
                for r, (c, _) in sorted(plan.items(), key=lambda x: -x[1][0])) or "Nobody is working."), True),
            ("Stock (tonight)", stock_lines(), True),
            ("Land", "**%d** tiles, %s of all land\n`%s` toward the win bar (%s)" % (
                len(mine), pct(len(mine), land_total), bar(100 * len(mine) // max(1, land_total), threshold or 100),
                "%d%%" % threshold if threshold else "n/a"), True),
        ]
        out["footer"] = trend_text(world, rules, nid) or (race[0] if race else "")
    elif tab == "economy":
        out["description"] = ("Pick a resource below, then press - or + to change how many %ss work on it "
                              "(0 turns it off, 5 is top). Only the proportions matter." % worker)
        out["fields"] = [
            ("Priorities", "\n".join("%s %s `%s` %d%s" % (icon(r), r, dots(n["priorities"][r]), n["priorities"][r],
                                                        "  ◀" if r == resource else "") for r in rules["resources"]), True),
            ("Output tonight", "\n".join("%s %s +%d (%d %ss)" % (icon(r), r, income[r], plan[r][0], worker)
                                          for r in rules["resources"] if r in plan) or "Nothing.", True),
        ]
        net, nights = advice.food_balance(world, rules, nid)
        balance = "%s Food %+d a night" % (icon("food"), net)
        balance += "" if nights is None else ", lasts about %d night%s" % (nights, "" if nights == 1 else "s")
        out["fields"].append(("Balance", balance + "\n" + (trend_text(world, rules, nid) or "Trends appear after the first night."), False))
        out["fields"].append(("Stock", stock_lines(), False))
    elif tab == "land":
        terrain, buildings = {}, {}
        for k in mine:
            t = world["tiles"][k]
            terrain[t["terrain"]] = terrain.get(t["terrain"], 0) + 1
            if t["building"]:
                buildings[t["building"]] = buildings.get(t["building"], 0) + 1
        oil = sum(1 for k in mine if W.oil_shown(world, world["tiles"][k]))
        cost = tick.claim_cost(world, rules, nid)
        out["description"] = "**%d** tiles = %s of all land. To win you need %s." % (
            len(mine), pct(len(mine), land_total), "%d%%" % threshold if threshold else "n/a")
        out["fields"] = [
            ("Terrain", "\n".join("%s: %d" % (k, v) for k, v in sorted(terrain.items(), key=lambda x: -x[1])), True),
            ("Buildings", "\n".join("%s: %d" % (k, v) for k, v in sorted(buildings.items())) or "none", True),
            ("Housing", "%ss %d/%d\n%ss %d/%d" % (worker, n["workers"], hw, soldier, soldiers, hs), True),
            ("Claiming", "Next claim: %s\nFree tiles you can claim: %d\nOil deposits found: %d" % (
                advice.fmt(cost), len(advice.free_frontier(world, nid)), oil), False),
        ]
    else:  # military
        away = advice.abroad(world, nid)
        need_oil = advice.oil_needed(world, rules, nid)
        armies = [a for a in world["armies"] if a["owner"] == nid]
        lines = []
        for a in armies:
            home = world["tiles"][a["tile"]]["owner"] == nid
            lines.append("Army %d: %d at %s%s%s" % (a["id"], a["n"], a["tile"], " -> " + a["dest"] if a["dest"] else "",
                                                      "" if home else " (abroad)"))
        out["description"] = "%ss: **%d** (room for %d). Training one costs %s and a %s." % (
            soldier.capitalize(), soldiers, hs, advice.fmt(rules["soldier"]["train_cost"]), worker)
        out["fields"] = [
            ("Armies", "\n".join(lines) or "None. Use /train, then send them out.", False),
            ("Oil upkeep", "%s Armies on your own land cost nothing. Armies abroad cost 1 oil per %d %ss a night.\n"
                           "Tonight: %d oil needed, you have %d.%s" % (
                icon("oil"), rules["oil_upkeep"]["soldiers_per_oil"], soldier, need_oil, n["resources"].get("oil", 0),
                "" if n["resources"].get("oil", 0) >= need_oil else " **Shortfall: they will lose soldiers.**"), False),
        ]
    if tab != "overview" and race:
        out["footer"] = race[0]
    return out


def as_text(p):
    """The same page as plain text (for tests and for places that can't show embeds)."""
    lines = ["**%s**" % p["title"], p["description"]]
    for name, value, _ in p["fields"]:
        lines += ["**%s**" % name, value]
    lines.append(p["footer"])
    return "\n".join(x for x in lines if x)
