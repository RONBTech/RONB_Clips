"""Call every route the app registers, so a broken route or a broken gate shows up at once.

Runs against the code directly with an empty data folder and no network: startup jobs are not started, no video is
rendered, no file is really uploaded, and no provider is contacted. Routes that would start ffmpeg, store a big file,
open a folder window, finish an OAuth sign-in or post to a social site are called on their error/validation paths
instead. Anything that returns a server error fails the test."""

import os
import re
import shutil
import sys
import tempfile
import time

# Everything below must happen before studio is imported: that is when it reads its settings and opens its database.
DATA = tempfile.mkdtemp(prefix="pc-routes-")
BASE = "http://127.0.0.1:8765"
os.environ.update(
    RONBCLIPS_DATA=DATA,
    RONBCLIPS_STORAGE_FILE=os.path.join(DATA, "storage.json"),
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_AI_PROVIDER="",
    RONBCLIPS_AI_MODELS="",
    RONBCLIPS_AI_KEY="",
    RONBCLIPS_AI_KEY_FILE="",
    RONBCLIPS_AI_BASE_URL="",
    RONBCLIPS_PUBLIC_URL=BASE,
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from studio import db, server, tasks  # noqa: E402

db.init()
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False, raise_server_exceptions=False)
fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


# The app keeps included routers as lazy objects in newer FastAPI versions. Flatten whatever this version gives us.
def all_routes(app):
    out, seen = [], set()
    for route in getattr(app, "routes", []):
        inner = getattr(route, "original_router", None)  # app.include_router(...)
        mounted = getattr(route, "app", None)  # a classic Starlette mount
        for item in (inner or mounted or route).routes if hasattr((inner or mounted or route), "routes") else [route]:
            methods = getattr(item, "methods", None) or ()
            path = getattr(item, "path", "")
            if not path or not methods:
                continue
            for method in sorted(methods):
                key = (method, path)
                if key not in seen:
                    seen.add(key)
                    out.append((key, item))
    return out


ROUTES = all_routes(server.app)


def fill(path, args):
    """Turn a registered path such as /media/{jid}/{name} into an address to call."""
    return re.sub(r"\{([^}]+)\}", lambda m: str(args.get(m.group(1).split(":")[0], "x")), path)


def fixture_creator(name):
    r = c.post("/api/creators", json={"name": name}, headers=ORIGIN)
    check("make a fixture creator for " + name, r.status_code, 200)
    return r.json().get("id", "")


# Two fixtures: one for the edit route and one for the delete route (delete goes first in the router's order).
CID_EDIT = fixture_creator("Route edit fixture")
CID_DELETE = fixture_creator("Route delete fixture")

# A real background task, so /api/tasks/{id} can return a result rather than only an error.
TID = tasks.submit("route check", lambda: 42)
for _ in range(50):
    if tasks.get(TID)["status"] != "running":
        break
    time.sleep(0.02)

NO_JOB = "nosuchjob"  # long enough to pass the id check, short enough to be obviously absent
NO_CREATOR = "nosuchcreator"

# What to send to each registered route. A missing entry gets a harmless default below, so new routes are still tried.
PLAN = {
    # pages and public files
    ("GET", "/"): {"status": 200},
    ("GET", "/robots.txt"): {"status": 200},
    ("GET", "/sitemap.xml"): {"status": 200},
    ("GET", "/videos"): {"status": 200},
    ("GET", "/usage"): {"status": 200},
    ("GET", "/settings"): {"status": 200},
    ("GET", "/settings/{rest:path}"): {"args": {"rest": "ai"}, "status": 200},
    ("GET", "/creators"): {"status": 200},
    ("GET", "/creators/{rest:path}"): {"args": {"rest": "example"}, "status": 200},
    ("GET", "/healthz"): {"status": 200},
    ("GET", "/livez"): {"status": 200},
    ("GET", "/readyz"): {"status": 200},
    ("GET", "/health/detail"): {"status": 200},
    ("GET", "/brand/{name}"): {"args": {"name": "favicon.svg"}, "status": 200},
    ("GET", "/assets/{name}"): {"args": {"name": "main.js"}, "status": 200},
    ("GET", "/favicon.ico"): {"status": lambda s: s < 500},
    # dashboard reads
    ("GET", "/api/status"): {"status": 200},
    ("GET", "/api/overview"): {"status": 200},
    ("GET", "/api/settings"): {"status": 200},
    ("GET", "/api/accounts"): {"status": 200},
    ("GET", "/api/apps"): {"status": 200},
    ("GET", "/api/usage"): {"status": 200},
    ("GET", "/api/ai"): {"status": 200},
    ("GET", "/api/storage"): {"status": 200},
    ("GET", "/api/notifications"): {"status": 200},
    ("GET", "/api/sync"): {"status": 200},
    ("GET", "/api/tasks/{tid}"): {"args": {"tid": TID}, "status": 200},
    ("GET", "/api/jobs"): {"status": 200},
    ("GET", "/api/jobs/{jid}"): {"args": {"jid": NO_JOB}, "status": 404},
    ("GET", "/api/creators"): {"status": 200},
    ("GET", "/api/creators/{cid}/avatar"): {"args": {"cid": NO_CREATOR}, "status": 404},
    # reads that use validation errors instead of reaching out to a site or a provider
    ("GET", "/api/preview"): {"status": 400},
    ("GET", "/api/channel-preview"): {"status": 400},
    ("GET", "/api/clips.zip"): {"status": 400},
    ("GET", "/media/{jid}/{name}"): {"args": {"jid": NO_JOB, "name": "clip.mp4"}, "status": 404},
    ("GET", "/pub/{token}.mp4"): {"args": {"token": "not-a-real-link"}, "status": 404},
    ("GET", "/oauth/{platform}/start"): {"args": {"platform": "youtube"}, "status": 303},
    ("GET", "/oauth/{platform}/callback"): {"args": {"platform": "youtube"}, "status": 303},
    # dashboard writes: real, small and reversible
    ("PUT", "/api/settings"): {"json": {}, "status": 200},
    ("PUT", "/api/storage"): {"json": {"cancel": True}, "status": 200},
    ("POST", "/api/feedback"): {"json": {"kind": "idea", "message": "route coverage check"}, "status": 200},
    ("POST", "/api/notifications/read"): {"status": 200},
    ("POST", "/api/creators"): {"json": {"name": "Route POST fixture"}, "status": 200},
    ("PUT", "/api/creators/{cid}"): {"args": {"cid": CID_EDIT}, "json": {"name": "Route edit fixture renamed"}, "status": 200},
    ("DELETE", "/api/creators/{cid}"): {"args": {"cid": CID_DELETE}, "status": 200},
    ("DELETE", "/api/accounts/{account_id}"): {"args": {"account_id": "no-such-account"}, "status": 200},
    # writes with bad input: these exercise the route without starting a render, an upload or an outside call
    ("PUT", "/api/ai"): {"json": {"provider": "nonsense", "models": "m"}, "status": 400},
    # Long provider calls start a task and report failures through /api/tasks/{id}, not through the POST status.
    ("POST", "/api/ai/models"): {"json": {"provider": "nonsense"}, "status": 200},
    ("POST", "/api/ai/test"): {"status": 200},
    ("PUT", "/api/apps"): {"json": {"platform": "nonsense"}, "status": 400},
    ("POST", "/api/storage/open"): {"status": 400},  # TestClient is not on this computer
    ("POST", "/api/storage/pick"): {"status": 400},
    ("POST", "/api/jobs"): {"json": {"url": "not a link"}, "status": 400},
    ("POST", "/api/jobs/{jid}/clips/{cid}/render"): {"args": {"jid": NO_JOB, "cid": "noclyp"}, "json": {}, "status": 404},
    ("PUT", "/api/jobs/{jid}/clips/{cid}/text"): {"args": {"jid": NO_JOB, "cid": "noclyp"}, "json": {}, "status": 404},
    ("POST", "/api/jobs/{jid}/clips/{cid}/write"): {"args": {"jid": NO_JOB, "cid": "noclyp"}, "status": 404},
    ("DELETE", "/api/jobs/{jid}"): {"args": {"jid": NO_JOB}, "status": 404},
    ("DELETE", "/api/jobs/{jid}/clips/{cid}"): {"args": {"jid": NO_JOB, "cid": "noclyp"}, "status": 404},
    ("POST", "/api/bulk/delete"): {"json": {"videos": [], "clips": []}, "status": 200},
    ("POST", "/api/upload"): {"files": {"file": ("notes.txt", b"not a video", "text/plain")}, "status": 400},
    ("POST", "/api/upload/start"): {"json": {"name": "video.mp4", "size": 0}, "status": 400},
    ("PUT", "/api/upload/{upid}/{n}"): {"args": {"upid": "0" * 24, "n": 0}, "status": 404},
    ("POST", "/api/upload/{upid}/finish"): {"args": {"upid": "0" * 24}, "status": 404},
    ("DELETE", "/api/upload/{upid}"): {"args": {"upid": "0" * 24}, "status": 404},
    ("POST", "/api/creators/{cid}/live"): {"args": {"cid": NO_CREATOR}, "status": 404},
    ("POST", "/api/live/{jid}/stop"): {"args": {"jid": NO_JOB}, "status": 404},
    ("POST", "/api/live/{jid}/clip"): {"args": {"jid": NO_JOB}, "json": {"seconds": 30}, "status": 404},
    ("POST", "/api/alerts/test"): {"status": 400},
    ("POST", "/api/posts"): {"json": {"job": NO_JOB, "clip": "noclyp", "accounts": []}, "status": 404},
    ("POST", "/api/posts/{post_id}/{action}"): {"args": {"post_id": "nopost", "action": "cancel"}, "status": 400},
}

# An unplanned route is still tried with the smallest possible request, and only a server error is a failure.
exercised = []
for (method, path), _route in ROUTES:
    spec = PLAN.get((method, path), {})
    args = spec.get("args", {})
    url = fill(path, args)
    label = f"{method} {url}"
    options = dict(headers=ORIGIN) if method in ("POST", "PUT", "DELETE", "PATCH") else {}
    if "json" in spec:
        options["json"] = spec["json"]
    if "files" in spec:
        options["files"] = spec["files"]
    if method in ("POST", "PUT", "PATCH") and "json" not in options and "files" not in options and (method, path) not in PLAN:
        options["json"] = {}
    want = spec.get("status", lambda s: s < 500)
    try:
        got = getattr(c, method.lower())(url, **options)
        status = got.status_code
    except Exception as e:  # noqa: BLE001 - a route that throws instead of answering is a failure, not a crash
        status = repr(e)
    exercised.append((method, path))
    check(label, status, want)

# A few extra error paths that share a route with a call above, to prove the gate and the validation still work.
check("an unknown health path is not a server error", c.get("/health/not-here").status_code, lambda s: s < 500)
check("a task id that is not an id is refused", c.get("/api/tasks/not-an-id").status_code, 404)
check("an unknown OAuth platform is refused", c.get("/oauth/nonsense/start").status_code, 404)
check("an unknown OAuth callback is refused", c.get("/oauth/nonsense/callback").status_code, 404)
check(
    "a cross-site write is refused",
    c.post("/api/creators", json={"name": "no"}, headers={"Origin": "https://evil.example"}).status_code,
    403,
)
check("short feedback is refused", c.post("/api/feedback", json={"message": ""}, headers=ORIGIN).status_code, 400)
check("a bad storage path is refused", c.put("/api/storage", json={"path": "relative/path"}, headers=ORIGIN).status_code, 400)
check("an unknown post action is refused", c.post("/api/posts/nopost/nonsense", headers=ORIGIN).status_code, 404)
check(
    "a finished clip is required before posting",
    c.post("/api/posts", json={"job": NO_JOB, "clip": "noclyp"}, headers=ORIGIN).status_code,
    404,
)

print("SKIP: successful renders, real uploads, opening folders, OAuth redirects to providers, and actual posting")
print(f"ROUTES EXERCISED: {len(exercised)} / {len(ROUTES)}")
check("no registered route was missed", len(exercised), len(ROUTES))

c.close()
db.engine.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
