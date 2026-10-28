"""SQLite checkpoints and transactional request-level idempotency."""
import json
import sqlite3
from .common import canonical, digest


class Store:
    def __init__(self, path):
        from pathlib import Path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plans (
            request_id TEXT PRIMARY KEY REFERENCES requests(id),
            plan_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def begin(self, request):
        rid, fingerprint = request["request_id"], digest(request)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO requests VALUES (?, ?, ?)",
                            (rid, fingerprint, canonical({})))
        row = self.db.execute("SELECT fingerprint,state FROM requests WHERE id=?", (rid,)).fetchone()
        if row[0] != fingerprint:
            raise ValueError("REQUEST_ID_CONFLICT：同一 request_id 的输入已变化，请使用新 ID")
        return json.loads(row[1])

    def checkpoint(self, rid, state):
        with self.db:
            self.db.execute("UPDATE requests SET state=? WHERE id=?", (canonical(state), rid))

    def saved(self, rid):
        row = self.db.execute("SELECT payload FROM plans WHERE request_id=?", (rid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, rid, payload):
        plan_id = "plan-" + digest(rid)
        value = {**payload, "plan_id": plan_id, "request_id": rid}
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO plans VALUES (?,?,?)", (rid, plan_id, canonical(value)))
        # Return the committed record, never an uncommitted candidate.
        return self.saved(rid)
