from __future__ import annotations

from app.likes import storage


def get_user_total_likes(user_id: int) -> int:
    return storage.get_user_total_likes(user_id)
