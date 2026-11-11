"""Saves game data in one SQLite file: a key/value table plus a snapshot of the world after every tick."""
import json
import sqlite3


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("create table if not exists kv (k text primary key, v text)")
        self.db.execute("create table if not exists snapshots (tick integer primary key, v text)")
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("select v from kv where k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("insert or replace into kv values (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def snapshot(self, tick, text):
        self.db.execute("insert or replace into snapshots values (?, ?)", (tick, text))
        self.db.commit()

    def snapshot_get(self, tick):
        row = self.db.execute("select v from snapshots where tick=?", (tick,)).fetchone()
        return row[0] if row else None

    def snapshot_ticks(self):
        return [r[0] for r in self.db.execute("select tick from snapshots order by tick")]
