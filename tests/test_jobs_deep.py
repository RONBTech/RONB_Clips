"""Deeper, direct checks of studio/jobs.py: options, queueing, clips, workers and recovery.

The pipeline (ffmpeg, downloads, transcription and the AI) is replaced with small recording fakes, so a real
job can be run from start to finish against tiny local files without touching a video encoder or the network.
"""

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="ronbclips-jobs-deep-"))
os.environ["RONBCLIPS_DATA"] = str(DATA)
os.environ.update(
    DATABASE_URL=os.environ.get("RONBCLIPS_TEST_DATABASE_URL", ""),
    RONBCLIPS_ACCOUNTS="0",
    RONBCLIPS_UPDATE_CHECK="0",
    RONBCLIPS_FETCH_MODEL="0",
)
sys.path.insert(0, str(ROOT))

from studio import auth, config, creators, ctx, db, jobs, notices  # noqa: E402

db.init()
UID = auth.LOCAL_ID
fails = []
created = []


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


def raises(fn, kind=Exception):
    try:
        fn()
    except kind:
        return True
    except Exception:
        return False
    return False


def reset_q():
    q = jobs._q
    with q.cv:
        q.quick.clear()
        q.lines.clear()
        q.turn.clear()
        q.epoch = 0
        q.running = 0
        q.held = False
        q.last_start = 0.0
        q._closed = False
        q._pos = (0.0, {})


def new_job(**overrides):
    source = {"type": "file", "path": str(DATA / "source.mp4"), "name": "source.mp4"}
    job = jobs.create(source, {}, enqueue=False, **overrides)
    created.append(job["id"])
    return job


def clip(cid, **extra):
    out = dict(
        id=cid,
        start=2.0,
        end=12.0,
        score=80,
        category="funny",
        hook="WOW",
        title="A clip",
        description="A description",
        hashtags=["funny"],
        highlight_words=[],
        reason="It was funny",
        style="bounce",
        layout="blur",
        captions=False,
        status="queued",
        version=0,
    )
    out.update(extra)
    return out


with ctx.as_user(UID):
    # ---------------------------------------------------------------- option validation
    check("unknown and empty options are dropped", jobs.clean_options({"unknown": 3, "style": "", "quality": None}), {})
    check(
        "numbers are clamped into their allowed range",
        jobs.clean_options({"max_clips": 99, "min_len": 1, "quality": 5000}),
        {"max_clips": 20, "min_len": 5, "quality": 1080},
    )
    check("numbers may be strings", jobs.clean_options({"max_clips": "7.8"}), {"max_clips": 7})
    check(
        "a valid style and layout are kept",
        jobs.clean_options({"style": "bounce", "layout": "gameplay"}),
        {"style": "bounce", "layout": "gameplay"},
    )
    check("an unknown style falls back", jobs.clean_options({"style": "nope"}), {"style": jobs.DEFAULTS["style"]})
    check("an unknown layout falls back", jobs.clean_options({"layout": "nope"}), {"layout": jobs.DEFAULTS["layout"]})
    check("captions must be a real boolean", raises(lambda: jobs.clean_options({"captions": "yes"}), ValueError), True)
    check("a bad start time is refused", raises(lambda: jobs.clean_options({"range_start": -1}), ValueError), True)
    check("a non-finite end time is refused", raises(lambda: jobs.clean_options({"range_end": float("inf")}), ValueError), True)
    check("the end must follow the start", raises(lambda: jobs.clean_options({"range_start": 20, "range_end": 20}), ValueError), True)
    check("a valid range is kept", jobs.clean_options({"range_start": 1.5, "range_end": 3.5}), {"range_start": 1.5, "range_end": 3.5})

    # ---------------------------------------------------------------- the fair queue on its own
    q = jobs._FairQueue()
    q.put(("fn", "quick", None))
    q.put(("job", "a1", None), user="u1")
    q.put(("job", "a2", None), user="u1")
    q.put(("job", "b1", None), user="u2")
    q.put(("job", "b2", None), user="u2")
    check("quick work goes first", q.get(), ("fn", "quick", None))
    check("the queue reports positions round-robin", q.ahead(), {"a1": 0, "b1": 1, "a2": 2, "b2": 3})
    with patch(config, "JOB_WORKERS", 10), patch(jobs.load, "_force", False):
        q.last_start = time.time() - 10
        q.running = 1  # a second job may start now that the first one has been running for six seconds
        check("users take turns", [q.get()[1], q.get()[1], q.get()[1], q.get()[1]], ["a1", "b1", "a2", "b2"])
        check("a finished line disappears", (q.lines, list(q.turn)), ({}, []))
        q.running = 2
        q.done()
        check("done() releases a slot", q.running, 1)

    q2 = jobs._FairQueue()
    with patch(config, "JOB_WORKERS", 2), patch(jobs.load, "_force", True):
        q2.running = 1
        q2.last_start = time.time() - 10
        q2.put(("job", "held", None), user="u1")
        got = []
        t = threading.Thread(target=lambda: got.append(q2.get()), daemon=True)
        t.start()
        time.sleep(0.15)
        check("a busy machine holds new videos", q2.held, True)
        jobs.load._force = False
        t.join(3)
    check("the held video starts once the machine has room", got, [("job", "held", None)])
    check("the thread finished", t.is_alive(), False)

    q2.close()
    check("closing the queue tells workers to stop", q2.get(), ("stop", None, None))
    q2.reopen()
    q2.put(("fn", "after", None))
    check("reopening the queue allows work again", q2.get(), ("fn", "after", None))

    # ---------------------------------------------------------------- create / save / list
    reset_q()
    job = new_job()
    jid = job["id"]
    check("a job is created queued", (job["status"], job["stage"], job["progress"]), ("queued", "Waiting", 0.0))
    check("a new job is a typed record", isinstance(job, jobs.Job), True)
    check("a job lives in the right user folder", job["dir"], str(config.user_dir(UID) / "jobs" / jid))
    check("the job folder and file exist", (Path(job["dir"]).is_dir(), (Path(job["dir"]) / "job.json").is_file()), (True, True))
    check(
        "defaults are filled in",
        {k: job["options"][k] for k in ("max_clips", "min_len", "max_len", "style", "layout", "quality")},
        {"max_clips": 6, "min_len": 15, "max_len": 60, "style": "bounce", "layout": "blur", "quality": 720},
    )
    check("a job is visible to its owner", jobs.get(jid)["id"], jid)
    check("job_dir finds the folder", jobs.job_dir(jid), Path(job["dir"]))
    check("adding a job bumps the list structure version", jobs.struct_version() >= 1, True)
    check("a new job is in the full list", jid in [j["id"] for j in jobs.list_jobs()], True)
    check("a new job is in changed_since(None)", jid in [j["id"] for j in jobs.changed_since(None)], True)

    enqueued = new_job()
    reset_q()
    auto_queued = jobs.create({"type": "file", "path": "", "name": "q.mp4"}, {}, enqueue=True)
    created.append(auto_queued["id"])
    check("create() queues the job unless told not to", list(jobs._q.lines[UID]), [("job", auto_queued["id"], None)])
    reset_q()
    jobs.enqueue(enqueued)
    check("enqueue() puts the job on its owner's line", list(jobs._q.lines[UID]), [("job", enqueued["id"], None)])
    made = []
    jobs.submit(lambda: made.append("ran"))
    check("submit() uses the quick line", jobs._q.quick[0][0], "fn")
    jobs.submit_live(lambda: made.append("live"))
    check("submit_live() uses the live line", jobs._live_q.qsize() >= 1, True)
    jobs._live_q.get_nowait()

    creator = creators.create({"name": "Deep Creator", "max_clips": 3, "style": "zoom"})
    cjob = jobs.create({"type": "file", "path": "", "name": "c.mp4"}, {}, creator=creator["id"], enqueue=False)
    created.append(cjob["id"])
    check("a creator's video goes in their workspace", cjob["dir"].startswith(str(creators.root(creator["id"]) / "videos")), True)
    check("a creator's settings become the defaults", (cjob["options"]["max_clips"], cjob["options"]["style"]), (3, "zoom"))
    check(
        "min_len is pulled below max_len when needed",
        jobs.create({}, {"min_len": 100, "max_len": 20}, enqueue=False)["options"]["min_len"],
        15,
    )

    before = ctx.version(UID)
    jobs._set(job, progress=0.5, stage="Working")
    check("a visual update bumps the page version", ctx.version(UID) > before, True)
    check(
        "a visual update does not rewrite the job file",
        json.loads((Path(job["dir"]) / "job.json").read_text(encoding="utf-8"))["progress"],
        0.0,
    )
    jobs._set(job, status="running", stage="Now running")
    check(
        "a structural update is written to disk",
        json.loads((Path(job["dir"]) / "job.json").read_text(encoding="utf-8"))["status"],
        "running",
    )
    check("changed jobs are sent on the next check", jid in [j["id"] for j in jobs.changed_since(before)], True)
    check("all_jobs() sees every job", len(jobs.all_jobs()) >= 4, True)
    check("busy_count() counts this user's unfinished videos", jobs.busy_count() >= 4, True)
    check("waiting_total() counts queued videos", jobs.waiting_total() >= 3, True)
    check("queue_epoch() reports queue movement", jobs.queue_epoch() >= 0, True)
    check("running_now() reports active slots", jobs.running_now(), 0)

    # public() hides server details and explains a wait
    job["clips"] = [clip("c1", src="/secret/a.mp4", fixed_words=[{"w": "x"}], fixed_for=[1, 2, "en2"])]
    job["ai"] = {"model": "secret-model", "tokens": 12, "note": "secret error", "log": ["secret log"]}
    job["source_path"] = "/secret/video.mp4"
    view = jobs.public(job)
    check("public() hides paths and the owner", [k for k in ("dir", "user", "source_path", "recover") if k in view], [])
    check("public() hides a clip's source and fixes", [k for k in ("src", "fixed_words", "fixed_for") if k in view["clips"][0]], [])
    check("public() hides the AI model and raw log", view["ai"], {"tokens": 12, "note": "secret error", "used": True})

    class FakeQ:
        held = True

        def ahead(self):
            return {jid: 2}

    job["status"] = "queued"
    with patch(jobs, "_q", FakeQ()):
        check("public() explains a busy queue", jobs.public(job)["stage"], "High demand right now: 2 videos ahead of yours")
        FakeQ.held = False
        check("public() explains a normal queue", jobs.public(job)["stage"], "Waiting in line: 2 videos ahead of yours")
        FakeQ.ahead = lambda self: {jid: 0}
        FakeQ.held = True
        check("public() says this video is next", jobs.public(job)["stage"], "High demand right now: yours is next")

    # words are read once, cached, and written atomically
    job["clips"] = []
    words_file = Path(job["dir"]) / "words.json"
    words_file.write_text(json.dumps([{"w": "hello", "s": 1.0, "e": 1.5}]), encoding="utf-8")
    jobs._words_cache.pop(jid, None)
    check("read_words() reads the transcript", jobs.read_words(job), [{"w": "hello", "s": 1.0, "e": 1.5}])
    words_file.unlink()
    check("read_words() uses the cache on the next call", jobs.read_words(job), [{"w": "hello", "s": 1.0, "e": 1.5}])
    jobs.write_words(job, [{"w": "new", "s": 2.0, "e": 2.5}])
    check("write_words() writes the transcript", json.loads(words_file.read_text(encoding="utf-8")), [{"w": "new", "s": 2.0, "e": 2.5}])
    check("write_words() updates the cache", jobs.read_words(job), [{"w": "new", "s": 2.0, "e": 2.5}])
    words_file.write_text("not json", encoding="utf-8")
    jobs._words_cache.pop(jid, None)
    check("a broken transcript is treated as empty", jobs.read_words(job), [])

    # ---------------------------------------------------------------- clip editing
    reset_q()
    job["clips"] = [clip("c1")]
    jobs.save(job)
    jobs.request_render(jid, "c1", {"start": 3, "end": 20, "captions": False, "hook": "H" * 80, "style": "not-a-style"})
    edited = job["clips"][0]
    check("request_render() changes the times", (edited["start"], edited["end"]), (3.0, 20.0))
    check("request_render() changes captions", edited["captions"], False)
    check("request_render() limits the hook", edited["hook"], "H" * 60)
    check("request_render() fixes an unknown style", edited["style"], jobs.DEFAULTS["style"])
    check("request_render() queues a re-render", list(jobs._q.quick), [("clip", jid, "c1")])
    check("request_render() validates a bad time", raises(lambda: jobs.request_render(jid, "c1", {"start": -1}), ValueError), True)
    check(
        "request_render() validates a short clip", raises(lambda: jobs.request_render(jid, "c1", {"start": 3, "end": 4}), ValueError), True
    )
    check("request_render() validates captions", raises(lambda: jobs.request_render(jid, "c1", {"captions": "no"}), ValueError), True)

    live_clip = clip("live1", src_offset=10.0, src_end=15.0, start=11.0, end=14.0)
    job["clips"] = [live_clip]
    jobs.save(job)
    jobs.request_render(jid, "live1", {"start": 1, "end": 99})
    saved_live_clip = job["clips"][0]
    check("a live clip cannot be moved outside its saved stream", (saved_live_clip["start"], saved_live_clip["end"]), (10.0, 15.0))
    check("clips kept in a job are typed records", isinstance(saved_live_clip, jobs.Clip), True)

    check("hashtags from text are cleaned", jobs.clean_hashtags("#one two! 3"), ["one", "two", "3"])
    check("hashtags from a list are cleaned", jobs.clean_hashtags(["good", "", None, "bad!tag"]), ["good", "badtag"])
    check("hashtags must be words", raises(lambda: jobs.clean_hashtags(7), ValueError), True)
    check("hashtags are limited to eight", len(jobs.clean_hashtags(["tag" + str(i) for i in range(40)])), 8)
    check("hashtags are limited to 30 characters", jobs.clean_hashtags(["x" * 50]), ["x" * 30])

    side = Path(job["dir"]) / "clips" / "c9.json"
    side.parent.mkdir(exist_ok=True)
    side.write_text(json.dumps({"title": "old"}), encoding="utf-8")
    text_clip = clip("c9", hashtags_in_description=True, description="Watch this", hashtags=["old"])
    jobs.save_post_text(job, text_clip, {"title": "New", "description": "Look #at this", "hashtags": "fun stuff"})
    check("post text is saved on the clip", (text_clip["title"], text_clip["hashtags"]), ("New", ["fun", "stuff"]))
    check("hashtags are folded into the description", text_clip["description"], "Look this\n\n#fun #stuff")
    check("post text is saved in the sidecar too", json.loads(side.read_text(encoding="utf-8"))["title"], "New")
    side.write_text("broken", encoding="utf-8")
    check("a broken sidecar does not stop saving", jobs.save_post_text(job, text_clip, {"title": "Again"}), None)

    words_file.write_text(json.dumps([{"w": "hello", "s": 3.0}, {"w": "world", "s": 5.0}, {"w": "outside", "s": 13.0}]), encoding="utf-8")
    jobs._words_cache.pop(jid, None)
    check("spoken_in() returns only words inside the clip", jobs.spoken_in(job, clip("x", start=2.0, end=12.0)), "hello world")

    # ---------------------------------------------------------------- rendering one clip
    rendered = []
    failures = []
    job["width"], job["height"] = 1280, 720
    job["clips"] = []
    render_clip = clip("r1")
    job["clips"] = [render_clip]
    jobs.on_clip_ready.append(lambda j, c: rendered.append(c["id"]))
    with (
        patch(jobs.render, "render_clip", lambda src, out, name, local, near, w, h: {"file": name + ".mp4", "cover": name + ".jpg"}),
        patch(jobs.creators, "log", lambda *a: None),
    ):
        jobs.render_one(job, render_clip, [])
    check(
        "render_one() marks a finished clip done", (render_clip["status"], render_clip["version"], render_clip["error"]), ("done", 1, None)
    )
    check("render_one() writes the clip sidecar", (Path(job["dir"]) / "clips" / "r1.json").is_file(), True)
    check("render_one() calls the ready callbacks once", rendered, ["r1"])
    with (
        patch(jobs.render, "render_clip", lambda *a: (_ for _ in ()).throw(RuntimeError("encoder failed"))),
        patch(jobs.creators, "log", lambda *a: failures.append(a)),
    ):
        jobs.render_one(job, clip("r2"), [])
    check("a failed clip is marked as an error", failures[0][1].startswith("Clip failed:"), True)

    cap_clip = clip("cap", start=2.0, end=8.0, captions=True)
    cap_words = [{"w": "hello", "s": 2.2, "e": 2.8}, {"w": "world", "s": 3.0, "e": 3.5}]
    called = []
    with (
        patch(jobs.usage, "set_context", lambda *a: None),
        patch(jobs.brain, "english_captions", lambda *a, **kw: (called.append(1), cap_words)[1]),
    ):
        near = jobs._caption_words(job, cap_clip, cap_words, 1.5)
    check("captions are tidied once per span", len(called), 1)
    check("captions are shifted to the clip's own time", [(round(w["s"], 2), round(w["e"], 2)) for w in near], [(0.7, 1.3), (1.5, 2.0)])
    check("the tidied words are cached on the clip", cap_clip["fixed_for"], [2.0, 8.0, "en2"])
    cap_clip["fixed_words"] = cap_words
    with patch(jobs.usage, "set_context", lambda *a: None), patch(jobs.brain, "english_captions", lambda *a, **kw: called.append(2)):
        jobs._caption_words(job, cap_clip, [], 1.5)
    check("captions are not tidied twice for the same span", len(called), 1)

    done_future = type(
        "F", (), {"n": 0, "done": lambda self: (setattr(self, "n", self.n + 1), self.n > 1)[1], "result": lambda self: "ok"}
    )()
    with patch(jobs.creators, "log", lambda *a: None):
        jobs._wait_for_clips(job, [(done_future, clip("wait1"))], {}, 1)
    check("_wait_for_clips() collects finished clips", done_future.n >= 2, True)
    stuck = type("F", (), {"done": lambda self: False})()
    stalled = clip("wait2")
    with patch(jobs, "CLIP_MINUTES", 0.0001), patch(jobs.creators, "log", lambda *a: None):
        jobs._wait_for_clips(job, [(stuck, stalled)], {stalled["id"]: time.time() - 100}, 1)
    check("a stuck clip is stopped and marked as an error", stalled["status"], "error")

    # ---------------------------------------------------------------- a whole job, from audio to clips
    source = DATA / "tiny.mp4"
    source.write_bytes(b"tiny video")
    full = new_job()
    full["source"] = {"type": "file", "path": str(source), "name": "tiny.mp4"}
    words = [{"w": f"word{i}", "s": float(i) + 0.1, "e": float(i) + 0.9} for i in range(12)]
    found = clip("found", captions=True, start=2.0, end=12.0)
    find_calls = []
    transcription_calls = []
    notice_calls = []
    summary_logs = []

    def fake_render(src, out, name, local, near, w, h):
        Path(out).mkdir(parents=True, exist_ok=True)  # render_clip normally creates this
        return {"file": name + ".mp4", "cover": name + ".jpg"}

    def fake_find(bw, db_, dur, title, uploader, quota, min_len, max_len, note, game=""):
        find_calls.append((len(bw), quota, game))
        return [dict(found, id="")], {"tokens": 12, "model": "fake-model"}

    def fake_transcribe(audio, progress=None):
        transcription_calls.append(len(audio))
        return [dict(w) for w in words]

    with (
        patch(jobs.media, "probe", lambda p: {"width": 1280, "height": 720, "duration": 30.0}),
        patch(jobs.media, "save_thumb", lambda *a, **kw: True),
        patch(jobs.media, "extract_audio", lambda *a: None),
        patch(jobs.media, "load_audio", lambda p: np.zeros(30 * 16000, dtype=np.int16)),
        patch(jobs.transcribe, "model_ready", lambda: True),
        patch(jobs.transcribe, "transcribe", fake_transcribe),
        patch(jobs.brain, "find_clips", fake_find),
        patch(jobs.brain, "english_captions", lambda raw, *a, **kw: raw),
        patch(jobs.hashtags, "apply", lambda *a: None),
        patch(jobs.render, "render_clip", fake_render),
        patch(notices, "add", lambda *a: notice_calls.append(a)),
        patch(jobs.creators, "log", lambda *a: summary_logs.append(a)),
    ):
        jobs._run_job(full)
    check("a full job ends as done", (full["status"], full["stage"], full["progress"]), ("done", "Done", 1.0))
    check("a full job records the video's details", (full["duration"], full["thumb"], full["width"]), (30.0, True, 1280))
    check("a full job writes the transcript", (Path(full["dir"]) / "words.json").is_file(), True)
    check(
        "a full job finds and renders a clip",
        (len(full["clips"]), full["clips"][0]["status"], full["clips"][0]["id"]),
        (1, "done", "clip01"),
    )
    check("clips get the chosen options", (full["clips"][0]["style"], full["clips"][0]["layout"]), ("bounce", "blur"))
    check("a full job records AI use", full["ai"]["tokens"], 12)
    check("a full job transcribes the audio", len(transcription_calls) >= 1, True)
    check("a full job sends one notice", len(notice_calls), 1)
    check("the notice says how many clips are ready", notice_calls[0][2], "1 clip ready")
    check("a full job takes a recorded time", isinstance(full.get("took"), int), True)

    # the same job after a restart: keep its transcript, make only the unfinished clip
    resume = new_job()
    resume["source"] = {"type": "file", "path": str(source), "name": "tiny.mp4"}
    resume["resume"] = True
    resume["done_blocks"] = 1
    resume["clips"] = [clip("again", status="queued", captions=False)]
    (Path(resume["dir"]) / "words.json").write_text(json.dumps(words), encoding="utf-8")
    stages = []
    real_set = jobs._set

    def record_set(job, **kw):
        if "stage" in kw:
            stages.append(kw["stage"])
        return real_set(job, **kw)

    find_calls.clear()
    with (
        patch(jobs, "_set", record_set),
        patch(jobs.media, "probe", lambda p: {"width": 1280, "height": 720, "duration": 30.0}),
        patch(jobs.media, "save_thumb", lambda *a, **kw: False),
        patch(jobs.media, "extract_audio", lambda *a: None),
        patch(jobs.media, "load_audio", lambda p: np.zeros(30 * 16000, dtype=np.int16)),
        patch(jobs.transcribe, "model_ready", lambda: True),
        patch(jobs.brain, "find_clips", fake_find),
        patch(jobs.hashtags, "apply", lambda *a: None),
        patch(jobs.render, "render_clip", fake_render),
        patch(notices, "add", lambda *a: None),
        patch(jobs.creators, "log", lambda *a: None),
    ):
        jobs._run_job(resume)
    check("a resumed job says it is picking up", "Picking up where it stopped" in stages, True)
    check("a resumed job does not ask the AI for clips again", find_calls, [])
    check("a resumed job renders its unfinished clip", (resume["status"], resume["clips"][0]["status"]), ("done", "done"))

    # ---------------------------------------------------------------- helpers for long videos and YouTube
    span_words = [{"w": str(i), "s": float(i * 2), "e": float(i * 2) + 0.5} for i in range(15)]
    check("a borrowed transcript that reaches the end is usable", jobs._covers(span_words, 0, 30), True)
    check("a borrowed transcript that stops early is not usable", jobs._covers(span_words[:8], 0, 30), False)

    en_job = new_job()
    check("_english_part() is None for an uploaded video", jobs._english_part(en_job, clip("e")), None)
    en_job["source"] = {"type": "url", "url": "https://www.youtube.com/watch?v=deep"}
    en_file = Path(en_job["dir"]) / "words_en.json"
    en_file.write_text(json.dumps([{"w": "in", "s": 3.0}, {"w": "out", "s": 9.0}]), encoding="utf-8")
    check("_english_part() filters to this clip", jobs._english_part(en_job, clip("e", start=2, end=5)), [{"w": "in", "s": 3.0}])
    en_file.unlink()
    with patch(jobs.media, "youtube_english_words", lambda url: [{"w": "fetched", "s": 4.0}]):
        check(
            "_english_part() fetches translations once", jobs._english_part(en_job, clip("e", start=2, end=5)), [{"w": "fetched", "s": 4.0}]
        )
    check("the fetched translations are cached on disk", en_file.is_file(), True)

    piece_job = new_job()
    piece_job["source"] = {"type": "url", "url": "https://www.youtube.com/watch?v=deep"}
    piece_job["duration"] = 30
    piece = Path(piece_job["dir"]) / "clips" / "p1.src.mp4"
    piece.parent.mkdir(exist_ok=True)
    piece.write_bytes(b"piece")
    piece_clip = clip("p1", start=5, end=15)
    with (
        patch(jobs.media, "download_section", lambda *a, **kw: piece),
        patch(jobs.media, "probe", lambda p: {"duration": 12.0, "width": 1280, "height": 720}),
    ):
        jobs._piece_again(piece_job, piece_clip)
    check(
        "_piece_again() records where the saved piece begins",
        (piece_clip["src"], piece_clip["src_offset"], piece_clip["src_end"]),
        (str(piece), 0.0, 12.0),
    )
    check("_piece_again() trims the clip to the piece", piece_clip["end"], 12.0)
    check("_piece_again() learns the video's size", (piece_job["width"], piece_job["height"]), (1280, 720))
    with patch(jobs.media, "download_section", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("refused"))):
        check("a failed piece fetch is explained", raises(lambda: jobs._piece_again(piece_job, clip("p2")), RuntimeError), True)
    check("a video with no source cannot be fetched again", raises(lambda: jobs._piece_again(new_job(), clip("p3")), RuntimeError), True)

    fetch_clip = clip("f1", start=5, end=15)
    wav = piece.parent / "f1.wav"
    with (
        patch(jobs.media, "download_section", lambda *a, **kw: piece),
        patch(jobs.media, "extract_audio", lambda src, out: out.write_bytes(b"wav")),
        patch(jobs.media, "load_audio", lambda p: np.zeros(16000, dtype=np.int16)),
        patch(jobs.media, "probe", lambda p: {"duration": 12.0, "width": 1280, "height": 720}),
        patch(jobs.media, "find_offset", lambda *a: 3.0),
    ):
        jobs._fetch_piece(piece_job, fetch_clip, np.zeros(30 * 16000, dtype=np.int16))
    check("_fetch_piece() pins the true start of a piece", (fetch_clip["src_offset"], fetch_clip["src_end"]), (3.0, 15.0))
    check("_fetch_piece() deletes the temporary wav", wav.exists(), False)
    with patch(jobs.media, "download_section", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("refused"))):
        jobs._fetch_piece(piece_job, fetch_clip, np.zeros(30 * 16000, dtype=np.int16))
    check("a failed piece marks its clip as an error", fetch_clip["status"], "error")

    face_job = new_job()
    face_job["source_path"] = str(source)
    with patch(jobs.facecam, "detect", lambda path, ask=None: {"x": 10, "y": 20, "w": 30, "h": 40, "conf": "high"}):
        check("_facecam() uses a sure detection", jobs._facecam(face_job), [10, 20, 30, 40])
    with patch(jobs.facecam, "detect", lambda path, ask=None: {"conf": "low"}):
        check("_facecam() ignores an unsure detection", jobs._facecam(face_job), None)
    face_job["source_path"] = ""
    check("_facecam() does nothing without a local file", jobs._facecam(face_job), None)

    # ---------------------------------------------------------------- a YouTube link, with borrowed words
    url_job = new_job()
    url_job["source"] = {"type": "url", "url": "https://www.youtube.com/watch?v=deep"}
    url_audio = DATA / "url_audio.m4a"
    url_audio.write_bytes(b"audio")

    def fake_download(url, d, height=720):
        p = Path(d) / "source.mp4"
        p.write_bytes(b"video")
        return p

    def fake_download_audio(url, d, on_progress=None):
        if on_progress:
            on_progress(1.0)
        return url_audio

    with (
        patch(
            jobs.media,
            "info",
            lambda url: {"title": "URL video", "uploader": "Channel", "duration": 30.0, "is_live": False, "kind": "video", "thumbnail": ""},
        ),
        patch(jobs.media, "is_youtube", lambda url: True),
        patch(jobs.media, "youtube_words", lambda url: (span_words, "en")),
        patch(jobs.media, "save_thumb", lambda *a, **kw: True),
        patch(jobs.media, "download", fake_download),
        patch(jobs.media, "download_audio", fake_download_audio),
        patch(jobs.media, "extract_audio", lambda *a: None),
        patch(jobs.media, "load_audio", lambda p: np.zeros(30 * 16000, dtype=np.int16)),
        patch(jobs.media, "probe", lambda p: {"width": 1280, "height": 720, "duration": 30.0}),
        patch(jobs.transcribe, "model_ready", lambda: True),
        patch(jobs.transcribe, "transcribe", fake_transcribe),
        patch(jobs.brain, "find_clips", fake_find),
        patch(jobs.brain, "english_captions", lambda raw, *a, **kw: raw),
        patch(jobs.hashtags, "apply", lambda *a: None),
        patch(jobs.render, "render_clip", fake_render),
        patch(notices, "add", lambda *a: None),
        patch(jobs.creators, "log", lambda *a: None),
    ):
        jobs._run_job(url_job)
    check("a link job reports where its transcript came from", url_job["transcript_from"], "youtube")
    check("a link job keeps the spoken language", url_job["lang"], "en")
    check(
        "a link job finds a clip from borrowed words",
        (url_job["status"], len(url_job["clips"]), url_job["clips"][0]["status"]),
        ("done", 1, "done"),
    )
    check("a link job uses the downloaded video for the clip", Path(url_job["source_path"]).is_file(), True)
    check("a link job keeps its own word timings for edits", (Path(url_job["dir"]) / "words.json").is_file(), True)
    check("a link job reports progress to the download hook", url_job["progress"], 1.0)

    for bad_meta, phrase in (
        ({"title": "x", "uploader": "u", "duration": 10.0, "is_live": False, "kind": "playlist", "thumbnail": ""}, "channel or playlist"),
        ({"title": "x", "uploader": "u", "duration": 10.0, "is_live": True, "kind": "video", "thumbnail": ""}, "live stream"),
    ):
        bad_job = new_job()
        bad_job["source"] = {"type": "url", "url": "https://www.youtube.com/watch?v=bad"}
        with (
            patch(jobs.media, "info", lambda url, m=bad_meta: m),
            patch(jobs.transcribe, "model_ready", lambda: True),
            patch(jobs.media, "save_thumb", lambda *a, **kw: False),
        ):
            try:
                jobs._run_job(bad_job)
                message = ""
            except RuntimeError as e:
                message = str(e)
        check(f"a link to {phrase} is refused", phrase in message, True)

    # ---------------------------------------------------------------- workers, errors and stopping
    failed_job = new_job()
    failed_job["clips"] = [clip("c1")]
    jobs.save(failed_job)
    stop_seq = iter([("job", failed_job["id"], None), ("stop", None, None)])
    with (
        patch(jobs, "_stopping", False),
        patch(jobs._q, "get", lambda quick_only=False: next(stop_seq)),
        patch(jobs, "_run_job", lambda j: (_ for _ in ()).throw(RuntimeError("boom"))),
    ):
        jobs._worker()
    check("a worker writes the traceback to error.log", (Path(failed_job["dir"]) / "error.log").is_file(), True)
    check("a worker marks a failed video as an error", (failed_job["status"], failed_job["stage"]), ("error", "Stopped"))
    check("a worker marks unfinished clips as errors", failed_job["clips"][0]["status"], "error")

    worker_job = new_job()
    worker_job["clips"] = [clip("c2", captions=True)]
    jobs.save(worker_job)
    seen = []
    seq = iter([("clip", worker_job["id"], "c2"), ("stop", None, None)])
    with (
        patch(jobs, "_stopping", False),
        patch(jobs._q, "get", lambda quick_only=False: next(seq)),
        patch(jobs, "read_words", lambda j: [{"w": "hi"}]),
        patch(jobs, "render_one", lambda j, c, w: seen.append((j["id"], c["id"], w))),
    ):
        jobs._worker()
    check("the worker's clip path renders with the transcript", seen, [(worker_job["id"], "c2", [{"w": "hi"}])])

    runs = []
    seq = iter([("fn", ctx.bind(lambda: runs.append("fn")), None), ("job", "gone", None), ("stop", None, None)])
    with patch(jobs, "_stopping", False), patch(jobs._q, "get", lambda quick_only=False: next(seq)):
        jobs._worker()
    check("a worker runs submitted functions", runs, ["fn"])
    check("a worker ignores a job that was deleted", jobs.running_now(), 0)

    stopping_job = new_job()
    stopping_job["status"] = "running"
    jobs.save(stopping_job)
    jobs.stop(wait=False)
    check("stopping changes a running job back to queued", stopping_job["status"], "queued")
    check("stopping marks it to resume", stopping_job["resume"], True)
    check("stopping closes the queue", jobs._q._closed, True)
    jobs._live_q.get_nowait()  # the live worker's stop signal
    jobs._stopping = False

    # ---------------------------------------------------------------- reload and orphan recovery
    for jid in list(created):
        jobs.delete(jid)
    created.clear()
    jobs.reload()
    reset_q()
    before_reload = len(jobs.all_jobs())

    def write_on_disk(name, **data):
        d = config.user_dir(UID) / "jobs" / name
        (d / "clips").mkdir(parents=True, exist_ok=True)
        base = {
            "id": name,
            "user": UID,
            "kind": "video",
            "creator": None,
            "dir": str(d),
            "created": time.time(),
            "source": {"type": "file", "path": str(d.parent / name / "old.mp4"), "name": "old.mp4"},
            "options": {"captions": True},
            "status": "queued",
            "stage": "Waiting",
            "progress": 0.0,
            "title": name,
            "uploader": "",
            "duration": 0,
            "clips": [],
            "error": None,
            "ai": {},
        }
        base.update(data)
        (d / "job.json").write_text(json.dumps(base), encoding="utf-8")
        return d, base

    d1, _ = write_on_disk(
        "orphan-one",
        status="running",
        source_path="C:/old/place/video.mp4",
        clips=[clip("c1", status="rendering", src="C:/old/place/clip01.mp4")],
    )
    (d1 / "video.mp4").write_bytes(b"video")
    (d1 / "old.mp4").write_bytes(b"old")
    (d1 / "clips" / "clip01.mp4").write_bytes(b"clip")
    d2, _ = write_on_disk("orphan-live", kind="live", status="live", clips=[clip("c1", status="rendering")])
    (d2 / "buffer").mkdir()
    (d2 / "buffer" / "part.ts").write_bytes(b"ts")
    d3, _ = write_on_disk("orphan-remake", status="done", clips=[clip("c1", status="rendering")])
    (d3 / "words.json").write_text("[]", encoding="utf-8")
    d4, _ = write_on_disk("orphan-broken-clip", status="done", clips=[clip("c1", status="rendering")])
    (config.user_dir(UID) / "jobs" / "orphan-invalid").mkdir(parents=True)
    (config.user_dir(UID) / "jobs" / "orphan-invalid" / "job.json").write_text("not json", encoding="utf-8")

    jobs.reload()
    one = jobs.get("orphan-one")
    check(
        "a running video is queued again after a restart",
        (one["status"], one["resume"], one["stage"]),
        ("queued", True, "Picking up where it stopped"),
    )
    check("old file paths are found again by name", Path(one["source_path"]).is_file(), True)
    check("an old source path is remapped", Path(one["source"]["path"]).is_file(), True)
    check("an old clip path is remapped", Path(one["clips"][0]["src"]).is_file(), True)
    check("an unfinished clip is queued again on resume", one["clips"][0]["status"], "queued")
    check(
        "a live recording paused by a restart is finished",
        (jobs.get("orphan-live")["status"], jobs.get("orphan-live")["was_live"]),
        ("done", True),
    )
    check(
        "a live clip with its stream on disk can be recovered",
        (jobs.get("orphan-live")["clips"][0]["status"], jobs.get("orphan-live")["recover"]),
        ("queued", True),
    )
    check("a single clip with a transcript is queued again", jobs.get("orphan-remake")["clips"][0]["status"], "queued")
    check(
        "an interrupted clip without a transcript fails clearly",
        jobs.get("orphan-broken-clip")["clips"][0]["error"].startswith("Interrupted by a restart."),
        True,
    )
    check("a broken job file is ignored", jobs.get("orphan-invalid"), None)
    check("reload() re-reads jobs from disk", len(jobs.all_jobs()) >= before_reload + 4, True)
    queued_ids = [item[1] for line in jobs._q.lines.values() for item in line]
    check("restarted videos are put back on the queue", "orphan-one" in queued_ids, True)
    check("clips waiting to be remade are re-queued", ("clip", "orphan-remake", "c1") in jobs._q.quick, True)

    spawns = []
    with (
        patch(jobs.load, "start", lambda: None),
        patch(jobs, "reload", lambda: spawns.append("reload")),
        patch(jobs, "_spawn", lambda fn, *a: spawns.append((fn.__name__, a))),
    ):
        jobs.start()
    check("start() reloads jobs from disk", spawns[0], "reload")
    check(
        "start() creates a worker per slot plus quick and live workers",
        len([s for s in spawns if isinstance(s, tuple)]),
        max(1, config.JOB_WORKERS) + 2,
    )
    check("start() creates a live worker", spawns[-1][0], "_live_worker")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
