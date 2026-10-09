"""Durable at-most-once claims for webhook redelivery, including across restarts."""

import sqlite3
import threading
import time
from pathlib import Path


class EventLedger:
    def __init__(self, directory: Path, ttl=604800):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "webhooks.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Webhook ledger must not be a symlink.")
        self.ttl = ttl
        self.lock = threading.Lock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, received REAL NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS events_received ON events(received)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=0.2)

    def claim(self, event_id):
        with self.lock, self._connect() as db:
            now = time.time()
            db.execute("DELETE FROM events WHERE received < ?", (now - self.ttl,))
            result = db.execute(
                "INSERT OR IGNORE INTO events(id,received) VALUES (?,?)",
                (event_id, now),
            )
            return result.rowcount == 1

    def release(self, event_id):
        with self.lock, self._connect() as db:
            db.execute("DELETE FROM events WHERE id = ?", (event_id,))
