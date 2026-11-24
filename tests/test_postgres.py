"""Real PostgreSQL proof: database CRUD plus a job, an upload, routes and restart recovery.

The normal test runner gives this suite its own PostgreSQL schema, so the checks below cannot be satisfied by SQLite.
With no RONBCLIPS_TEST_DATABASE_URL it says so and exits successfully; the SQLite run stays self-contained.
"""

import os
import shutil
import sys
import tempfile

URL = os.environ.get("RONBCLIPS_TEST_DATABASE_URL", "")
if not URL.startswith(("postgresql", "postgres")):
    print("PASS PostgreSQL checks skipped (no test database URL)")
    sys.exit(0)

DATA = tempfile.mkdtemp(prefix="pc-postgres-")
BASE = "http://127.0.0.1:8765"
os.environ.update(
    RONBCLIPS_DATA=DATA,
    RONBCLIPS_STORAGE_FILE=os.path.join(DATA, "storage.json"),
    DATABASE_URL=URL,
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_PUBLIC_URL=BASE,
    RONBCLIPS_UPLOAD_PART_MB="5",
    RONBCLIPS_MAX_UPLOAD_MB="64",
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
)
sys.path.insert(0, ".")

import sqlalchemy as sa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from studio import auth, config, ctx, db, feedback, jobs, server  # noqa: E402

db.init()
UID = auth.local_user().id
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)
fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


# ---- the database is really PostgreSQL, with the current schema in place
check("the suite is connected to PostgreSQL", db.engine.dialect.name, "postgresql")
check("migrations are recorded in PostgreSQL", sa.inspect(db.engine).has_table("schema_migrations"), True)

# ---- CRUD through the app and through SQL
fid = feedback.submit(UID, "idea", "postgres-crud-marker")["id"]
rows = feedback.listing()["items"]
check("a row can be created and read back", any(r["id"] == fid and r["message"] == "postgres-crud-marker" for r in rows), True)
feedback.set_status(fid, "done")
check("a row can be updated", next(r for r in feedback.listing()["items"] if r["id"] == fid)["status"], "done")
with db.engine.begin() as connection:
    connection.execute(sa.delete(db.feedback).where(db.feedback.c.id == fid))
    left = connection.execute(sa.select(sa.func.count()).select_from(db.feedback).where(db.feedback.c.id == fid)).scalar()
check("a row can be deleted", left, 0)

# ---- HTTP routes answer against the same PostgreSQL-backed app
check("the status route works on PostgreSQL", c.get("/api/status").status_code, 200)
r = c.post("/api/creators", json={"name": "Postgres route"}, headers=ORIGIN)
check("a creator can be saved through a route", r.status_code, 200)
check("the creator appears in the route list", any(x["name"] == "Postgres route" for x in c.get("/api/creators").json()), True)

# ---- an upload becomes a job on disk and in the app
blob = b"postgres-upload-bytes-" * 32
started = c.post("/api/upload/start", json={"name": "postgres.mp4", "size": len(blob)}, headers=ORIGIN)
check("an upload can be started on PostgreSQL", started.status_code, 200)
upid = started.json()["id"]
check(
    "the piece is accepted",
    c.put(f"/api/upload/{upid}/0", content=blob, headers={"Content-Type": "application/octet-stream", **ORIGIN}).status_code,
    200,
)
finished = c.post(f"/api/upload/{upid}/finish", json={}, headers=ORIGIN)
check("an upload can be finished on PostgreSQL", finished.status_code, 200)
uploaded = finished.json()["id"]
check("the joined file is on disk", (config.user_dir(UID) / "jobs" / uploaded / "source.mp4").read_bytes(), blob)

# ---- a running job survives a stop/reload like a real restart
with ctx.as_user(UID):
    job = jobs.create({"type": "file", "path": "", "name": "postgres-recovery.mp4"}, {}, enqueue=False)
    job["status"] = "running"
    job["resume"] = True
    jobs.save(job)
    jobs.stop(wait=True)
    jobs.reload()
    restarted = jobs.get(job["id"])
    check("a running job is resumable on PostgreSQL", (restarted["status"], restarted["resume"]), ("queued", True))
    jobs.delete(job["id"])
    jobs.delete(uploaded)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
db.engine.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
