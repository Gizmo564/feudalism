"""Hex grid helpers. A tile is named by a string "q,r" (axial coordinates)."""

DIRS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]


def key(q, r):
    return "%d,%d" % (q, r)


def parse(k):
    q, r = k.split(",")
    return int(q), int(r)


def neighbors(k):
    q, r = parse(k)
    return [key(q + dq, r + dr) for dq, dr in DIRS]


def distance(a, b):
    aq, ar = parse(a)
    bq, br = parse(b)
    dq, dr = aq - bq, ar - br
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def within(center, radius):
    """Every tile name within `radius` steps of center (the map may not contain all of them)."""
    q, r = parse(center)
    out = []
    for dq in range(-radius, radius + 1):
        for dr in range(max(-radius, -dq - radius), min(radius, -dq + radius) + 1):
            out.append(key(q + dq, r + dr))
    return out


def ring(radius):
    """Tiles exactly `radius` steps from the middle of the map."""
    return [k for k in within("0,0", radius) if distance("0,0", k) == radius]
