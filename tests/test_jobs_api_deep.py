"""Deep checks of the video and clip routes: creation, preview, editing, deletion and downloads.

Runs against the code directly with its own empty data folder and no running server. Video work, site lookups and
AI calls are mocked; the jobs and clips themselves are real records in the temporary data folder.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

DATA = tempfile.mkdtemp(prefix="pc-jobs-deep-")
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
    api_common,
    auth,
    brain,
    ctx,
    db,
    jobs,
    media,
    server,
)

db.init()
fails = []
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False, raise_server_exceptions=False)
owner = auth.local_user()


def task_of(r, tries=200):
    """Read a background task the way the page does, after a route hands back its id."""
    tid = r.json()["id"]
    for _ in range(tries):
        got = c.get(f"/api/tasks/{tid}").json()
        if got["status"] != "running":
            return got
        time.sleep(0.02)
    raise AssertionError("the task did not finish")


GOOD = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


def clip(cid="clip01", status="done", **kw):
    out = {
        "id": cid,
        "status": status,
        "file": cid + ".mp4",
        "title": "A clip",
        "description": "Some words",
        "hashtags": ["clips"],
        "start": 1.0,
        "end": 11.0,
        "score": 90,
        "category": "fun",
        "hook": "a hook",
        "style": "bounce",
        "layout": "blur",
        "captions": True,
        "version": 1,
    }
    out.update(kw)
    return out


def make_job(status="done", clips=None, source=None, **kw):
    with ctx.as_user(owner.id):
        job = jobs.create(source or {"type": "file", "path": "", "name": "job.mp4"}, {}, enqueue=False)
    job["status"] = status
    job["clips"] = clips if clips is not None else []
    job.update(kw)
    jobs.save(job)
    return job


def clip_file(job, cid="clip01"):
    d = Path(job["dir"]) / "clips"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{cid}.mp4"
    f.write_bytes(b"a finished clip, stored for the download check" * 20)
    return f


def get_job(jid):
    with ctx.as_user(owner.id):
        return jobs.get(jid)


# ------------------------------------------------------------------ listing and reading
r = c.get("/api/jobs")
check("the job list opens", r.status_code, 200)
check("an empty list to start with", r.json(), [])
check("paths and ids are not leaked", "dir" not in r.text and "user" not in r.text, True)

j_done = make_job(clips=[clip()])
clip_file(j_done)
r = c.get("/api/jobs")
check("a created job is listed", [j["id"] for j in r.json()], [j_done["id"]])
check("a listed job hides its folder", "dir" not in r.json()[0], True)

with patch.object(jobs, "list_jobs", return_value=[j_done, j_done, j_done]) as listed:
    check("offset and limit slice the list", len(c.get("/api/jobs", params={"limit": 2, "offset": 1}).json()), 2)
    check("the list is only asked once", listed.call_count, 1)
    check("a negative offset is clamped", len(c.get("/api/jobs", params={"offset": -5}).json()), 3)
    check("a huge limit is capped", len(c.get("/api/jobs", params={"limit": 99999}).json()), 3)

r = c.get("/api/jobs/" + j_done["id"])
check("one job can be read", (r.status_code, r.json()["id"]), (200, j_done["id"]))
check("a bad job id is a 404", c.get("/api/jobs/short").status_code, 404)
check("an absent job is a 404", c.get("/api/jobs/nosuchjob").status_code, 404)

# ------------------------------------------------------------------ link preview
check("a bad link is refused", c.get("/api/preview", params={"url": "not a link"}).status_code, 400)
with patch.object(auth, "throttle", side_effect=auth.AuthError("slow down")):
    r = c.get("/api/preview", params={"url": GOOD})
    check("too many previews are refused", (r.status_code, "Wait a moment" in r.json().get("detail", "")), (429, True))
with patch.object(media, "preview", return_value={"title": "A video"}) as preview:
    r = c.get("/api/preview", params={"url": GOOD})
    t = task_of(r)
    check("a preview is returned", (r.status_code, t["status"], t["result"]), (200, "done", {"title": "A video"}))
    check("the site lookup is used", preview.call_args.args[0], GOOD)
for exc, fragment in (
    (media.YouTubeBlocked("YouTube is refusing us"), "refusing"),
    (ValueError("that link is private"), "private"),
    (HTTPException(418, "teapot"), "teapot"),
    (RuntimeError("site went strange"), "strange"),
):
    with patch.object(media, "preview", side_effect=exc):
        r = c.get("/api/preview", params={"url": GOOD})
        t = task_of(r)
        check(f"preview error {type(exc).__name__}", (r.status_code, t["status"], fragment in t["error"]), (200, "error", True))

# ------------------------------------------------------------------ making a job from a link
with patch.object(api_common, "quota", side_effect=HTTPException(507, "Disk full")):
    check("a full disk refuses a new job", c.post("/api/jobs", json={"url": GOOD}, headers=ORIGIN).status_code, 507)
with patch.object(api_common, "quota"), patch.object(api_common, "throttle", side_effect=HTTPException(429, "Slow down")):
    check("too many new jobs are refused", c.post("/api/jobs", json={"url": GOOD}, headers=ORIGIN).status_code, 429)
with patch.object(api_common, "quota"):
    check("a bad link makes no job", c.post("/api/jobs", json={"url": "hello"}, headers=ORIGIN).status_code, 400)
    with patch.object(media, "check_youtube", side_effect=media.YouTubeBlocked("blocked")):
        check("a blocked youtube is reported", c.post("/api/jobs", json={"url": GOOD}, headers=ORIGIN).status_code, 503)
    check(
        "a missing creator is a 404", c.post("/api/jobs", json={"url": GOOD, "creator": "nosuchcreator"}, headers=ORIGIN).status_code, 404
    )
    with patch.object(api_common, "job_slots", side_effect=HTTPException(429, "Too busy")):
        check("a full queue refuses a job", c.post("/api/jobs", json={"url": GOOD}, headers=ORIGIN).status_code, 429)
    with patch.object(jobs, "create", side_effect=ValueError("bad options")):
        r = c.post("/api/jobs", json={"url": GOOD}, headers=ORIGIN)
        check("bad options are reported", (r.status_code, "bad options" in r.text), (400, True))
    with patch.object(jobs, "create", side_effect=TypeError("wrong type")):
        check(
            "a wrongly typed option is explained",
            "options aren't valid" in c.post("/api/jobs", json={"url": GOOD}, headers=ORIGIN).text,
            True,
        )

with patch.object(api_common, "quota"):
    other = "https://www.twitch.tv/someone/v/123"
    media._PREVIEWS[other] = {"title": "Stream", "uploader": "Someone", "duration": 42}
    with patch.object(media, "save_thumb", return_value=True) as thumb:
        r = c.post("/api/jobs", json={"url": other, "options": {"quality": 360}}, headers=ORIGIN)
        check("a link with a cached preview is accepted", r.status_code, 200)
        check("the cached title is shown", (r.json()["title"], r.json()["uploader"], r.json()["duration"]), ("Stream", "Someone", 42))
        check("the cached thumbnail is saved", (r.json()["thumb"], thumb.called), (True, True))
    media._PREVIEWS.pop(other, None)

with patch.object(api_common, "quota"):
    r = c.post("/api/jobs", json={"url": "https://www.twitch.tv/another/v/456", "options": {"quality": 360}}, headers=ORIGIN)
    check("an ordinary link makes a job", (r.status_code, r.json()["source"]["url"]), (200, "https://www.twitch.tv/another/v/456"))
    check("the job has no thumbnail yet", r.json().get("thumb", False), False)
    NEW_JOB = r.json()["id"]

# ------------------------------------------------------------------ re-render, text and AI rewrite
render_job = make_job(clips=[clip()])
CID = render_job["clips"][0]["id"]
with patch.object(jobs, "request_render", return_value=None) as request:
    r = c.post(f"/api/jobs/{render_job['id']}/clips/{CID}/render", json={"start": 2, "end": 8, "captions": False}, headers=ORIGIN)
    check("a finished clip can be re-cut", (r.status_code, request.called), (200, True))
    check("the render gets the clip id", request.call_args.args[:3], (render_job["id"], CID, {"start": 2, "end": 8, "captions": False}))
    with patch.object(jobs, "request_render", side_effect=ValueError("Those times aren't valid.")):
        check(
            "a bad re-cut is reported",
            c.post(f"/api/jobs/{render_job['id']}/clips/{CID}/render", json={"start": 2}, headers=ORIGIN).status_code,
            400,
        )
check(
    "a missing clip cannot be re-cut", c.post(f"/api/jobs/{render_job['id']}/clips/noclyp/render", json={}, headers=ORIGIN).status_code, 404
)
queued_job = make_job(status="queued", clips=[clip()])
check(
    "a clip in a queued job cannot be re-cut",
    c.post(f"/api/jobs/{queued_job['id']}/clips/clip01/render", json={}, headers=ORIGIN).status_code,
    409,
)
rendering_job = make_job(clips=[clip(status="rendering")])
check(
    "a rendering clip cannot be re-cut",
    c.post(f"/api/jobs/{rendering_job['id']}/clips/clip01/render", json={}, headers=ORIGIN).status_code,
    409,
)

with patch.object(jobs, "save_post_text") as save_text:
    r = c.put(f"/api/jobs/{render_job['id']}/clips/{CID}/text", json={"title": "New title", "hashtags": "one two"}, headers=ORIGIN)
    check("clip text can be saved", (r.status_code, save_text.called), (200, True))
    with patch.object(jobs, "save_post_text", side_effect=ValueError("bad hashtags")):
        check(
            "bad clip text is reported", c.put(f"/api/jobs/{render_job['id']}/clips/{CID}/text", json={}, headers=ORIGIN).status_code, 400
        )
check("text of a missing clip is a 404", c.put(f"/api/jobs/{render_job['id']}/clips/noclyp/text", json={}, headers=ORIGIN).status_code, 404)

with patch.object(brain, "post_text", return_value={"title": "AI title"}):
    r = c.post(f"/api/jobs/{render_job['id']}/clips/{CID}/write", headers=ORIGIN)
    t = task_of(r)
    check("AI can write post text", (r.status_code, t["status"], t["result"]), (200, "done", {"title": "AI title"}))
    check("the AI work is named in the task list", t["name"], "Write post text")
for exc in (ValueError("no key"), RuntimeError("provider down")):
    with patch.object(brain, "post_text", side_effect=exc):
        r = c.post(f"/api/jobs/{render_job['id']}/clips/{CID}/write", headers=ORIGIN)
        t = task_of(r)
        check(
            f"AI rewrite error {type(exc).__name__}",
            (r.status_code, t["status"], str(exc) in t["error"]),
            (200, "error", True),
        )
check("a missing clip cannot be rewritten", c.post(f"/api/jobs/{render_job['id']}/clips/noclyp/write", headers=ORIGIN).status_code, 404)

# ------------------------------------------------------------------ deleting jobs and clips
check("a missing job cannot be deleted", c.delete("/api/jobs/nosuchjob", headers=ORIGIN).status_code, 404)
check("a queued job cannot be deleted", c.delete("/api/jobs/" + queued_job["id"], headers=ORIGIN).status_code, 409)
with patch("studio.live.SESSIONS", {NEW_JOB: object()}):
    check("a recording in progress cannot be deleted", c.delete("/api/jobs/" + NEW_JOB, headers=ORIGIN).status_code, 409)
check("a finished job can be deleted", c.delete("/api/jobs/" + render_job["id"], headers=ORIGIN).status_code, 200)
check("the deleted job is gone", get_job(render_job["id"]), None)

check("deleting a clip from a missing job is a 404", c.delete("/api/jobs/nosuchjob/clips/clip01", headers=ORIGIN).status_code, 404)
check("deleting a clip that is not there is a 404", c.delete(f"/api/jobs/{j_done['id']}/clips/noclyp", headers=ORIGIN).status_code, 404)
with patch("studio.job_api.account.delete_clip", side_effect=account.Busy("still rendering")):
    r = c.delete(f"/api/jobs/{j_done['id']}/clips/clip01", headers=ORIGIN)
    check("a busy clip is a 409", (r.status_code, "still rendering" in r.json().get("detail", "")), (409, True))
check("a finished clip can be deleted", c.delete(f"/api/jobs/{j_done['id']}/clips/clip01", headers=ORIGIN).status_code, 200)
check("the clip is gone from the job", [x["id"] for x in get_job(j_done["id"])["clips"]], [])
check("the clip's file is gone", (Path(j_done["dir"]) / "clips" / "clip01.mp4").exists(), False)

# ------------------------------------------------------------------ bulk delete
with patch.object(api_common, "throttle", side_effect=HTTPException(429, "Slow down")):
    check("too many bulk deletes are refused", c.post("/api/bulk/delete", json={}, headers=ORIGIN).status_code, 429)
with patch("studio.job_api.account.delete_many", return_value={"videos": 1, "clips": 2, "skipped": 0}) as many:
    r = c.post(
        "/api/bulk/delete",
        json={
            "videos": [j_done["id"], "short", 12, None],
            "clips": [[j_done["id"], "clip01"], ["x" * 70, "c"], "not a pair", 4],
        },
        headers=ORIGIN,
    )
    check("a bulk delete is accepted", (r.status_code, r.json()), (200, {"videos": 1, "clips": 2, "skipped": 0}))
    check("only valid video ids are passed on", many.call_args.args[0], [j_done["id"]])
    check("only valid pairs are passed on", many.call_args.args[1], [(j_done["id"], "clip01")])
with patch("studio.job_api.account.delete_many", return_value={"videos": 0, "clips": 0, "skipped": 0}) as many:
    c.post("/api/bulk/delete", json={"videos": "not a list", "clips": "not a list"}, headers=ORIGIN)
    check("bad lists are ignored", many.call_args.args, ([], []))

# ------------------------------------------------------------------ a clip download as a zip
with patch.object(api_common, "throttle", side_effect=HTTPException(429, "Slow down")):
    check("too many zip downloads are refused", c.get("/api/clips.zip", params={"ids": "a.b"}).status_code, 429)
with patch.object(api_common, "quota", side_effect=HTTPException(507, "Disk full")):
    check("a full disk refuses a zip", c.get("/api/clips.zip", params={"ids": "a.b"}).status_code, 507)
with patch("studio.job_api.account.zip_clips", side_effect=ValueError("None of those clips are ready.")):
    r = c.get("/api/clips.zip", params={"ids": "a.b"})
    check("no ready clips is a 400", (r.status_code, r.json().get("detail", "")), (400, "None of those clips are ready."))

zip_job = make_job(clips=[clip(cid="zipclip", file="zipclip.mp4", title="Zip clip")])
clip_file(zip_job, "zipclip")
zip_path = Path(DATA) / "made.zip"
zip_path.write_bytes(b"PK\x03\x04" + b"zip bytes" * 30)
with patch("studio.job_api.account.zip_clips", return_value=zip_path) as zipped:
    r = c.get("/api/clips.zip", params={"ids": f"{zip_job['id']}.zipclip,{zip_job['id']}.other,nodot"})
    check("a zip download opens", r.status_code, 200)
    check("the zip has a safe name", "ronbclips-clips-2.zip" in r.headers.get("content-disposition", ""), True)
    check("the zip is not cached", "no-store" in r.headers.get("cache-control", ""), True)
    check("valid pairs are passed through", zipped.call_args.args[0], [(zip_job["id"], "zipclip"), (zip_job["id"], "other")])
    check("the finished zip is removed", zip_path.exists(), False)

# ------------------------------------------------------------------ single media file
media_job = make_job(clips=[clip()])
mf = clip_file(media_job)
check("a bad media filename is a 404", c.get(f"/media/{media_job['id']}/nope.exe").status_code, 404)
check("a missing media job is a 404", c.get("/media/nosuchjob/clip01.mp4").status_code, 404)
check("a missing media file is a 404", c.get(f"/media/{media_job['id']}/absent.mp4").status_code, 404)
r = c.get(f"/media/{media_job['id']}/clip01.mp4")
check("a clip can be streamed", (r.status_code, r.content), (200, mf.read_bytes()))
r = c.get(f"/media/{media_job['id']}/clip01.mp4", params={"download": 1})
check("a clip can be downloaded by name", (r.status_code, "attachment" in r.headers.get("content-disposition", "")), (200, True))
check(
    "a jpeg name is allowed too",
    (lambda p: (p.write_bytes(b"jpg"), c.get(f"/media/{media_job['id']}/{p.name}").status_code)[1])(
        Path(media_job["dir"]) / "clips" / "cover.jpg"
    ),
    200,
)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
c.close()
db.engine.dispose()
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
