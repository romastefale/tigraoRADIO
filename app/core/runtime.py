import time

user_last_call = {}

def allow(user_id, cooldown=1.2):
    now = time.time()
    last = user_last_call.get(user_id, 0)

    if now - last < cooldown:
        return False

    user_last_call[user_id] = now
    return True
