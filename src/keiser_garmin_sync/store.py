"""SQLite dedup store: one row per Keiser ride we've processed.

Keeps the sync idempotent -- a ride is uploaded to Garmin exactly once, even
though each poll cycle re-lists rides inside the look-back window.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rides (
                keiser_id           INTEGER PRIMARY KEY,
                started_at          TEXT,
                ended_at            TEXT,
                duration_seconds    REAL,
                status              TEXT NOT NULL,   -- uploaded | duplicate | skipped | error
                garmin_activity_id  TEXT,
                error               TEXT,
                first_seen          REAL,
                updated_at          REAL
            );
            CREATE INDEX IF NOT EXISTS idx_rides_status ON rides(status);
            """
        )
        self._conn.commit()

    def is_synced(self, keiser_id: int) -> bool:
        row = self._conn.execute(
            "SELECT status FROM rides WHERE keiser_id = ?", (keiser_id,)
        ).fetchone()
        # Retry on prior errors; skip only terminal states.
        return bool(row) and row["status"] in ("uploaded", "duplicate", "skipped")

    def record(
        self,
        keiser_id: int,
        status: str,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_seconds: float | None = None,
        garmin_activity_id: str | None = None,
        error: str | None = None,
    ) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO rides (keiser_id, started_at, ended_at, duration_seconds,
                               status, garmin_activity_id, error, first_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keiser_id) DO UPDATE SET
                started_at=excluded.started_at,
                ended_at=excluded.ended_at,
                duration_seconds=excluded.duration_seconds,
                status=excluded.status,
                garmin_activity_id=excluded.garmin_activity_id,
                error=excluded.error,
                updated_at=excluded.updated_at
            """,
            (
                keiser_id, started_at, ended_at, duration_seconds,
                status, garmin_activity_id, error, now, now,
            ),
        )
        self._conn.commit()

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) c FROM rides GROUP BY status"
        ).fetchall()
        out = {r["status"]: r["c"] for r in rows}
        out["total"] = sum(out.values())
        return out

    def mismatch_count(self) -> int:
        """Uploaded rides whose post-upload validation flagged a discrepancy."""
        row = self._conn.execute(
            "SELECT COUNT(*) c FROM rides WHERE status='uploaded' AND error IS NOT NULL"
        ).fetchone()
        return int(row["c"]) if row else 0

    def recent(self, limit: int = 15) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM rides ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
