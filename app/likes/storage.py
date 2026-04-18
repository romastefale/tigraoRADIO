from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


@dataclass(frozen=True)
class PlayEvent:
    user_id: int
    track_id: str
    played_at: str


class LikesStorage:
    """SQLite data layer for play tracking and likes toggling."""

    def __init__(self, database_path: str = "./data/app.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS play_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    track_id TEXT NOT NULL,
                    played_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS track_play_counts (
                    track_id TEXT PRIMARY KEY,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_play_counts (
                    user_id INTEGER PRIMARY KEY,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_track_likes (
                    user_id INTEGER NOT NULL,
                    track_id TEXT NOT NULL,
                    liked_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, track_id)
                )
                """
            )
            connection.commit()

    def track_play(
        self,
        *,
        user_id: int,
        track_id: str,
        played_at: str | None = None,
    ) -> PlayEvent:
        event_time = played_at or self._now_iso()

        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO play_events (user_id, track_id, played_at)
                VALUES (?, ?, ?)
                """,
                (user_id, track_id, event_time),
            )

            connection.execute(
                """
                INSERT INTO track_play_counts (track_id, play_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    play_count = play_count + 1,
                    updated_at = excluded.updated_at
                """,
                (track_id, event_time),
            )

            connection.execute(
                """
                INSERT INTO user_play_counts (user_id, play_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    play_count = play_count + 1,
                    updated_at = excluded.updated_at
                """,
                (user_id, event_time),
            )

            connection.commit()

        return PlayEvent(user_id=user_id, track_id=track_id, played_at=event_time)

    def toggle_like(self, *, user_id: int, track_id: str) -> bool:
        now = self._now_iso()

        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1 FROM user_track_likes
                WHERE user_id = ? AND track_id = ?
                """,
                (user_id, track_id),
            ).fetchone()

            if existing:
                connection.execute(
                    """
                    DELETE FROM user_track_likes
                    WHERE user_id = ? AND track_id = ?
                    """,
                    (user_id, track_id),
                )
                connection.commit()
                return False

            connection.execute(
                """
                INSERT INTO user_track_likes (user_id, track_id, liked_at)
                VALUES (?, ?, ?)
                """,
                (user_id, track_id, now),
            )
            connection.commit()
            return True

    def is_liked(self, *, user_id: int, track_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM user_track_likes
                WHERE user_id = ? AND track_id = ?
                """,
                (user_id, track_id),
            ).fetchone()

        return row is not None

    def get_track_play_count(self, *, track_id: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT play_count FROM track_play_counts
                WHERE track_id = ?
                """,
                (track_id,),
            ).fetchone()

        return int(row["play_count"]) if row else 0

    def get_user_play_count(self, *, user_id: int) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT play_count FROM user_play_counts
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

        return int(row["play_count"]) if row else 0

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
