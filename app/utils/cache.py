from __future__ import annotations

import time


class TTLCache:
    def __init__(self, ttl_seconds: int = 5):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        hit = self._store.get(key)
        if not hit:
            return None
        expires_at, value = hit
        if time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.time() + self.ttl_seconds, value)
