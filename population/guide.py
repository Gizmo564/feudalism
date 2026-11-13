"""Text that teaches the game: /help pages, the welcome messages, and tutorial hints.
Numbers come from the ruleset, so they stay right when the rules change. No Discord code here."""
from . import advice, victory, world as W

HELP_TABS = ("basics", "resources", "goods", "modes", "war", "winning", "commands")


def claim_text(rules):
    c = rules["claim"]
    if c.get("share_percent"):
        least = ", ".join("%d %s" % (v, k) for k, v in c["share_min"].items() if v)
        return ("Claiming land: %d%% of the wood and %d%% of the stone you hold (and %d%% more for each tile you own), never less than %s. "
                "Land gets dearer as the map fills up and cheaper when it grows." % (
                    c["share_percent"], c.get("share_stone_percent", c["share_percent"]), c.get("share_per_tile", 0), least))
    cost = lambda d: ", ".join("%d %s" % (v, k) for k, v in d.items())
    return ("Claiming land: %s, plus %s for each tile you own. Land gets dearer as the map fills up and cheaper when it grows." % (
        cost(c["base"]), cost(c["per_tile"])))


def words(rules):
    t = rules.get("terms", {})
    return t.get("worker", "worker"), t.get("soldier", "soldier"), t.get("nation", "nation")


def cost(c):
    return advice.fmt(c)


def basics(rules):
    worker, soldier, nation = words(rules)
    s = rules["start"]
    return "\n".join([
        "**How it works**",
        "The game runs once a night. During the day you give orders; at night they all happen together.",
        "Each night: orders run (claims, builds, training), armies move and fight, then your %ss work, eat, and your people grow." % worker,
        "",
        "You start with a capital, %d %ss, %d %ss and a few fields." % (s["workers"], worker, s["soldiers"], soldier),
        "",
        "**Your first night**",
        "1. Open /zoom, pick a tile with an outline, press Claim.",
        "2. Open /info, then the Economy tab, and choose what your %ss work on." % worker,
        "3. When your %ss near your housing limit, build a home." % worker,
        "4. Check /report in the morning to see what happened.",
        "",
        "/info tells you what needs attention. Stuck? Everything is in /help.",
    ])


def resources(rules):
    worker, soldier, _ = words(rules)
    lines = ["**What land makes**"]
    for terrain, spec in rules["terrain"].items():
        if spec["produces"]:
            lines.append("%s: %s" % (terrain, ", ".join(spec["produces"])))
    lines.append("Oil comes only from oil deposits (1 in about %d land tiles)." % rules["oil_deposit_one_in"])
    lines += ["", "Up to %d %ss work one tile. Each makes: %s." % (
        rules["workers_per_tile"], worker, ", ".join("%d %s" % (v, k) for k, v in rules["yield_per_worker"].items()
                                                      if k not in rules.get("refine", {}))),
        "Food: every person eats %d a night. Without it people starve. Livestock counts as %d food when you run short." % (
            rules["eat_per_person"], rules["food_value"].get("livestock", 1)),
        "", "**What things cost**",
        claim_text(rules),
        "Only %d claim a night." % rules["claim"]["per_tick"]]
    workshops = {spec["building"] for spec in rules.get("refine", {}).values()}
    for name, spec in rules["buildings"].items():
        if name in workshops:
            continue
        effect = []
        if spec.get("houses_workers"):
            effect.append("houses %d %ss" % (spec["houses_workers"], worker))
        if spec.get("houses_soldiers"):
            effect.append("houses %d %ss" % (spec["houses_soldiers"], soldier))
        if spec.get("defense_percent"):
            effect.append("+%d%% defence" % spec["defense_percent"])
        if name == "market":
            effect.append("lets you trade")
        lines.append("%s: %s%s" % (name.capitalize(), cost(spec["cost"]), " (%s)" % ", ".join(effect) if effect else ""))
        for i, up in enumerate(spec.get("upgrades", []), 2):
            gain = ["houses %d %ss" % (up[k], worker if k == "houses_workers" else soldier) for k in ("houses_workers", "houses_soldiers") if k in up]
            lines.append("  Level %d: %s (%s)" % (i, cost(up["cost"]), ", ".join(gain)))
    rise = rules.get("build", {}).get("rise_percent", 0)
    if rise:
        lines.append("Each building of a kind you already own makes the next one %d%% dearer. Upgrade with /upgrade or from the tile card." % rise)
    cap = rules["capital"]
    lines.append("Your capital houses %d %ss and %d %ss and gets +%d%% defence." % (
        cap["houses_workers"], worker, cap["houses_soldiers"], soldier, cap["defense_percent"]))
    lines += ["", "**Priorities** (0 to 5) say how your %ss are shared between resources. Only the proportions matter." % worker]
    return "\n".join(lines)


def goods(rules):
    worker, _, _ = words(rules)
    refine = rules.get("refine", {})
    if not refine:
        return "This ruleset has no refined goods."
    lines = ["**Refined goods**",
             "A workshop turns raw resources into a refined good. It makes nothing until %ss work in it: %d slots, 1 good per %s a night, "
             "never more than the inputs in your store allow. A new workshop starts on priority 2; change it in /info or /assign." % (
                 worker, rules["refine_slots"], worker), ""]
    for good, spec in refine.items():
        lines.append("%s: %s makes it from %s. Build cost: %s." % (
            good.capitalize(), spec["building"].capitalize(), " + ".join("%d %s" % (v, k) for k, v in spec["inputs"].items()),
            cost(rules["buildings"][spec["building"]]["cost"])))
    lines += ["",
              "Tools, steel and bricks are needed for upgrades (see the resources page). Provisions feed faster growth: each one adds a spare %s "
              "on a night your %ss are growing, up to double the usual growth." % (worker, worker),
              "Nobody has to make everything. Specialise and trade at a market."]
    return "\n".join(lines)


def modes(rules):
    worker, soldier, _ = words(rules)
    return "\n".join([
        "**Basic and advanced mode**",
        "Each player picks their own with /mode. Everyone starts on basic. Your own choice is the only one that matters to you.",
        "",
        "**Basic**",
        "Priorities (0 to 5) decide who works on what. Set them in /info. Only the proportions count. Nothing else to manage.",
        "",
        "**Advanced**",
        "/assign sets an exact number of %ss on a resource, like 'food 8, wood 4'. A tile holds %d %ss, so you can't put more on a resource than your tiles for it can hold; /info shows what is actually working." % (worker, rules["workers_per_tile"], worker),
        "Resources you don't give a count keep following your priorities. A count of 0 turns that resource off. '/assign auto' clears every count.",
        "/move takes an optional count, so you can send part of an army and leave the rest.",
        "",
        "Your counts are kept if you switch back to basic, but only the mode you are in runs. An admin can switch advanced off for the whole server.",
    ])


def war(rules):
    worker, soldier, _ = words(rules)
    c = rules["combat"]
    rolls = c["roll_percent"]
    return "\n".join([
        "**Armies**",
        "/train turns %ss into %ss (each costs %s and a %s) and needs room in your barracks or capital." % (worker, soldier, cost(rules["soldier"]["train_cost"]), worker),
        "In /zoom, pick a tile and press Send army. Armies move one tile a night.",
        "",
        "**Fighting**",
        "An army that walks onto enemy land attacks. Each side's strength is its %ss times a random %d-%d%%." % (
            soldier, min(rolls), max(rolls)),
        "Defenders get bonuses: terrain (%s), walls, and the capital. A tie goes to the defender. Both sides lose %ss." % (
            ", ".join("%s +%d%%" % (k, v["defense_percent"]) for k, v in rules["terrain"].items() if v["defense_percent"]), soldier),
        "",
        "**Oil**",
        "Armies on your own land cost nothing. Armies on land that isn't yours cost 1 oil per %d %ss a night. "
        "If you run short, each army abroad loses %d%% of its %ss." % (
            rules["oil_upkeep"]["soldiers_per_oil"], soldier, rules["oil_upkeep"]["shortfall_loss_percent"], soldier),
        "",
        "**Trade**",
        "/trade needs a Market on both sides. Name a nation, or leave 'to' empty to post on the trade board for anyone. Offers expire after %d nights; goods take time to arrive." % rules["trade"]["offer_expires_ticks"],
        *(["Goods travel as caravans over land. An army of a third nation that stands on one captures it, so guard the road."]
          if rules["trade"].get("caravans") else []),
        "/pact writes a promise between two nations on the public record. The game never enforces it; breaking one is announced.",
    ])


def winning(rules):
    v = rules["victory"]
    lines = ["**How to win**"]
    parts = []
    if v["land_share_percent"] is not None:
        parts.append("%d%% of all land" % v["land_share_percent"])
    if v["population_share_percent"] is not None:
        parts.append("%d%% of all people" % v["population_share_percent"])
    if v["stock_share_percent"] is not None:
        parts.append("%d%% of all stored goods" % v["stock_share_percent"])
    lines.append("Hold %s for %d nights in a row." % (" or ".join(parts), v["hold_ticks"]))
    if v.get("land_share_decay_per_tick") and v["land_share_percent"] is not None:
        lines.append("From night %d the land bar falls %d%% a night, down to a floor of %.1f times a fair share, so games always finish." % (
            v.get("land_share_decay_start", 0), v["land_share_decay_per_tick"], v.get("land_share_floor_multiple", 1)))
    if v["last_nation_standing"]:
        lines.append("The last nation standing also wins.")
    if "ranks" in rules:
        lines += ["", "**Titles** (by share of all land; you get a Discord role)"]
        lines.append(", ".join("%s %d%%" % (r["title"], r["min_land_percent"]) for r in rules["ranks"]))
    return "\n".join(lines)


def commands(rules):
    return "\n".join([
        "**Commands**",
        "/zoom - your map: pan, zoom, pick a tile, claim, build, send armies",
        "/map - the whole map",
        "/info - your nation in detail: switch mode, set priorities or exact counts, train soldiers",
        "/report - what happened to you last night",
        "/status - a quick summary",
        "/standings - who is ahead",
        "/claim, /build, /upgrade, /train, /move, /priorities - the typed versions of orders",
        "/mode basic|advanced - advanced adds /assign (exact workers per resource) and /move with a count (send part of an army)",
        "/dms on|off - private messages (night reports, private trade offers); off until you turn them on",
        "/cancel - clear tonight's orders and start again",
        "/trade, /offers, /accept, /decline - deals with other players (open offers appear on the trade board)",
        "/pact propose|accept|decline|break|list - recorded promises",
        "/tutorial - practise in a private test game",
    ])


PAGES = {"basics": basics, "resources": resources, "goods": goods, "modes": modes, "war": war, "winning": winning, "commands": commands}


def help_page(rules, tab):
    return PAGES[tab](rules)


def welcome(rules, name):
    """Private message after /join."""
    worker, soldier, nation = words(rules)
    return "\n".join([
        "You are in as **%s**. Here is what happens next:" % name,
        "- When the game starts you get a capital, a few fields, %d %ss and %d %ss." % (
            rules["start"]["workers"], worker, rules["start"]["soldiers"], soldier),
        "- Every night the game runs. You give orders during the day.",
        "- Use /help for the rules in seven short pages, or /tutorial to practise first.",
    ])


def started(world, rules, nid):
    """Message for a player when the game begins."""
    n = world["nations"][nid]
    return "\n".join([
        "The game has begun! **%s** has its capital at %s." % (n["name"], n["capital"]),
        "Try this first: /zoom, pick an outlined tile and claim it. Then open /info for what to do next, and /help for the rules.",
    ])


def report(world, rules, nid, row, events):
    """What happened to one nation during the last night."""
    n = world["nations"][nid]
    worker, soldier, _ = words(rules)
    if not n["alive"]:
        return "**%s** has fallen. You are now a spectator." % n["name"]
    if row is None:
        return "No night has passed yet."
    lines = ["**%s - night %d**" % (n["name"], row["tick"])]
    land_now = len(W.owned(world, nid))
    people_now = n["workers"] + W.soldiers(world, nid)

    def delta(before, after):
        return "%d (%+d)" % (after, after - before) if after != before else str(after)

    lines.append("Land: %s | %ss: %s | %ss: %s" % (delta(row["land"], land_now), worker.capitalize(),
                 delta(row["workers"], n["workers"]), soldier.capitalize(), delta(row["soldiers"], W.soldiers(world, nid))))
    changes = []
    for r in rules["resources"]:
        b, a = row["res"].get(r, 0), n["resources"].get(r, 0)
        if a != b:
            changes.append("%s %d -> %d (%+d)" % (r, b, a, a - b))
    lines.append("Stock: " + (", ".join(changes) if changes else "no change"))
    if events:
        lines += ["", "**What happened**"] + ["- " + e for e in events[:15]]
    else:
        lines.append("Nothing else happened.")
    return "\n".join(lines)
