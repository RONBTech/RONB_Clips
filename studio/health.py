"""What the health endpoints report.

A small leaf module so both the pages and the server can see the same lifecycle state without importing each other.
The database result is a real SELECT 1; nothing here prints an address, a file path or a secret.
"""

import time

from . import config, db

_STARTED_AT = time.time()
_BOOT = time.monotonic()
_shutting_down = False


def mark_started():
    """The web server has finished starting. Also lets an in-process restart show fresh uptime."""
    global _STARTED_AT, _BOOT, _shutting_down
    _STARTED_AT = time.time()
    _BOOT = time.monotonic()
    _shutting_down = False


def mark_shutting_down():
    """Shutdown has started and cannot be undone for this process."""
    global _shutting_down
    _shutting_down = True


def shutting_down() -> bool:
    return _shutting_down


def lifecycle() -> dict:
    state = "shutting_down" if _shutting_down else "running"
    return {"state": state, "started_at": round(_STARTED_AT, 3), "uptime_s": round(time.monotonic() - _BOOT, 1)}


def version() -> str:
    """The running commit, when installed from git. Zip installs simply leave this empty."""
    try:
        from . import updates

        return (getattr(updates, "RUNNING", "") or "")[:12]
    except Exception:  # noqa: BLE001 - health reporting must never fail because of a version lookup
        return ""


def detail() -> tuple[dict, int]:
    """Build the detailed report and the HTTP status it should have."""
    from . import jobs, live, load, transcribe

    probe = db.ping()
    down = shutting_down()
    healthy = probe["ok"] and not down
    body = {
        "ok": healthy,
        "status": "shutting_down" if down else ("healthy" if probe["ok"] else "degraded"),
        "database": "connected" if probe["ok"] else "disconnected",
        "db": probe,
        "lifecycle": lifecycle(),
        "version": version(),
        "host": config.HOST,
        "port": config.PORT,
        "mode": "remote_asr" if transcribe.REMOTE else "local",
        "speech_model": transcribe.model_ready(),
        "load": load.state(),
        "jobs": {"running": jobs.running_now(), "waiting": jobs.waiting_total()},
        "workers": {
            "job_threads": len(jobs._workers),
            "queue_closed": jobs.queue_closed(),
            "shutting_down": down,
            "live_sessions": len(live.SESSIONS),
        },
    }
    return body, (200 if healthy else 503)
