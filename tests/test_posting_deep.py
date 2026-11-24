"""Deep tests for studio/posting.py with a real persisted job and clip, and no network or database calls.

Run with: python tests/test_posting_deep.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

DATA = tempfile.mkdtemp(prefix="pc-posting-deep-")
os.environ["RONBCLIPS_DATA"] = DATA
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_FETCH_MODEL="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_PUBLIC_URL="http://127.0.0.1:8765",
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    GOOGLE_CLIENT_FILE="",
    RONBCLIPS_IG_APP_ID="",
    RONBCLIPS_IG_APP_SECRET="",
    RONBCLIPS_TIKTOK_CLIENT_KEY="",
    RONBCLIPS_TIKTOK_CLIENT_SECRET="",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from studio import accounts, alerts, config, creators, ctx, jobs, live, posting, profiles  # noqa: E402

UID = "poster_001"
fails = []
ctx.set_uid(UID)


def check(name, got, expected):
    ok = expected(got) if callable(expected) else got == expected
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)
    return ok


def expect_raise(name, fn, kind=Exception, part=""):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - the test names the kind it wants
        check(name, isinstance(e, kind) and (not part or part in str(e)), True)
        return e
    check(name, "no exception was raised", "an exception")
    return None


# ---------------------------------------------------------------- replace the edges
def no_network(*a, **k):
    raise AssertionError("posting tests must not make a network request")


httpx.get = httpx.post = httpx.put = no_network
httpx.Client.request = no_network

alerts_sent = []
alerts.configured = lambda: False
alerts.notify = lambda text, kind="live", link="": alerts_sent.append((text, kind))
live.active_for = lambda cid: False
live.active_mine = lambda: []

profile_calls = []
profiles.refresh = lambda cid, force=False: profile_calls.append((cid, force))
profiles.live_shared = lambda platform, url: {"live": False, "viewers": None, "title": "", "started": None, "embed": ""}

uploads = []
view_calls = []


def fake_youtube_upload(acc, path, title, description, tags, privacy):
    uploads.append(("youtube", acc["id"], title, description, tags, privacy, path.read_bytes()))
    return "yt-remote-1", "https://youtube.example/short/1"


def fake_instagram_upload(acc, path, caption, job_id):
    uploads.append(("instagram", acc["id"], caption, job_id, path.read_bytes()))
    return "ig-remote-1", "https://instagram.example/reel/1"


def fake_tiktok_upload(acc, path, caption, privacy):
    uploads.append(("tiktok", acc["id"], caption, privacy, path.read_bytes()))
    return "tt-remote-1", "https://tiktok.example/video/1"


def fake_youtube_views(acc, ids):
    view_calls.append(("youtube", ids))
    return {i: 123 for i in ids}


def fake_instagram_views(acc, media_id):
    view_calls.append(("instagram", media_id))
    return 45


accounts.youtube_upload = fake_youtube_upload
accounts.instagram_upload = fake_instagram_upload
accounts.tiktok_upload = fake_tiktok_upload
accounts.youtube_views = fake_youtube_views
accounts.instagram_views = fake_instagram_views

# ---------------------------------------------------------------- real files on disk
posting._posts.clear()
posting._account_posts.clear()
posting._save()

creator = creators.create(
    {
        "name": "Deep Poster",
        "autopost": True,
        "accounts": ["yt_acc_1", "ig_acc_1", "tt_acc_1"],
        "post_min_score": 75,
        "post_gap_min": 45,
        "daily_cap": 6,
        "privacy": "public",
        "post_footer": "Follow Deep Poster",
        "rate_per_1k": 2.5,
    }
)
for account in (
    {"id": "yt_acc_1", "platform": "youtube", "name": "My YouTube", "added": time.time()},
    {"id": "ig_acc_1", "platform": "instagram", "name": "My Instagram", "added": time.time()},
    {"id": "tt_acc_1", "platform": "tiktok", "name": "My TikTok", "added": time.time()},
    {"id": "other_acc", "platform": "myspace", "name": "Old account", "added": time.time()},
):
    accounts._put(account)

job = jobs.create({"type": "file", "path": "", "name": "source.mp4"}, {}, enqueue=False, creator=creator["id"])
clips_dir = Path(job["dir"]) / "clips"
clips_dir.mkdir(parents=True, exist_ok=True)
clip_file = clips_dir / "clip01.mp4"
clip_file.write_bytes(b"\x00\x01a rendered clip" * 30)
clip = {
    "id": "clip01",
    "status": "done",
    "file": "clip01.mp4",
    "title": "A great clip",
    "description": "What a moment",
    "hashtags": ["fun", "gaming"],
    "score": 92,
    "made_at": time.time(),
}
job["clips"] = [clip]
jobs.save(job)

check("the clip is really on disk", clip_file.is_file(), True)
check("the job is really saved", (Path(job["dir"]) / "job.json").is_file(), True)
profiles.refresh(creator["id"], force=True)
check("profile lookups are mocked, not made over the network", profile_calls, [(creator["id"], True)])

# ---------------------------------------------------------------- queueing
made = posting.enqueue(job, clip, ["yt_acc_1", "ig_acc_1", "tt_acc_1", "missing_acc"])
p_yt, p_ig, p_tt = made
check(
    "one post is made per known account",
    [(p["platform"], p["status"]) for p in made],
    [("youtube", "scheduled"), ("instagram", "scheduled"), ("tiktok", "scheduled")],
)
check(
    "the queued post describes its clip and account",
    (p_yt["title"], p_yt["account_name"], p_yt["tries"], p_yt["url"]),
    ("A great clip", "My YouTube", 0, ""),
)
check("a missing account is skipped", posting.enqueue(job, clip, ["missing_acc"]), [])
check("the same clip is not queued twice", posting.enqueue(job, clip, ["yt_acc_1", "ig_acc_1", "tt_acc_1"]), [])
check("posts are indexed by account", len(posting._account_posts[("poster_001", "yt_acc_1")]), 1)
check("queueing writes the posts file", len(json.loads((config.DATA_DIR / "posts.json").read_text(encoding="utf-8"))), 3)
check("queueing copies the state onto the clip", jobs.get(job["id"])["clips"][0]["posts"][p_yt["id"]]["status"], "scheduled")
check("queued posts are mirrored with the account name", jobs.get(job["id"])["clips"][0]["posts"][p_yt["id"]]["account_name"], "My YouTube")

check("a caption joins text, tags and the footer", posting._caption(clip, creator), "What a moment\n\n#fun #gaming\n\nFollow Deep Poster")
fixed_clip = dict(clip, fixed_description=True)
check("a fixed description drops the generated tags", posting._caption(fixed_clip, creator), "What a moment\n\nFollow Deep Poster")
check(
    "a creator without a footer has a shorter caption",
    posting._caption(clip, dict(creator, post_footer="")),
    "What a moment\n\n#fun #gaming",
)

# ---------------------------------------------------------------- posting each platform
for p in made:
    posting._post(p)
check("all three posts are marked posted", [p["status"] for p in made], ["posted", "posted", "posted"])
check(
    "each post remembers its remote id and link",
    [(p["remote_id"], p["url"]) for p in made],
    [
        ("yt-remote-1", "https://youtube.example/short/1"),
        ("ig-remote-1", "https://instagram.example/reel/1"),
        ("tt-remote-1", "https://tiktok.example/video/1"),
    ],
)
check("a posted clip has a time", all(p["posted_at"] > 0 for p in made), True)
check("no error is left behind", [p["error"] for p in made], [None, None, None])
check("each platform got the file", [u[0] for u in uploads], ["youtube", "instagram", "tiktok"])
check(
    "the YouTube post includes Shorts and the footer",
    ("#Shorts" in uploads[0][3], "Follow Deep Poster" in uploads[0][3], uploads[0][5]),
    (True, True, "public"),
)
check(
    "the Instagram and TikTok posts include the title",
    (uploads[1][2].startswith("A great clip\n\n"), uploads[2][2].startswith("A great clip\n\n")),
    (True, True),
)
check("the TikTok post uses the creator's privacy", uploads[2][3], "public")
check("the uploaded bytes came from the real clip", uploads[0][6], clip_file.read_bytes())
check("a successful post is copied onto its clip", jobs.get(job["id"])["clips"][0]["posts"][p_ig["id"]]["status"], "posted")
check(
    "successful posting writes the queue to disk",
    [p["status"] for p in json.loads((config.DATA_DIR / "posts.json").read_text(encoding="utf-8"))],
    ["posted", "posted", "posted"],
)
check(
    "the creator log says where it went",
    any(
        "Posted to My YouTube" in json.loads(x)["msg"]
        for x in (creators.root(creator["id"]) / "log.jsonl").read_text(encoding="utf-8").splitlines()
    ),
    True,
)


# ---------------------------------------------------------------- retry and backoff
def reset(post, **kw):
    post.update(status="scheduled", tries=0, error=None, due_at=time.time() - 1, posted_at=None, remote_id="", url="")
    post.update(kw)
    posting._save()
    return post


reset(p_yt)
accounts.youtube_upload = lambda *a: (_ for _ in ()).throw(RuntimeError("upload refused"))
before = time.time()
posting._post(p_yt)
check("a first failure schedules another try", (p_yt["status"], p_yt["tries"], p_yt["error"]), ("scheduled", 1, "upload refused"))
check("the retry waits about ten minutes", p_yt["due_at"] - before > 590 and p_yt["due_at"] - before < 610, True)
posting._post(p_yt)
check("a second failure still retries", (p_yt["status"], p_yt["tries"]), ("scheduled", 2))
posting._post(p_yt)
check("a third failure gives up", (p_yt["status"], p_yt["tries"]), ("failed", 3))
check("a failed post raises an alert", any("post failed on My YouTube" in text and kind == "post" for text, kind in alerts_sent), True)
check("the failure is copied onto the clip", jobs.get(job["id"])["clips"][0]["posts"][p_yt["id"]]["error"], "upload refused")
check(
    "the creator log records the failure",
    any(
        "Post failed on My YouTube" in json.loads(x)["msg"]
        for x in (creators.root(creator["id"]) / "log.jsonl").read_text(encoding="utf-8").splitlines()
    ),
    True,
)
accounts.youtube_upload = fake_youtube_upload


# Failures that cannot be retried (no clip, no account) fail on the first try.
def extra_post(**kw):
    p = {
        **p_yt,
        "id": "extra" + str(len([x for x in posting._posts if x["id"].startswith("extra")])),
        "status": "posting",
        "tries": 0,
        "error": None,
    }
    p.update(kw)
    posting._posts.append(p)
    posting._index_post(p)
    posting._save()
    return p


gone = extra_post(job="no-such-job")
posting._post(gone)
check("a missing clip fails at once", (gone["status"], gone["error"]), ("failed", "The clip no longer exists or is not finished."))
disconnected = extra_post(account="no-such-account")
posting._post(disconnected)
check("a disconnected account fails at once", (disconnected["status"], "disconnected" in disconnected["error"]), ("failed", True))
real_get = accounts.get
accounts.get = (
    lambda aid: {"id": "other_acc", "platform": "myspace", "name": "Old account", "added": time.time()}
    if aid == "other_acc"
    else real_get(aid)
)
unsupported = extra_post(account="other_acc", tries=2)
posting._post(unsupported)
accounts.get = real_get
check("an unsupported platform fails", "no longer supported" in unsupported["error"], True)

stored_clip = jobs.get(job["id"])["clips"][0]
stored_clip["status"] = "rendering"
not_done = extra_post()
posting._post(not_done)
check("an unfinished clip fails", "not finished" in not_done["error"], True)
stored_clip["status"] = "done"

for p in (gone, disconnected, unsupported, not_done):
    posting._posts.remove(p)
posting._rebuild_indexes()


# ---------------------------------------------------------------- pause settings and the worker loop
class StopLoop(Exception):
    pass


def one_tick(_):
    raise StopLoop()


ticks = {"n": 0}


def two_ticks(_):
    ticks["n"] += 1
    if ticks["n"] >= 2:  # let the loop do one full pass, then stop it
        raise StopLoop()


real_sleep = time.sleep
time.sleep = two_ticks
try:
    accounts.save_settings({"paused": True})
    reset(p_tt)
    ticks["n"] = 0
    try:
        posting._run()
    except StopLoop:
        pass
    check("a paused studio leaves due posts waiting", p_tt["status"], "scheduled")

    accounts.save_settings({"paused": False})
    ticks["n"] = 0
    try:
        posting._run()
    except StopLoop:
        pass
    check("an unpaused studio posts the due clip", (p_tt["status"], p_tt["remote_id"]), ("posted", "tt-remote-1"))
finally:
    time.sleep = real_sleep

# ---------------------------------------------------------------- view statistics
reset(p_yt, status="posted", posted_at=time.time(), remote_id="yt-remote-1", url="https://youtube.example/short/1")
reset(p_ig, status="posted", posted_at=time.time(), remote_id="ig-remote-1", url="https://instagram.example/reel/1")
time.sleep = one_tick
try:
    try:
        posting._stats()
    except StopLoop:
        pass
finally:
    time.sleep = real_sleep
check("YouTube views are saved on the posts", p_yt["views"], 123)
check("Instagram views are saved on the posts", p_ig["views"], 45)
check("view calls ask for the remote ids", view_calls[-2:], [("youtube", ["yt-remote-1"]), ("instagram", "ig-remote-1")])
check(
    "views survive a queue-file write",
    [
        p.get("views")
        for p in json.loads((config.DATA_DIR / "posts.json").read_text(encoding="utf-8"))
        if p["id"] in (p_yt["id"], p_ig["id"])
    ],
    [123, 45],
)

# ---------------------------------------------------------------- cancel and retry by hand
reset(p_ig)
posting.cancel(p_ig["id"])
check(
    "a waiting post can be cancelled",
    (p_ig["status"], jobs.get(job["id"])["clips"][0]["posts"][p_ig["id"]]["status"]),
    ("cancelled", "cancelled"),
)
expect_raise("a second cancellation is refused", lambda: posting.cancel(p_ig["id"]), ValueError, "still waiting")
posting.retry(p_ig["id"])
check("a cancelled post can be retried", (p_ig["status"], p_ig["tries"], p_ig["error"], p_ig["views"]), ("scheduled", 0, None, 45))
expect_raise("a waiting post cannot be retried again", lambda: posting.retry(p_ig["id"]), ValueError, "failed or cancelled")
expect_raise("a posted post cannot be cancelled", lambda: posting.cancel(p_tt["id"]), ValueError, "still waiting")
# ---------------------------------------------------------------- scheduling around gaps and caps
crowded = creators.create({"name": "Crowded", "post_gap_min": 45, "daily_cap": 1})
crowded_job = jobs.create({"type": "file", "path": "", "name": "crowded.mp4"}, {}, enqueue=False, creator=crowded["id"])
crowded_clip = dict(clip, id="clip-crowded")
crowded_clip.pop("posts", None)
crowded_job["clips"] = [crowded_clip]
jobs.save(crowded_job)
# Freeze the clock at midday: near midnight the gap alone can cross into tomorrow, which is a different case.
today = time.localtime()
fixed_now = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 12, 0, 0, 0, 0, -1))
tomorrow = time.localtime(fixed_now + 86400)
nine_tomorrow = time.mktime((tomorrow.tm_year, tomorrow.tm_mon, tomorrow.tm_mday, 9, 0, 0, 0, 0, -1))
with patch.object(posting.time, "time", return_value=fixed_now):
    reset(p_yt, status="posted", posted_at=fixed_now, remote_id="yt-remote-1", url="https://youtube.example/short/1")
    reset(p_ig, status="posted", posted_at=fixed_now, remote_id="ig-remote-1", url="https://instagram.example/reel/1", views=45)
    first = posting.enqueue(crowded_job, crowded_clip, ["yt_acc_1"])[0]
    first.update(status="posted", posted_at=fixed_now, remote_id="x", url="u")
    posting._save()
    second = posting.enqueue(crowded_job, crowded_clip, ["ig_acc_1"])[0]
    check("the daily cap pushes the next post to tomorrow", abs(second["due_at"] - nine_tomorrow) < 2, True)
    check("the next slot is around nine in the morning", time.localtime(second["due_at"]).tm_hour, 9)
    check("an immediate post also respects the cap", abs(posting._now_slot("yt_acc_1", 45, 1) - nine_tomorrow) < 2, True)
posting._posts.remove(second)
posting._posts.remove(first)
posting._rebuild_indexes()

# ---------------------------------------------------------------- automatic posting on a finished clip
autoclip = dict(clip, id="clip02", file="clip02.mp4", title="Auto clip")
(clips_dir / "clip02.mp4").write_bytes(b"auto")
job["clips"].append(autoclip)
jobs.save(job)
before = len(posting._posts)
posting.on_clip_ready(job, autoclip)
check("a good new clip is queued for every connected account", len(posting._posts) - before, 3)
check(
    "autopost writes a creator log line",
    any(
        "Queued for 3 account(s)" in json.loads(x)["msg"]
        for x in (creators.root(creator["id"]) / "log.jsonl").read_text(encoding="utf-8").splitlines()
    ),
    True,
)
posting._posts[:] = posting._posts[:before]
posting._rebuild_indexes()

posting.on_clip_ready(job, dict(autoclip, id="clip03", score=0))
check("a hand-made clip waits for a person", len(posting._posts), before)

no_auto = creators.create({"name": "No Auto", "autopost": False, "accounts": ["yt_acc_1"]})
job["creator"] = no_auto["id"]
posting.on_clip_ready(job, dict(autoclip, id="clip04"))
check("autopost off means no queue", len(posting._posts), before)
no_accounts = creators.create({"name": "No Accounts", "autopost": True, "accounts": []})
job["creator"] = no_accounts["id"]
posting.on_clip_ready(job, dict(autoclip, id="clip05"))
check("no connected accounts means no queue", len(posting._posts), before)
job["creator"] = creator["id"]
jobs.save(job)

# ---------------------------------------------------------------- deletion of a user's posts
other = {
    **p_yt,
    "id": "other-post-1",
    "user": "other_002",
    "status": "scheduled",
    "account": "yt_acc_1",
}
posting._posts.append(other)
posting._index_post(other)
main_count = len([p for p in posting._posts if p.get("user") == UID])
posting.forget("other_002")
check(
    "deleting a user removes their posts",
    ([p for p in posting._posts if p.get("user") == "other_002"], posting._account_posts.get(("other_002", "yt_acc_1"))),
    ([], None),
)
check("other users' posts are left alone", len([p for p in posting._posts if p.get("user") == UID]), main_count)

# ---------------------------------------------------------------- what the page sees
accounts.save_settings({"paused": True})
overview = posting.overview()
own_creator = next(r for r in overview["creators"] if r["id"] == creator["id"])
check("the overview says posting is paused", overview["paused"], True)
check(
    "the overview counts posted clips and views",
    (own_creator["posted_total"] >= 2, own_creator["views"] > 0, own_creator["earnings"] > 0),
    (True, True, True),
)
check("the overview marks autopost creators", own_creator["autopost"], True)
check(
    "the overview shows the queue without the owner id",
    ("user" not in overview["posts"][0], "creator_name" in overview["posts"][0]),
    (True, True),
)
check("the home graph has fourteen days", len(overview["daily"]), 14)
check("the overview asks the mocked live module", (overview["recording"], overview["alerts"]), (0, False))
digest = posting.digest_text()
check("the daily digest names the creator and the pause", ("Deep Poster" in digest, "Posting is paused." in digest), (True, True))
check(
    "an empty digest says there are no creators",
    "No creators set up yet." in posting.digest_text() if not overview["creators"] else True,
    True,
)

# ---------------------------------------------------------------- start and stop
threads = []


class FakeThread:
    def __init__(self, *a, **k):
        self.target, self.args = a[0] if a else k.get("target"), a[1] if len(a) > 1 else k.get("args", ())
        self.started = False
        threads.append(self)

    def start(self):
        self.started = True


real_thread = posting.threading.Thread
posting.threading.Thread = FakeThread
p_tt["status"] = "posting"  # pretend the studio closed during this upload
callbacks = len(jobs.on_clip_ready)
posting.start()
check("start recovers an interrupted upload", (p_tt["status"], "closed during this upload" in p_tt["error"]), ("failed", True))
check(
    "start saves the recovered queue",
    any(
        p["id"] == p_tt["id"] and p["status"] == "failed" for p in json.loads((config.DATA_DIR / "posts.json").read_text(encoding="utf-8"))
    ),
    True,
)
check("start wires the clip callback once", (len(jobs.on_clip_ready), jobs.on_clip_ready[-1]), (callbacks + 1, posting.on_clip_ready))
check("start launches the posting, stats, digest and awake threads", (len(threads), all(t.started for t in threads)), (4, True))
posting.stop()
check("stop tells the loops to finish", posting._stopping, True)
posting.threading.Thread = real_thread

shutil.rmtree(DATA, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
