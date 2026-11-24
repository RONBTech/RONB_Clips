"""The small checks and counters shared by the route modules.

Keeping them here means each domain module can use the same queue limits, upload space checks and change tokens
without importing the web server itself.
"""

import os
import re
import threading
import time

from fastapi import HTTPException

from . import auth, config, creators, ctx, jobs, live, load, profiles, sync_api, tasks
from .models import Job

BOOT = f"{int(time.time()) % 100000:x}"  # part of every sync token, so pages reload their data after a restart

_create_lock = threading.Lock()  # "do I have a free slot?" and "take it" happen together, so bursts cannot slip past the limit
_lookups = threading.BoundedSemaphore(8)  # link and channel previews running at once, whole server
_disk: dict = {}
_reserved: dict = {}  # bytes promised to uploads that are still arriving
_reserved_lock = threading.Lock()


def bounds(limit: int, offset: int, cap: int = 200):
    """Keep a page request from asking for the whole table."""
    return min(max(limit, 1), cap), max(offset, 0)


def throttle(key, limit, window, msg="Too many requests at once. Wait a moment."):
    try:
        auth.throttle(key, limit, window)
    except auth.AuthError as e:
        raise HTTPException(429, msg) from e


def job_or_404(jid: str) -> Job:
    job = jobs.get(jid) if re.fullmatch(r"[\w\-]{8,60}", jid) else None
    if not job:
        raise HTTPException(404, "No such video")
    return job


def job_slots():
    from . import tools

    if tools.missing():
        raise HTTPException(503, tools.HELP)
    if jobs.busy_count() >= config.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, f"You already have {config.MAX_ACTIVE_JOBS_PER_USER} videos in progress. Wait for one to finish.")


def reserve(uid: str, size: int):
    """Take this upload's space out of the owner's quota now, so ten uploads started at once cannot all fit."""
    with _reserved_lock:
        have = _disk.get(uid, (0, 0))[1] + _reserved.get(uid, 0)
        if have + size > config.MAX_USER_GB * 1e9:
            raise HTTPException(507, "There isn't room for this file in your storage. Delete some older videos to make room.")
        _reserved[uid] = _reserved.get(uid, 0) + size


def release(uid: str, size: int):
    with _reserved_lock:
        left = max(0, _reserved.get(uid, 0) - size)
        if left:
            _reserved[uid] = left
        else:
            _reserved.pop(uid, None)


def forget_disk(uid: str):
    _disk.pop(uid, None)


def quota():
    """Disk space is measured at most once a minute."""
    if load.disk_full():
        from . import storage

        raise HTTPException(507, storage.full_message())
    uid = ctx.uid()
    got = _disk.get(uid)
    if not got or time.time() - got[0] > 60:
        total = 0
        for root, _dirs, files in os.walk(config.user_dir(uid)):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        got = _disk[uid] = (time.time(), total)
    with _reserved_lock:
        promised = _reserved.get(uid, 0)
    if got[1] + promised > config.MAX_USER_GB * 1e9:
        raise HTTPException(507, "Your storage is full. Delete some older videos to make room.")


def lookup(fn, *a) -> dict:
    """Start a preview lookup off the request thread and return its task id.

    The page asks /api/tasks/{id} for the answer. The slot is taken and freed by the background work, so a long
    lookup never holds a web request open.
    """
    from . import safe  # imported here to keep the shared module's imports small

    def work():
        if not _lookups.acquire(blocking=False):
            raise RuntimeError("Lots of people are looking things up right now. Try again in a few seconds.")
        try:
            return fn(*a)
        except Exception as error:  # noqa: BLE001 - a site can answer in any shape at all
            raise RuntimeError(safe.friendly(error)) from error
        finally:
            _lookups.release()

    return {"id": tasks.submit("Site lookup", work)}


def creator_rows() -> list:
    out = []
    for c in creators.all_creators():
        if profiles.needs_refresh(c):
            profiles.refresh(c["id"])
        s = live.active_for(c["id"])
        out.append({**c, "live_job": s.job["id"] if s else None, "log": creators.read_log(c["id"], 30)})
    return out


def pairs(v, limit=300) -> list:
    """[[video id, clip id], ...] from the page, checked."""
    out = []
    for x in (v if isinstance(v, list) else [])[:limit]:
        if isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(y, str) and re.fullmatch(r"[\w\-]{1,60}", y) for y in x):
            out.append((x[0], x[1]))
    return out


def sync_token() -> str:
    return sync_api.token(BOOT)


def sync_place(v: str):
    return sync_api.place(v, BOOT)


def sync_unchanged(v: str):
    return sync_api.unchanged(v, BOOT)


def sync_response(v: str) -> dict:
    return sync_api.response(v, BOOT)
