import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.db = sqlite3.connect(self.directory / "ledger.db", isolation_level=None)
        os.chmod(self.directory / "ledger.db", 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (hash TEXT PRIMARY KEY, stage TEXT NOT NULL, plan TEXT NOT NULL, metainfo BLOB NOT NULL, command_id INTEGER, reason TEXT, updated REAL NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('owner',?)", (uuid.uuid4().hex,))
        self.owner_id = self.db.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()[0]

    def add(self, plan, metainfo):
        self.db.execute("INSERT INTO jobs(hash,stage,plan,metainfo,updated) VALUES(?,?,?,?,?)",
                        (plan["hash"], "prepared", json.dumps(plan), metainfo, time.time()))

    def get(self, info_hash):
        row = self.db.execute("SELECT hash,stage,plan,metainfo,command_id,reason FROM jobs WHERE hash=?", (info_hash,)).fetchone()
        if row is None:
            return None
        return dict(zip(("hash", "stage", "plan", "metainfo", "command_id", "reason"), (row[0], row[1], json.loads(row[2]), *row[3:])))

    def update(self, info_hash, stage, *, command_id=None, reason=None):
        self.db.execute("UPDATE jobs SET stage=?,command_id=?,reason=?,updated=? WHERE hash=?",
                        (stage, command_id, reason, time.time(), info_hash))

    def jobs(self):
        return [self.get(row[0]) for row in self.db.execute("SELECT hash FROM jobs ORDER BY updated")]

    def close(self):
        self.db.close()
