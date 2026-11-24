"""The small /api/sync response: cheap polling is kept out of the web monolith."""

import threading
import time

from . import accounts, creators, ctx, jobs, live, profiles, usage


def start_background_refresh():
    def loop():
        while True:
            time.sleep(300)
            for uid, mine in list(creators._mem.items()):
                with ctx.as_user(uid):
                    for c in list(mine.values()):
                        if profiles.needs_refresh(c):
                            profiles.refresh(c["id"])

    threading.Thread(target=loop, daemon=True, name="profile-refresh").start()


def token(boot: str) -> str:
    """One string tells the page whether jobs or queue state changed."""
    return f"{boot}.{ctx.version()}.{jobs.queue_epoch() if jobs.busy_count() else 0}.{jobs.struct_version()}"


def place(v: str, boot: str):
    """Read the change and structure counters from a page token."""
    parts = (v or "").split(".")
    if len(parts) != 4 or parts[0] != boot:
        return None, None
    try:
        return int(parts[1]), int(parts[3])
    except ValueError:
        return None, None


def unchanged(v: str, boot: str) -> dict | None:
    """Answer nothing-changed synchronously, without disk or network work."""
    mine = creators._mem.get(ctx.uid())
    if mine is None or not v or v != token(boot):
        return None
    return {"v": v}


def response(v: str, boot: str) -> dict:
    """Build a full or incremental sync response."""
    current = token(boot)
    if v == current:
        return unchanged(v, boot) or {"v": current}

    from . import notices

    since, struct = place(v, boot)
    full = since is None or struct != jobs.struct_version()
    out = {
        "v": current,
        "jobs": [jobs.public(j) for j in jobs.changed_since(since, full)],
        "creators": creator_rows(),
        "accounts": accounts.list_accounts(),
        "ai": usage.brief(),
        "unread": notices.unread(),
    }
    if full:
        out["jobs_full"] = True
    return out


def creator_rows() -> list:
    out = []
    for c in creators.all_creators():
        s = live.active_for(c["id"])
        out.append({**c, "live_job": s.job["id"] if s else None, "log": creators.read_log(c["id"], 30)})
    return out
