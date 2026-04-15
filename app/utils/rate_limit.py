from __future__ import annotations

import time


class UserRateLimiter:
    def __init__(self, window_seconds: int = 2):
        self.window_seconds = window_seconds
        self._last_call: dict[int, float] = {}

    def allow(self, user_id: int) -> bool:
        now = time.time()
        last = self._last_call.get(user_id)
        if last is not None and now - last < self.window_seconds:
            return False
        self._last_call[user_id] = now
        return True
