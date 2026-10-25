from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .models import StorageBlocked


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class LongformStore:
    """One Windows host owns the DB. Worker processes never open SQLite."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "studio.db"
        self.lock = threading.RLock()
        self._connection = None
        # WAL-reset fixes were backported to these branches. Older bundled SQLite
        # uses rollback journaling until the packaged runtime is upgraded.
        version = sqlite3.sqlite_version_info
        patched = version >= (3, 51, 3) or version[:2] == (3, 50) and version >= (3, 50, 7) or version[:2] == (3, 44) and version >= (3, 44, 6)
        self.journal_mode = "WAL" if patched else "DELETE"

    @property
    def connection(self):
        # A create_app factory must not open/change a live service's SQLite
        # journal or schema before lifespan has acquired its ownership lock.
        # Standalone library users may initialize through create() before start().
        with self.lock:
            if self._connection is not None:
                return self._connection
            connection = sqlite3.connect(self.path, check_same_thread=False, timeout=0.2)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA busy_timeout=200")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(f"PRAGMA journal_mode={self.journal_mode}")
                connection.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT OR IGNORE INTO meta VALUES('schema','2');
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS segments(job_id TEXT NOT NULL,id TEXT NOT NULL,start INTEGER NOT NULL,payload TEXT NOT NULL,
          PRIMARY KEY(job_id,id),FOREIGN KEY(job_id) REFERENCES jobs(id));
        CREATE INDEX IF NOT EXISTS segment_order ON segments(job_id,start);
        CREATE TABLE IF NOT EXISTS renders(key TEXT PRIMARY KEY,path TEXT NOT NULL,sha TEXT NOT NULL,metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,utc TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS event_job ON events(job_id,seq);
        CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,segment_id TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,payload TEXT NOT NULL);
                """)
                connection.commit()
            except Exception:
                connection.close()
                raise
            self._connection = connection
            return connection

    @contextmanager
    def transaction(self):
        with self.lock:
            for attempt in range(6):
                try:
                    self.connection.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if attempt == 5 or not any(s in str(exc).lower() for s in ("locked", "busy")):
                        raise StorageBlocked(str(exc)) from exc
                    time.sleep(min(0.025 * 2 ** attempt, 0.4))
            try:
                yield self.connection
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def save_job(self, job: dict, connection=None):
        if connection is not None:
            connection.execute("INSERT OR REPLACE INTO jobs VALUES(?,?)", (job["id"], dumps(job)))
            return
        with self.transaction() as conn:
            self.save_job(job, conn)

    def save_segment(self, job_id, segment, connection=None):
        if connection is not None:
            connection.execute("INSERT OR REPLACE INTO segments VALUES(?,?,?,?)", (job_id, segment["id"], segment["start"], dumps(segment)))
            return
        with self.transaction() as conn:
            self.save_segment(job_id, segment, conn)

    def get_job(self, job_id):
        with self.lock:
            row = self.connection.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return json.loads(row[0])

    def list_jobs(self):
        with self.lock:
            rows = self.connection.execute("SELECT payload FROM jobs ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def job_versions(self):
        with self.lock:
            rows = self.connection.execute("SELECT id,json_extract(payload,'$.version') FROM jobs").fetchall()
        return {row[0]: row[1] for row in rows}

    def segments(self, job_id):
        with self.lock:
            rows = self.connection.execute("SELECT payload FROM segments WHERE job_id=? ORDER BY start,id", (job_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def segment_counts(self, job_id):
        with self.lock:
            row = self.connection.execute("""SELECT COUNT(*),
                SUM(CASE WHEN json_extract(payload,'$.status') != 'skipped' THEN 1 ELSE 0 END),
                SUM(CASE WHEN json_extract(payload,'$.status') = 'accepted' THEN 1 ELSE 0 END)
                FROM segments WHERE job_id=? AND json_extract(payload,'$.status') != 'superseded'""", (job_id,)).fetchone()
        return {"records": row[0], "total": row[1] or 0, "accepted": row[2] or 0}

    def render(self, key):
        with self.lock:
            row = self.connection.execute("SELECT path,sha,metadata FROM renders WHERE key=?", (key,)).fetchone()
        return {"path": row[0], "sha256": row[1], "metadata": json.loads(row[2])} if row else None

    def save_render(self, key, path, sha, metadata, segment, job_id):
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO renders VALUES(?,?,?,?)", (key, str(path), sha, dumps(metadata)))
            self.save_segment(job_id, segment, conn)

    def event(self, job_id, utc, kind, payload=None):
        with self.transaction() as conn:
            conn.execute("INSERT INTO events(job_id,utc,kind,payload) VALUES(?,?,?,?)", (job_id, utc, kind, dumps(payload or {})))

    def events(self, job_id, after=0, limit=100, tail=False):
        with self.lock:
            order = "DESC" if tail else "ASC"
            rows = self.connection.execute(f"SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq {order} LIMIT ?", (job_id, after, limit)).fetchall()
        if tail:
            rows.reverse()
        return [{"seq": r["seq"], "utc": r["utc"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows]

    def record(self, table, identity, job_id, payload, segment_id=None):
        with self.transaction() as conn:
            if table == "attempts":
                conn.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)", (identity, job_id, segment_id, dumps(payload)))
            elif table == "sessions":
                conn.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (identity, job_id, dumps(payload)))
            else:
                raise ValueError(table)

    def backup(self, path: Path):
        with self.lock, sqlite3.connect(path) as destination:
            self.connection.backup(destination)

    def close(self):
        with self.lock:
            if self._connection is None:
                return
            if self.journal_mode == "WAL":
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._connection.close()
            self._connection = None
