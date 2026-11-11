"""Battle math. Everything is a whole number; rolls are percentages from the ruleset."""


def roll(rules, rng):
    rolls = rules["combat"]["roll_percent"]
    return rolls[rng.randrange(len(rolls))]


def defense_bonus(world, rules, k):
    """Percent bonus for the defender: terrain plus wall or capital on the tile."""
    tile = world["tiles"][k]
    bonus = rules["terrain"][tile["terrain"]]["defense_percent"]
    b = tile["building"]
    if b == "capital":
        bonus += rules["capital"]["defense_percent"]
    elif b:
        bonus += rules["buildings"][b].get("defense_percent", 0)
    return bonus


def fight(rules, rng, attackers, defenders, bonus_percent):
    """One battle. Strengths are held x100 so small armies are not lost to rounding.
    A tie goes to the defender. Each side loses a share of the other side's strength."""
    if defenders == 0:
        return {"attacker_wins": True, "att_strength": attackers, "def_strength": 0,
                "att_loss": 0, "def_loss": 0}
    a = attackers * roll(rules, rng)
    d = defenders * (100 + bonus_percent) * roll(rules, rng) // 100
    cas = rules["combat"]["casualty_percent"]
    return {
        "attacker_wins": a > d,
        "att_strength": a // 100,
        "def_strength": d // 100,
        "att_loss": min(attackers, d * cas // 10000),
        "def_loss": min(defenders, a * cas // 10000),
    }
