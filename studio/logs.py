"""Small structured event log.

One line is one JSON object: a UTC timestamp, a level, an event name and (when there is one) the request id from
ctx. Background workers use a made-up id such as job-20261124-... when no web request started the work, so a line
from a worker can still be followed through the log.

Nothing here imports the web server or the job pipeline, so it is safe to use from any module.
"""

import contextlib
import datetime
import json
import math
import os
import re
import threading

from . import ctx

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_LOCK = threading.Lock()
_MAX_FIELD = 1000


def clean_request_id(raw: str) -> str:
    """Accept a short caller id, or make one, so nobody can put newlines or huge text into the log."""
    value = (raw or "").strip()
    if 1 <= len(value) <= 64 and all(ch.isalnum() or ch in "-_.:" for ch in value):
        return value
    return _uuid4()


def _uuid4() -> str:
    import uuid

    return uuid.uuid4().hex


def background_id(prefix: str, identifier: str = "") -> str:
    """A readable correlation id for work that did not come from a web request."""
    clean = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(identifier)).strip("-")[:64]
    return f"{prefix}-{clean}" if clean else f"{prefix}-{_uuid4()[:12]}"


def _value(value):
    if isinstance(value, str) and len(value) > _MAX_FIELD:
        return value[:_MAX_FIELD] + "..."
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _value(v) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple, set)):
        return [_value(v) for v in list(value)[:50]]
    return value


def emit(event: str, level: str = "info", msg: str = "", **fields):
    """Write one structured line. Logging must never break the work that is being logged."""
    try:
        minimum = _LEVELS.get(os_level(), 20)
        if _LEVELS.get(level, 20) < minimum:
            return
        record: dict[str, object] = {
            "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level,
            "event": event,
        }
        rid = ctx.request_id()
        if rid:
            record["request_id"] = rid
        if msg:
            record["msg"] = _value(msg)
        for key, value in fields.items():
            if value is not None:
                record[key] = _value(value)
        line = json.dumps(record, ensure_ascii=True, default=str, separators=(",", ":"), allow_nan=False)
        with _LOCK:
            print(line, flush=True)
    except Exception:  # noqa: BLE001 - a broken log line must never stop a job or a request
        pass


def os_level() -> str:
    return (os.environ.get("RONBCLIPS_LOG_LEVEL") or "info").strip().lower()


@contextlib.contextmanager
def scope(uid=None, request_id="", ip=""):
    """Run a short piece of work with its own correlation id, then put the old one back."""
    with ctx.as_context(uid, request_id=request_id, ip=ip):
        yield
