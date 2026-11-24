"""The local-owner identity for RONB Clips.

There are no app accounts or sessions. The same built-in owner is used for every request.
This module keeps only the few things other parts of the app still ask about that owner.
"""

import collections
import threading
import time
from types import SimpleNamespace

from . import config, ctx

LOCAL_ID = "u_local"
_local = None
_seen = {}
_attempts = collections.defaultdict(collections.deque)
_lock = threading.Lock()


class AuthError(ValueError):
    pass


def local_user():
    global _local
    if _local is None:
        config.user_dir(LOCAL_ID).mkdir(parents=True, exist_ok=True)
        _local = SimpleNamespace(id=LOCAL_ID, name="You")
    return _local


def public(u):
    if u is None:
        return None
    return {"id": u.id, "name": u.name}


def throttle(key: str, limit: int, window: int):
    """Stop one caller from making too many requests in a short time.

    Accounts are off, so every request has the same owner id. Adding the caller's address keeps one busy visitor
    from using up everybody's allowance. The address only comes from a header when a trusted proxy is configured.
    """
    now = time.time()
    key = (ctx.ip() + "|" + key)[:200]
    with _lock:
        if len(_attempts) > 20000:
            for old_key, values in list(_attempts.items()):
                if not values or values[-1] < now - 3600:
                    del _attempts[old_key]
        values = _attempts[key]
        while values and values[0] < now - window:
            values.popleft()
        if len(values) >= limit:
            raise AuthError("Too many attempts. Wait a few minutes and try again.")
        values.append(now)


def user_ids():
    return [LOCAL_ID]


def seen_recently(uid: str):
    return time.time() - _seen.get(uid, 0) < 300


def touch(uid: str):
    _seen[uid] = time.time()
