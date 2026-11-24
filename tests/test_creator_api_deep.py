"""Deep checks of the creator workspaces and live recording routes.

Runs against the code directly with its own empty data folder and no running server. Channel lookups, profile
refreshes and recording work are mocked; the workspaces themselves are real records in the temporary data folder.
"""

import os
import shutil
import sys
import tempfile
import time
from unittest.mock import Mock, patch

DATA = tempfile.mkdtemp(prefix="pc-creators-deep-")
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
    RONBCLIPS_PUBLIC_URL=BASE,
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from studio import (  # noqa: E402
    account,
    alerts,
    api_common,
    auth,
    config,
    creators,
    ctx,
    db,
    jobs,
    live,
    profiles,
    server,
)

db.init()
fails = []
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False, raise_server_exceptions=False)
owner = auth.local_user()
YT = "https://www.youtube.com/@someone"
TW = "https://www.twitch.tv/someone"


def task_of(r, tries=200):
    """Read a background task the way the page does, after a route hands back its id."""
    tid = r.json()["id"]
    for _ in range(tries):
        got = c.get(f"/api/tasks/{tid}").json()
        if got["status"] != "running":
            return got
        time.sleep(0.02)
    raise AssertionError("the task did not finish")


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


def add_creator(name, **kw):
    body = {"name": name, **kw}
    with patch.object(creators, "log"), patch("studio.profiles.refresh") as refresh:
        r = c.post("/api/creators", json=body, headers=ORIGIN)
    check("a workspace is made for " + name, r.status_code, 200)
    return r.json()["id"]


def get_creator(cid):
    with ctx.as_user(owner.id):
        return creators.get(cid)


# ------------------------------------------------------------------ list, add, validate
r = c.get("/api/creators")
check("the creator list opens", r.status_code, 200)
CID = add_creator("Deep Fixture")
r = c.get("/api/creators")
check("the new workspace is listed", [x["id"] for x in r.json()], [CID])
check("a listed workspace has a log", r.json()[0]["log"], [])

with patch.object(api_common, "creator_rows", return_value=[{"id": str(i), "name": f"Creator {i}"} for i in range(5)]) as rows:
    check(
        "creator list honours offset and limit",
        [x["id"] for x in c.get("/api/creators", params={"limit": 2, "offset": 1}).json()],
        ["1", "2"],
    )
    check("creator list clamps a negative offset", len(c.get("/api/creators", params={"offset": -3}).json()), 5)
    check("creator list caps a large limit", len(c.get("/api/creators", params={"limit": 99999}).json()), 5)
    check("creator rows are asked for each time", rows.call_count >= 3, True)

# Bad channel lists and links are refused before a workspace is written. A non-dict list is stopped by the form
# validator, so the route's own check is called directly as well.
r = c.post("/api/creators", json={"name": "Bad list", "channels": "not a dict"}, headers=ORIGIN)
check("a non-dict channel list is refused by the form", r.status_code, 422)
try:
    server.creator_api.channel_ok({"channels": "not a dict"})
    check("a non-dict channel list is refused by the route", False, True)
except HTTPException as e:
    check("a non-dict channel list is refused by the route", (e.status_code, "Bad channel list" in e.detail), (400, True))
r = c.post("/api/creators", json={"name": "Wrong platform", "channels": {"youtube": TW}}, headers=ORIGIN)
check("a link on the wrong platform is a 400", (r.status_code, "channel." in r.json().get("detail", "")), (400, True))
r = c.post("/api/creators", json={"name": "Bad site", "channel": "https://example.com/someone"}, headers=ORIGIN)
check("a link from an unknown site is a 400", (r.status_code, "YouTube, Twitch or Kick" in r.json().get("detail", "")), (400, True))
r = c.post("/api/creators", json={"name": "   "}, headers=ORIGIN)
check(
    "an empty name is refused by the creator store", (r.status_code, "Give the creator a name" in r.json().get("detail", "")), (400, True)
)
with patch.object(config, "MAX_CREATORS", 0):
    r = c.post("/api/creators", json={"name": "Too many"}, headers=ORIGIN)
    check("the creator limit is enforced", (r.status_code, "allows 0 creators" in r.json().get("detail", "")), (400, True))
with patch("studio.creator_api.creators.create", side_effect=ValueError("Give the creator a name.")):
    check("a creator error is mapped to a 400", c.post("/api/creators", json={"name": "x"}, headers=ORIGIN).status_code, 400)

with patch("studio.profiles.refresh") as refresh:
    r = c.post("/api/creators", json={"name": "Channel Fixture", "channel": YT, "channels": {"youtube": YT}}, headers=ORIGIN)
    check("a creator with a channel is made", r.status_code, 200)
    check("the channel is remembered", r.json()["channel"], YT)
    check("a new channel refreshes the profile", refresh.call_args.args[0], r.json()["id"])
    CH = r.json()["id"]

# ------------------------------------------------------------------ editing
r = c.put(f"/api/creators/{CID}", json={"name": "Renamed"}, headers=ORIGIN)
check("a creator can be renamed", (r.status_code, r.json()["name"]), (200, "Renamed"))
with patch.object(api_common, "throttle", side_effect=HTTPException(429, "Slow down")):
    check("too many edits are refused", c.put(f"/api/creators/{CID}", json={"name": "x"}, headers=ORIGIN).status_code, 429)
r = c.put(f"/api/creators/{CID}", json={"name": "x", "channels": {"youtube": TW}}, headers=ORIGIN)
check("a bad channel list is refused on edit", (r.status_code, "channel." in r.json().get("detail", "")), (400, True))
check("editing an absent creator is a 404", c.put("/api/creators/nosuchcreator", json={"name": "x"}, headers=ORIGIN).status_code, 404)
with patch("studio.creator_api.creators.update", side_effect=ValueError("Shortest clip must be shorter")):
    check("a bad edit is a 400", c.put(f"/api/creators/{CID}", json={"name": "x", "min_len": 90}, headers=ORIGIN).status_code, 400)

# A running recording picks up new settings immediately.
with patch("studio.creator_api.live.active_for") as active:
    session = Mock()
    with patch("studio.profiles.refresh"):
        r = c.put(f"/api/creators/{CID}", json={"name": "Live rename"}, headers=ORIGIN)
    check("a live creator can still be edited", r.status_code, 200)
    active.return_value = session
    with patch("studio.profiles.refresh"):
        c.put(f"/api/creators/{CID}", json={"name": "Live rename again"}, headers=ORIGIN)
    check("a running recording gets the new settings", session.settings["name"], "Live rename again")

with patch("studio.profiles.refresh") as refresh:
    r = c.put(f"/api/creators/{CH}", json={"name": "Channel Fixture", "channels": {"twitch": TW}}, headers=ORIGIN)
    check("changing channels is accepted", r.status_code, 200)
    check("changing channels refreshes profiles", refresh.called, True)
with patch("studio.live.check_now") as check_now:
    r = c.put(f"/api/creators/{CID}", json={"name": "Live rename again", "auto_watch": True}, headers=ORIGIN)
    check("auto watch can be turned on", r.status_code, 200)
    check("auto watch starts checking immediately", check_now.called, True)

# ------------------------------------------------------------------ avatars and channel preview
check("an absent creator has no avatar", c.get("/api/creators/nosuchcreator/avatar").status_code, 404)
check("no file means no avatar", c.get(f"/api/creators/{CH}/avatar").status_code, 404)

with ctx.as_user(owner.id):
    root = creators.root(CH)
    (root / "avatar-youtube.img").write_bytes(b"a small picture")
    cdata = get_creator(CH)
    cdata["channel"] = YT
    cdata["channels"] = {"youtube": YT}
    cdata["profiles"] = {"youtube": {"pic": "image/png"}}
    creators._mine()[CH] = cdata
r = c.get(f"/api/creators/{CH}/avatar")
check("an avatar can be served", (r.status_code, r.content, r.headers.get("content-type")), (200, b"a small picture", "image/png"))
check("an avatar is cached privately", "private, max-age=86400" in r.headers.get("cache-control", ""), True)
r = c.get(f"/api/creators/{CH}/avatar", params={"p": "youtube"})
check("an avatar can be asked for by platform", r.status_code, 200)
r = c.get(f"/api/creators/{CH}/avatar", params={"p": "kick"})
check("an unknown platform has no avatar", r.status_code, 404)
with ctx.as_user(owner.id):
    cdata = get_creator(CH)
    cdata["profiles"]["youtube"]["pic"] = "text/plain"
    creators._mine()[CH] = cdata
check("a picture that is not an image is refused", c.get(f"/api/creators/{CH}/avatar").status_code, 404)

check("an empty channel preview is a 400", c.get("/api/channel-preview").status_code, 400)
check("an unknown site preview is a 400", c.get("/api/channel-preview", params={"url": "https://example.com/x"}).status_code, 400)
with patch.object(auth, "throttle", side_effect=auth.AuthError("slow down")):
    r = c.get("/api/channel-preview", params={"url": YT})
    check("too many channel lookups are refused", (r.status_code, "Wait a moment" in r.json().get("detail", "")), (429, True))
with patch.object(profiles, "preview", return_value={"name": "Someone"}) as preview:
    r = c.get("/api/channel-preview", params={"url": "youtube.com/@someone"})
    t = task_of(r)
    check("a channel preview is returned", (r.status_code, t["status"], t["result"]), (200, "done", {"name": "Someone"}))
    check("the link passed to the lookup is trimmed", preview.call_args.args[0], "youtube.com/@someone")
for exc, fragment in (
    (ValueError("that channel is gone"), "gone"),
    (HTTPException(418, "teapot"), "teapot"),
    (RuntimeError("site broke"), "site broke"),
):
    with patch.object(profiles, "preview", side_effect=exc):
        r = c.get("/api/channel-preview", params={"url": YT})
        t = task_of(r)
        check(f"channel error {type(exc).__name__}", (r.status_code, t["status"], fragment in t["error"]), (200, "error", True))

# ------------------------------------------------------------------ going live
check("going live with no creator is a 404", c.post("/api/creators/nosuchcreator/live", headers=ORIGIN).status_code, 404)
with patch("studio.creator_api.creators.get", side_effect=ValueError("bad id")):
    check("a bad creator id is a 404", c.post("/api/creators/anything/live", headers=ORIGIN).status_code, 404)

live_creator = {
    "id": "livecreator",
    "channel": YT,
    "channels": {"youtube": YT, "twitch": TW},
    "profiles": {
        "youtube": {"channel": YT, "live": False},
        "twitch": {"channel": TW, "live": True},
    },
}
with (
    patch("studio.creator_api.creators.get", return_value=live_creator),
    patch("studio.live.start_session", side_effect=ValueError("not live right now")),
):
    check("a failed start is a 400", c.post("/api/creators/livecreator/live", headers=ORIGIN).status_code, 400)

with ctx.as_user(owner.id):
    live_job = jobs.create({"type": "live", "url": TW, "name": "live"}, {}, enqueue=False, kind="live")
    live_job["status"] = "live"
    jobs.save(live_job)


def start_with(body, creator=live_creator):
    with patch("studio.creator_api.creators.get", return_value=creator), patch("studio.live.start_session", return_value=live_job) as start:
        r = c.post("/api/creators/livecreator/live", json=body, headers=ORIGIN)
    return r, start.call_args.args[1] if start.called else None


r, url = start_with({"channel": TW})
check("going live opens", (r.status_code, r.json()["id"]), (200, live_job["id"]))
check("the asked channel is used", url, TW)
r, url = start_with({})
check("a live channel is preferred over the default", url, TW)
r, url = start_with({}, {**live_creator, "channel": TW})
check("a live default channel is kept", url, TW)
r, url = start_with({}, {**live_creator, "profiles": {}})
check("the chosen channel is the fallback", url, YT)


# ------------------------------------------------------------------ stopping and clipping a recording
class FakeSession:
    def __init__(self, ended=False, manual=None):
        self.ended = ended
        self.manual = [] if manual is None else manual
        self.stopped = False
        self.clips = []

    def stop(self):
        self.stopped = True

    def clip_last(self, seconds):
        self.clips.append(seconds)


check("stopping a recording that already ended is a 404", c.post("/api/live/nosuchjob/stop", headers=ORIGIN).status_code, 404)
session = FakeSession()
with patch.object(live, "mine", return_value=session):
    r = c.post("/api/live/anything/stop", headers=ORIGIN)
    check("a recording can be stopped", (r.status_code, r.json(), session.stopped), (200, {"ok": True}, True))

check("clipping a recording that ended is a 404", c.post("/api/live/nosuchjob/clip", json={"seconds": 30}, headers=ORIGIN).status_code, 404)
ended = FakeSession(ended=True)
with patch.object(live, "mine", return_value=ended):
    check("an ended recording cannot be clipped", c.post("/api/live/anything/clip", json={"seconds": 30}, headers=ORIGIN).status_code, 404)
with patch.object(live, "mine", return_value=FakeSession()), patch.object(auth, "throttle", side_effect=auth.AuthError("slow down")):
    r = c.post("/api/live/anything/clip", json={"seconds": 30}, headers=ORIGIN)
    check("too many live clips are refused", (r.status_code, "last clips" in r.json().get("detail", "")), (429, True))
with patch.object(live, "mine", return_value=FakeSession(manual=[1, 2, 3])), patch.object(api_common, "throttle"):
    check("three pending clips block another", c.post("/api/live/anything/clip", json={"seconds": 30}, headers=ORIGIN).status_code, 429)

for seconds, want in ((1, 5), (30, 30), (999, 180)):
    s = FakeSession()
    with patch.object(live, "mine", return_value=s), patch.object(api_common, "throttle"):
        r = c.post("/api/live/anything/clip", json={"seconds": seconds}, headers=ORIGIN)
        check(f"live clip length {seconds}s is clamped", (r.status_code, s.clips), (200, [want]))
for exc in (ValueError("bad seconds"), TypeError("bad seconds")):
    with (
        patch.object(live, "mine", return_value=FakeSession()),
        patch.object(api_common, "throttle"),
        patch.object(FakeSession, "clip_last", side_effect=exc),
    ):
        check(
            f"live clip error {type(exc).__name__}",
            c.post("/api/live/anything/clip", json={"seconds": 30}, headers=ORIGIN).status_code,
            400,
        )

# ------------------------------------------------------------------ test alerts
with patch.object(alerts, "configured", return_value=False):
    r = c.post("/api/alerts/test", headers=ORIGIN)
    check("an unconfigured alert is a 400", (r.status_code, "Discord webhook" in r.json().get("detail", "")), (400, True))
with (
    patch.object(alerts, "configured", return_value=True),
    patch.object(auth, "throttle", side_effect=auth.AuthError("wait a few minutes")),
):
    r = c.post("/api/alerts/test", headers=ORIGIN)
    check("a throttled alert shows the reason", (r.status_code, "wait a few minutes" in r.json().get("detail", "")), (429, True))
with patch.object(alerts, "configured", return_value=True), patch.object(alerts, "send", return_value=["webhook failed"]):
    r = c.post("/api/alerts/test", headers=ORIGIN)
    t = task_of(r)
    check("a failed alert is reported", (r.status_code, t["status"], "webhook failed" in t["error"]), (200, "error", True))
with patch.object(alerts, "configured", return_value=True), patch.object(alerts, "send", return_value=[]):
    r = c.post("/api/alerts/test", headers=ORIGIN)
    t = task_of(r)
    check("a test alert can be sent", (r.status_code, t["status"], t["result"]), (200, "done", {"ok": True}))
    check("the alert runs in the task pool", t["name"], "Send test alert")

# ------------------------------------------------------------------ deleting a creator
with patch.object(api_common, "throttle", side_effect=HTTPException(429, "Slow down")):
    check("too many deletions are refused", c.delete(f"/api/creators/{CID}", headers=ORIGIN).status_code, 429)
with patch("studio.creator_api.account.delete_creator", side_effect=account.Busy("Stop the recording first")):
    r = c.delete(f"/api/creators/{CID}", headers=ORIGIN)
    check("a busy creator is a 409", (r.status_code, "Stop the recording" in r.json().get("detail", "")), (409, True))
with patch("studio.creator_api.account.delete_creator", return_value={"ok": False}):
    check("an absent creator is a 404 on delete", c.delete("/api/creators/nosuchcreator", headers=ORIGIN).status_code, 404)
with patch("studio.creator_api.account.delete_creator", return_value={"ok": True, "videos": 2}) as deleted:
    r = c.delete(f"/api/creators/{CID}", headers=ORIGIN)
    check("a creator can be deleted", (r.status_code, r.json()), (200, {"ok": True, "videos": 2}))
    check("the right creator is deleted", deleted.call_args.args[0], CID)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
c.close()
db.engine.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
