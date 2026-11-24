"""The health endpoints check the real database and say when the server is closing.

Runs against the code directly with its own empty data folder. The database is replaced with a broken one for one
check, so a degraded answer can be proved without stopping the real database.
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="ronbclips-health-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
)
sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from studio import db, health, server  # noqa: E402

db.init()
health.mark_started()
BASE = "http://127.0.0.1:8765"
fails = []


def check(name, got, expected):
    ok = got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, expected {expected!r}"))
    if not ok:
        fails.append(name)


@contextlib.contextmanager
def patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


probe = db.ping()
check("the database probe works", probe["ok"], True)
check("the probe names the backend", probe["backend"], db.engine.dialect.name)
check("the probe measures time", probe["latency_ms"] >= 0, True)
check("the probe keeps secrets out", any(k in probe for k in ("url", "password", "dsn", "path")), False)

broken = sa.create_engine("sqlite:///" + str(DATA / "no-folder" / "missing.db"), poolclass=sa.pool.NullPool)
with patch(db, "_probe", broken):
    down = db.ping()
check("a broken database is reported, not raised", (down["ok"], bool(down["error"])), (False, True))
check("a broken database keeps its address out", str(DATA) in json.dumps(down), False)

c = TestClient(server.app, base_url=BASE, follow_redirects=False)
check("the simple health route works", c.get("/healthz").status_code, 200)
check("the live route works", c.get("/livez").status_code, 200)
check("the ready route works", c.get("/readyz").status_code, 200)
detail_response = c.get("/health/detail")
detail = detail_response.json()
check("the detailed route answers", detail_response.status_code, 200)
check("the detailed route has a real database result", (detail["database"], detail["db"]["ok"]), ("connected", True))
check("the detailed route has lifecycle and version fields", sorted(detail["lifecycle"]), ["started_at", "state", "uptime_s"])
check("the detailed route starts running", (detail["status"], detail["lifecycle"]["state"]), ("healthy", "running"))
check("the detailed route has a version string", isinstance(detail["version"], str), True)
check("the detailed route keeps secrets out", any(str(DATA) in str(v) for v in detail.values()), False)

with patch(db, "ping", lambda: {"ok": False, "backend": "sqlite", "latency_ms": 1.2, "error": "OperationalError"}):
    check("a broken database makes healthz fail", c.get("/healthz").status_code, 503)
    ready = c.get("/readyz")
    check("a broken database makes readyz fail", (ready.status_code, ready.json()["ok"]), (503, False))
    broken_detail = c.get("/health/detail")
    check(
        "a broken database makes the detailed route fail",
        (broken_detail.status_code, broken_detail.json()["status"], broken_detail.json()["database"]),
        (503, "degraded", "disconnected"),
    )

health.mark_shutting_down()
check("the live route still says it is alive", (c.get("/livez").json()["status"], c.get("/livez").json()["shutting_down"]), ("alive", True))
check("the ready route refuses new work during a drain", c.get("/readyz").status_code, 503)
shutting = c.get("/health/detail")
check(
    "the detailed route reports the shutdown",
    (
        shutting.status_code,
        shutting.json()["status"],
        shutting.json()["ok"],
        shutting.json()["lifecycle"]["state"],
        shutting.json()["workers"]["shutting_down"],
    ),
    (503, "shutting_down", False, "shutting_down", True),
)
health.mark_started()
check("a fresh start clears the shutdown state", c.get("/health/detail").status_code, 200)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
db.engine.dispose()
broken.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
