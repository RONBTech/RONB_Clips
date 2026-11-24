"""Connect routes with accounts off: one local owner, OAuth states, no secrets in the page data, and signed clip
links. Runs against the code directly with its own empty data folder. No platform is contacted: only a state check
is made, and the signed links are read straight from the app's own disk.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

os.environ["RONBCLIPS_DATA"] = tempfile.mkdtemp(prefix="pc-connect-")
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_PUBLIC_URL="https://clips.example.com",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
    RONBCLIPS_IG_APP_ID="",
    RONBCLIPS_IG_APP_SECRET="",
    RONBCLIPS_TIKTOK_CLIENT_KEY="",
    RONBCLIPS_TIKTOK_CLIENT_SECRET="",
)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from studio import accounts, auth, ctx, db, jobs, server  # noqa: E402

db.init()
fails = []
BASE = "https://clips.example.com"
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)
owner = auth.local_user()


def task_result(loc, tries=200):
    """Follow the task id in a redirect and read its answer the way the page does."""
    tid = parse_qs(urlparse(loc).query)["task"][0]
    for _ in range(tries):
        got = c.get(f"/api/tasks/{tid}").json()
        if got["status"] != "running":
            return got
        time.sleep(0.02)
    raise AssertionError("the connect task did not finish")


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


# ---- setting up an app keeps its secret to itself
SECRET = "yt-secret-value-456"
r = c.put("/api/apps", json={"platform": "youtube", "client_id": "g-client-123", "client_secret": SECRET}, headers=ORIGIN)
check("a YouTube app can be saved", r.status_code, 200)
check("the save answer does not repeat the secret", SECRET not in r.text, True)
apps = c.get("/api/apps").json()
check("the setup panel says the app is ready", apps["youtube"]["ready"], True)
check("the secret is not in the setup panel", SECRET not in json.dumps(apps), True)
check("the redirect address is shown", apps["youtube"]["redirect"], BASE + "/oauth/youtube/callback")

# ---- the Connect button makes a one-time state, and only that state works
before = len(accounts._states)
r = c.get("/oauth/youtube/start")
check(
    "Connect goes to the platform's own page",
    (r.status_code, r.headers.get("location", "").startswith("https://accounts.google.com/")),
    (303, True),
)
query = parse_qs(urlparse(r.headers["location"]).query)
state = query.get("state", [""])[0]
check(
    "a state is created for this browser",
    (len(accounts._states) > before, bool(state), query.get("client_id")),
    (True, True, ["g-client-123"]),
)
check("the state belongs to the local owner and to YouTube", accounts._states.get(state, (None, None, None))[::2], (owner.id, "youtube"))

r = c.get("/oauth/youtube/callback", params={"code": "code-1", "state": "not-the-state"})
check("a mismatched state is refused", r.status_code, 303)
loc = r.headers.get("location", "")
check("a mismatched state starts a background task", "task=" in loc and "platform=youtube" in loc, True)
t = task_result(loc)
check("a mismatched state explains why", (t["status"], "not connected" in t["error"]), ("error", True))
check("a wrong callback does not use up the real state", state in accounts._states, True)
check("an unknown platform does not exist", c.get("/oauth/myspace/start").status_code, 404)

# ---- connected accounts and their tokens are never sent to the page
TOKENS = ("ig-token-secret-123", "refresh-secret-456", "yt-access-secret-789")
with ctx.as_user(owner.id):
    accounts._put(
        {
            "id": "ig_17841",
            "platform": "instagram",
            "name": "@clipper",
            "added": time.time(),
            "token": TOKENS[0],
            "refresh_token": TOKENS[1],
            "access_token": TOKENS[2],
        }
    )
r = c.get("/api/accounts")
listed = r.json()
check("the account is listed", [(a["platform"], a["name"]) for a in listed], [("instagram", "@clipper")])
check("tokens are not in the account list", any(t in r.text for t in TOKENS), False)
check("the account list has no token fields", [k for k in listed[0] if "token" in k], [])

# ---- signed, one-clip links
with ctx.as_user(owner.id):
    job = jobs.create({"type": "file", "path": "", "name": "link.mp4"}, {}, enqueue=False)
clips = Path(job["dir"]) / "clips"
clips.mkdir(parents=True, exist_ok=True)
clip_bytes = b"\x00\x01signed-clip" * 400
(clips / "clip01.mp4").write_bytes(clip_bytes)
job["clips"] = [{"id": "clip01", "status": "done", "file": "clip01.mp4", "title": "Clip"}]
jobs.save(job)

with ctx.as_user(owner.id):
    link = accounts.clip_link(job["id"], "clip01.mp4")
    token = urlparse(link).path[len("/pub/") : -len(".mp4")]
    expired_link = accounts.clip_link(job["id"], "clip01.mp4", seconds=-5)
check("the link is a signed address for one clip", link.startswith(BASE + "/pub/") and link.endswith(".mp4"), True)
check("the signature reads back", accounts.read_clip_link(token), (owner.id, job["id"], "clip01.mp4"))

path = urlparse(link).path
served = c.get(path)
check("a valid link serves the clip without signing in", (served.status_code, served.content), (200, clip_bytes))
with patch.object(auth, "throttle", side_effect=auth.AuthError("slow down")):
    limited = c.get(path)
check(
    "a busy public clip link is rate limited",
    (limited.status_code, "Too many clip views" in limited.json().get("detail", "")),
    (429, True),
)
bad_path = path[:-4] + "x.mp4"
check("a changed link is refused", c.get(bad_path).status_code, 404)
check("a made-up link is refused", c.get("/pub/abc.def.mp4").status_code, 404)
check("a changed signature is not readable", accounts.read_clip_link(bad_path[len("/pub/") : -len(".mp4")]) is None, True)

expired_path = urlparse(expired_link).path
check("an expired link is refused", c.get(expired_path).status_code, 404)
check("an expired signature is not readable", accounts.read_clip_link(expired_path[len("/pub/") : -len(".mp4")]) is None, True)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
db.engine.dispose()
shutil.rmtree(os.environ["RONBCLIPS_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
