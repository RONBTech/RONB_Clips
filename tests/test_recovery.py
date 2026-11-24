"""Restart and orphan recovery checks."""

import os
import shutil
import sys
import tempfile
import threading

import sqlalchemy as sa

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-recovery-")
os.environ.update(DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""))
sys.path.insert(0, ".")

from studio import auth, ctx, db, jobs

db.init()
fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


with db.engine.connect() as connection:
    version = connection.execute(sa.text("SELECT MAX(version) FROM schema_migrations")).scalar()
check("database migrations record their applied version", version == 3)
old_tables = {"users", "sessions", "email_ok", "email_tokens", "admin_log"}
check("the old account and admin tables are gone", old_tables.isdisjoint(sa.inspect(db.engine).get_table_names()))


owner = auth.local_user()
with ctx.as_user(owner.id):
    job = jobs.create(
        {"type": "file", "path": "", "name": "recovery.mp4"},
        {},
        enqueue=False,
    )
    job["status"] = "running"
    job["resume"] = True
    jobs.save(job)
    check("running jobs can be marked to resume", jobs.get(job["id"])["resume"] is True)
    jobs.stop(wait=True)
    stopped = jobs.get(job["id"])
    check("graceful stop changes the running job to queued", stopped["status"] == "queued")
    check("graceful stop leaves the job resumable", stopped["resume"] is True)
    check("graceful stop leaves no worker waiting for more work", not jobs._workers)

    # Starting again with the same data folder is what a restart after a crash does: the saved job is read
    # back from disk and anything marked resumable is put back on the queue.
    jobs.reload()
    restarted = jobs.get(job["id"])
    check("a restart reads the orphaned job back from disk", restarted is not None)
    check("a restart leaves the orphaned job queued", restarted["status"] == "queued" and restarted["resume"] is True)
    queued = any(item[1] == job["id"] for line in jobs._q.lines.values() for item in line)
    check("a restart puts the orphaned job back on the queue", queued)

    # A second start, with no jobs left over: now check that shutdown really waits for work already in progress.
    jobs.delete(job["id"])
    jobs.start()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_work():
        entered.set()
        release.wait(10)
        finished.set()

    jobs.submit(slow_work)
    check("a worker picks up background work", entered.wait(5))
    stopped = threading.Event()
    threading.Thread(target=lambda: (jobs.stop(wait=True), stopped.set()), daemon=True).start()
    check("shutdown waits while a job is still running", not stopped.wait(0.3))
    release.set()
    check("shutdown finishes once the running job ends", stopped.wait(5) and finished.is_set())

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
db.engine.dispose()
shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
