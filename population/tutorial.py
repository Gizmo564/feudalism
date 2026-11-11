"""A private practice game: you plus two scripted rivals, run on a throwaway in-memory database.
No Discord code here."""
import random

from . import bot
from .game import Game
from .store import Store

STEPS = 4
RIVALS = (("Rival North", 0.1), ("Rival South", 0.3))


class Tutorial:
    def __init__(self, uid, mode="feudalism", seed=None):
        self.uid = str(uid)
        self.game = Game(Store(":memory:"))
        self.game.open_signup(mode)
        self.game.join(self.uid, "Your Realm")
        for i, (name, _) in enumerate(RIVALS):
            self.game.join("rival%d" % i, name)
        self.game.start(5, seed=seed)
        self.nid = 0
        self.nights = 0
        self.start_land = len(self.land())

    def land(self):
        return [k for k, t in self.game.world["tiles"].items() if t["owner"] == self.nid]

    def night(self):
        """Rivals give their orders, then the night runs. Returns the report text."""
        w, rules = self.game.world, self.game.rules
        for i, (_, aggression) in enumerate(RIVALS, start=1):
            if w["nations"][i]["alive"]:
                bot.act(w, rules, i, aggression, random.Random(w["seed"] * 7 + w["tick"] * 31 + i))
        self.nights += 1
        return self.game.run_tick()

    def done(self):
        w = self.game.world
        n = w["nations"][self.nid]
        return {"claim": len(self.land()) > self.start_land,
                "build": any(w["tiles"][k]["building"] not in (None, "capital") for k in self.land()),
                "priorities": n["priorities"] != self.game.rules["default_priorities"],
                "nights": self.nights >= 3}

    def hint(self):
        """The next thing to try, as a short message."""
        w = self.game.world
        done = self.done()
        if not w["nations"][self.nid]["alive"]:
            return "Your practice realm fell. Type /tutorial stop, then /tutorial start to try again."
        queued = [o for o in w["orders"] if o["nation"] == self.nid]
        step = sum(done.values())
        if all(done.values()):
            return "Tutorial complete! You know the basics. /tutorial stop takes you back to the real game. /help has the full rules."
        if queued and not (step == 3 and not done["nights"]):
            return "Orders queued. Run /tutorial night to let the night pass and see them happen."
        if not done["claim"]:
            return "Step 1 of %d - Claim land: open /zoom, pick a tile with an outline from the menu, press Claim. Then /tutorial night." % STEPS
        if not done["build"]:
            return "Step 2 of %d - Build: in /zoom pick one of your own tiles and press Home. Homes let your people grow." % STEPS
        if not done["priorities"]:
            return "Step 3 of %d - Work: open /info, go to the Economy tab and change a priority with the - and + buttons." % STEPS
        return "Step 4 of %d - Let a few nights pass with /tutorial night (%d so far). Check /report and /info each time." % (STEPS, self.nights)
