import time
import asyncio

# cache simples com TTL + limite
class Cache:
    def __init__(self, max_size=500):
        self.store = {}
        self.max_size = max_size

    def get(self, key):
        data = self.store.get(key)
        if not data:
            return None

        value, exp = data
        if exp < time.time():
            del self.store[key]
            return None

        return value

    def set(self, key, value, ttl=5):
        if len(self.store) > self.max_size:
            self.store.pop(next(iter(self.store)))

        self.store[key] = (value, time.time() + ttl)


cache = Cache()

# rate limit simples
user_last_call = {}

def allow(user_id, cooldown=1.2):
    now = time.time()
    last = user_last_call.get(user_id, 0)

    if now - last < cooldown:
        return False

    user_last_call[user_id] = now
    return True

# controle de concorrência
spotify_semaphore = asyncio.Semaphore(20)
