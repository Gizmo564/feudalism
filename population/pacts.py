"""Pacts are written promises between two nations. The game only keeps the record; it never enforces one.
Breaking a pact is allowed and is announced to everyone, so a nation's word has a public history."""
from . import world as W

MAX_TEXT = 120


def news(world, text):
    """Pact news is always public, even with fog of war: a promise is only worth something if everyone can see it."""
    world["events"].setdefault("pacts", []).append(text)


def _list(world):
    return world.setdefault("pacts", [])


def find(world, pid):
    return next((p for p in _list(world) if p["id"] == pid), None)


def propose(world, frm, to, text):
    text = " ".join(text.split())
    if frm == to:
        raise ValueError("you cannot make a pact with yourself")
    if not text:
        raise ValueError("say what the pact is, e.g. 'no attacks until tick 30'")
    if len(text) > MAX_TEXT:
        raise ValueError("keep the pact under %d characters" % MAX_TEXT)
    for nid in (frm, to):
        if not world["nations"][nid]["alive"]:
            raise ValueError("that nation has been eliminated")
    pid = world.get("next_pact_id", 1)
    world["next_pact_id"] = pid + 1
    _list(world).append({"id": pid, "a": frm, "b": to, "text": text, "tick": world["tick"], "status": "proposed"})
    W.ev(world, to, "%s proposes pact %d: %s" % (world["nations"][frm]["name"], pid, text))
    return pid


def accept(world, pid, by):
    p = find(world, pid)
    if p is None or p["status"] != "proposed" or p["b"] != by:
        raise ValueError("no pact %d is waiting for you" % pid)
    p["status"] = "active"
    p["since"] = world["tick"]
    news(world, "Pact %d: %s and %s agree: %s" % (pid, world["nations"][p["a"]]["name"],
                                                  world["nations"][p["b"]]["name"], p["text"]))
    return p


def decline(world, pid, by):
    p = find(world, pid)
    if p is None or p["status"] != "proposed" or by not in (p["a"], p["b"]):
        raise ValueError("no pact %d is open to you" % pid)
    p["status"] = "withdrawn" if by == p["a"] else "declined"
    return p


def end(world, pid, by, broken=True):
    """Either side can end an active pact. It is recorded as broken and announced."""
    p = find(world, pid)
    if p is None or p["status"] != "active" or by not in (p["a"], p["b"]):
        raise ValueError("you have no active pact %d" % pid)
    p["status"] = "broken"
    p["ended"] = world["tick"]
    p["broken_by"] = by
    other = p["b"] if by == p["a"] else p["a"]
    news(world, "Pact %d broken: %s ended its promise to %s (%s)" % (
        pid, world["nations"][by]["name"], world["nations"][other]["name"], p["text"]))
    return p


def for_nation(world, nid, statuses=("proposed", "active")):
    return [p for p in _list(world) if nid in (p["a"], p["b"]) and p["status"] in statuses]


def broken_count(world, nid):
    return sum(1 for p in _list(world) if p["status"] == "broken" and p.get("broken_by") == nid)
