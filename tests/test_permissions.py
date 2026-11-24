"""Accounts-off permissions: identity can only come from the built-in local owner.

Runs against the code directly with an empty data folder. A second user's files are planted on disk to prove that
a request cannot borrow that identity by putting a user id in its form, its headers or its cookies.
"""

import json
import os
import shutil
import sys
import tempfile

import sqlalchemy as sa

DATA = tempfile.mkdtemp(prefix="pc-permissions-")
BASE = "http://127.0.0.1:8765"
os.environ.update(
    RONBCLIPS_DATA=DATA,
    RONBCLIPS_STORAGE_FILE=os.path.join(DATA, "storage.json"),
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_PUBLIC_URL=BASE,
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
    RONBCLIPS_AI_KEY="",
    RONBCLIPS_AI_KEY_FILE="",
    RONBCLIPS_AI_MODELS="",
)
sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

from studio import auth, config, creators, ctx, db, jobs, server  # noqa: E402

db.init()
ORIGIN = {"Origin": BASE}
FORGED = {
    "uid": "u_other_1",
    "user_id": "u_other_1",
    "owner": "u_other_1",
    "owner_id": "u_other_1",
}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)
c.cookies.set("uid", "u_other_1")
c.cookies.set("session", "u_other_1")
fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


# ---- accounts are off: there is only one owner, and no door to sign in as somebody else
check("the app has one built-in owner", auth.user_ids(), [auth.LOCAL_ID])
check(
    "the old account doors are gone",
    [c.get(p).status_code for p in ("/login", "/signup", "/reset", "/api/auth/signup", "/api/auth/login", "/api/admin")],
    [404] * 6,
)
status = c.get("/api/status", headers={"X-User-ID": "u_other_1", "X-Forwarded-User": "u_other_1"})
check("headers and cookies cannot change who the page belongs to", status.json()["user"], {"id": auth.LOCAL_ID, "name": "You"})

# ---- plant something that belongs to nobody this HTTP client may act as
with ctx.as_user("u_other_1"):
    other_creator = creators.create({"name": "Other workspace"})
    other_job = jobs.create({"type": "file", "path": "", "name": "other.mp4"}, {}, enqueue=False)

# ---- forms may carry a user id, but it is ignored everywhere data is stored or read
r = c.post("/api/creators", json={"name": "Mine", **FORGED}, headers=ORIGIN)
check("a creator can be made", r.status_code, 200)
cid = r.json()["id"]
check(
    "a forged form puts the creator in the local owner's folder",
    ((config.user_dir(auth.LOCAL_ID) / "creators" / cid).is_dir(), (config.user_dir("u_other_1") / "creators" / cid).exists()),
    (True, False),
)
names = [row["name"] for row in c.get("/api/creators").json()]
check("the creator list shows only the local owner's work", ("Mine" in names, "Other workspace" in names), (True, False))
check(
    "a forged id cannot read another owner's job",
    c.request("GET", f"/api/jobs/{other_job['id']}", json=FORGED).status_code,
    404,
)
check(
    "a forged id cannot delete another owner's job",
    c.request("DELETE", f"/api/jobs/{other_job['id']}", json=FORGED, headers=ORIGIN).status_code,
    404,
)

marker = "permission-marker-123456"
r = c.post("/api/feedback", json={"kind": "idea", "message": marker, **FORGED}, headers=ORIGIN)
check("feedback can be sent", r.status_code, 200)
with db.engine.connect() as connection:
    author = connection.execute(sa.select(db.feedback.c.user_id).where(db.feedback.c.id == r.json()["id"])).scalar()
check("feedback is attributed to the session, not the form", author, auth.LOCAL_ID)

r = c.put("/api/settings", json={"tour_done": True, **FORGED}, headers=ORIGIN)
check("settings can be saved", r.status_code, 200)
mine = config.user_dir(auth.LOCAL_ID) / "accounts" / "settings.json"
theirs = config.user_dir("u_other_1") / "accounts" / "settings.json"
check(
    "settings are saved for the local owner only",
    (json.loads(mine.read_text(encoding="utf-8"))["tour_done"], theirs.exists()),
    (True, False),
)
check("the second owner's files are untouched", (config.user_dir("u_other_1") / "creators" / other_creator["id"]).is_dir(), True)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
db.engine.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
