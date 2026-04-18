from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

from app.config.settings import DATABASE_URL


def _get_sqlite_database_path() -> str:
    parsed = urlparse(DATABASE_URL)
    if parsed.scheme != "sqlite":
        raise ValueError("get_user_total_likes supports only sqlite DATABASE_URL")

    if parsed.netloc:
        return f"//{parsed.netloc}{parsed.path}"

    return parsed.path


def get_user_total_likes(user_id: int) -> int:
    database_path = _get_sqlite_database_path()

    with sqlite3.connect(database_path) as conn:
        cursor = conn.execute(
            """
            SELECT COUNT(*) FROM user_track_likes
            WHERE user_id = ? AND liked = 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()

    return int(row[0] if row else 0)
