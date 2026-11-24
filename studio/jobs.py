"""Job pipeline: get video -> audio -> transcript -> AI picks -> rendered clips. One heavy task at a time."""

import collections
import copy
import json
import math
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np

from . import brain, config, creators, ctx, facecam, games, hashtags, load, logs, media, quality, render, roman, safe, transcribe, usage
from .models import Clip, Job


class _FairQueue:
    """Whose video is next. Each person has their own line and the lines take turns, so someone who adds ten
    videos does not make everyone else wait behind all ten. Small tasks (re-making one clip) go first."""

    def __init__(self):
        self.cv = threading.Condition()
        self.quick = collections.deque()
        self.lines = {}  # user -> their waiting videos, oldest first
        self.turn = collections.deque()  # users with something waiting, in the order they will be served
        self.epoch = 0  # goes up whenever the line moves
        self._pos = (0.0, {})
        self.running = 0  # videos being worked on right now
        self.last_start = 0.0
        self.held = False  # videos are waiting only because the machine is near full
        self._closed = False  # set on shutdown: no new work is accepted
        self.draining = False  # during shutdown, workers take what is already queued before leaving
        self.deadline: float | None = None

    def close(self, drain: bool = False, deadline: float | None = None):
        """Wake everyone waiting for work. With drain=True they take queued items first, then leave."""
        with self.cv:
            self._closed = True
            self.draining = drain
            self.deadline = deadline
            self.cv.notify_all()

    def reopen(self):
        """Allow work again after a stop (used when the app is started again in the same process)."""
        with self.cv:
            self._closed = False
            self.draining = False
            self.deadline = None

    def pending(self) -> int:
        """How many items are still waiting on this queue."""
        with self.cv:
            return len(self.quick) + sum(len(line) for line in self.lines.values())

    def closed(self) -> bool:
        with self.cv:
            return self._closed

    def _room(self) -> bool:
        """May one more video start? One always may. More only while the machine has room, and a few seconds
        after the last one started, so its weight is already showing in the measurements."""
        if self.running == 0:
            return True
        return self.running < config.JOB_WORKERS and time.time() - self.last_start > 6 and not load.busy()

    def done(self):
        with self.cv:
            self.running = max(0, self.running - 1)
            self.cv.notify_all()

    def put(self, item, user=None):
        with self.cv:
            if self._closed:
                return False  # the app is closing; new work must not be added to a draining queue
            if user is None:
                self.quick.append(item)
            else:
                if user not in self.lines:
                    self.lines[user] = collections.deque()
                    self.turn.append(user)
                self.lines[user].append(item)
            self.epoch += 1
            self.cv.notify()
            return True

    def get(self, quick_only=False):
        with self.cv:
            while True:
                time_left = None
                if self._closed:
                    time_left = None if self.deadline is None else self.deadline - time.monotonic()
                    if not self.draining or (time_left is not None and time_left <= 0):
                        return ("stop", None, None)
                    if self.quick:
                        self.epoch += 1
                        return self.quick.popleft()
                    if quick_only or not self.turn:
                        return ("stop", None, None)
                    # A normal worker keeps taking videos from the line while there is time left.
                if self.quick:
                    self.epoch += 1
                    return self.quick.popleft()
                if not quick_only and self.turn:
                    if self._room() or self.draining:
                        break
                    if not self.held:
                        self.held = True
                        self.epoch += 1
                elif self.held:
                    self.held = False
                    self.epoch += 1
                self.cv.wait(1.0 if time_left is None else max(0.05, min(1.0, time_left)))
            self.epoch += 1
            self.held = False
            self.running += 1
            self.last_start = time.time()
            user = self.turn.popleft()
            item = self.lines[user].popleft()
            if self.lines[user]:
                self.turn.append(user)
            else:
                del self.lines[user]
            return item

    def ahead(self) -> dict:
        """job id -> how many videos will start before it. Worked out at most once a second."""
        with self.cv:
            if time.time() - self._pos[0] < 1:
                return self._pos[1]
            out, lines, order, n = {}, {u: list(q) for u, q in self.lines.items()}, list(self.turn), 0
            depth = 0
            while order:
                nxt = []
                for u in order:
                    if depth < len(lines[u]):
                        out[lines[u][depth][1]] = n
                        n += 1
                        if depth + 1 < len(lines[u]):
                            nxt.append(u)
                order, depth = nxt, depth + 1
            self._pos = (time.time(), out)
            return out


_q = _FairQueue()
_live_q: "queue.Queue" = queue.Queue()  # live clips never wait behind a long video
_live_pending = 0  # queued live renders not yet finished, so shutdown can report what was drained
_live_lock = threading.Lock()
# One gate for new live work and the stop marker: a render can never slip in behind the marker during shutdown.
_live_submit_lock = threading.RLock()
_workers: list[threading.Thread] = []  # kept so shutdown can wait for work already in progress
_jobs: dict = {}
_by_user: dict = {}  # user -> {job id: job}: listing one person's videos never scans everyone's
_lock = threading.RLock()
on_clip_ready: list = []  # callbacks (job, clip), run once when a clip first finishes

# What the page needs to hear about, so it is not sent every video again on every check.
#   _changes[user][job id] = the number of changes that user had when that video last changed
#   _struct[user]          = goes up when a video is added or removed (the page then asks for the whole list)
_changes: dict = {}
_struct: dict = {}
_writing: dict = {}  # job id -> its own lock: one write of job.json at a time, without holding up everyone else
_writing_lock = threading.Lock()
_words_cache: dict = {}  # jid -> latest words.json: avoids rereading the same transcript for every clip
_words_cache_lock = threading.Lock()

DEFAULTS = {
    "max_clips": 6,
    "min_len": 15,
    "max_len": 60,
    "style": "bounce",
    "layout": "blur",
    "quality": 720,
    "range_start": None,
    "range_end": None,
    "captions": True,
}


LIMITS = {"max_clips": (1, 20), "min_len": (5, 120), "max_len": (10, 180), "quality": (360, 1080)}


def clean_options(options) -> dict:
    """Only known settings, each inside its allowed range. Raises ValueError on nonsense."""
    out = {}
    for k, v in (options if isinstance(options, dict) else {}).items():
        if k == "captions":
            if not isinstance(v, bool):
                raise ValueError("Captions must be on or off.")
            out[k] = v
            continue
        if k not in DEFAULTS or v in ("", None):
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            v = max(lo, min(hi, int(float(v))))
        elif k == "style":
            v = v if v in render.STYLES else DEFAULTS["style"]
        elif k == "layout":
            v = v if v in render.LAYOUTS else DEFAULTS["layout"]
        else:  # range_start, range_end: seconds into the video
            v = float(v)
            if not math.isfinite(v) or v < 0:
                raise ValueError("That part of the video isn't valid.")
        out[k] = v
    if out.get("range_end") is not None and out["range_end"] <= out.get("range_start", 0):
        raise ValueError("The end of the chosen part must be after its start.")
    return out


def job_dir(jid: str) -> Path:
    return Path(_jobs[jid]["dir"])


HIDDEN = ("dir", "user", "source_path", "recover")


def public(job: Job) -> dict:
    """What the browser may see: no server paths, no owner id."""
    out = {k: v for k, v in job.items() if k not in HIDDEN}
    out["source"] = {k: v for k, v in job["source"].items() if k != "path"}
    out["clips"] = [{k: v for k, v in c.items() if k not in ("src", "fixed_words", "fixed_for")} for c in job["clips"]]
    ai = job.get("ai") or {}
    out["ai"] = {
        "tokens": ai.get("tokens", 0),
        "note": ai.get("note", ""),
        "used": bool(ai.get("model")),
    }  # never the model, never raw errors
    if job.get("status") == "queued":
        n = _q.ahead().get(job["id"])
        if n:
            out["stage"] = (
                f"High demand right now: {n} video{'s' if n != 1 else ''} ahead of yours"
                if _q.held
                else f"Waiting in line: {n} video{'s' if n != 1 else ''} ahead of yours"
            )
        elif n == 0 and _q.held:
            out["stage"] = "High demand right now: yours is next"
    return out


def save(job: Job):
    """Write the job to its file, and note that this user has something new to hear about. The file is written
    without holding the lock everyone else needs, so a page asking for the list never waits on a disk."""
    user = job.get("user")
    ctx.bump(user)
    with _lock:
        if user:
            _changes.setdefault(user, {})[job["id"]] = ctx.version(user)
        try:
            snapshot = copy.deepcopy(job)
            text = json.dumps(snapshot.as_dict())
        except (RuntimeError, TypeError):
            return
        d = Path(job["dir"])
    with _writing_lock:
        mine = _writing.setdefault(job["id"], threading.Lock())
    with mine:
        tmp = d / "job.json.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(d / "job.json")


def read_words(job):
    """Read a job's transcript once, then reuse the snapshot while it is being processed."""
    jid = job["id"]
    with _words_cache_lock:
        if jid in _words_cache:
            return [dict(w) for w in _words_cache[jid]]
    try:
        words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        words = []
    with _words_cache_lock:
        _words_cache[jid] = words
    return [dict(w) for w in words]


def write_words(job, words):
    """Update the cached transcript and its atomic job-file copy together."""
    jid = job["id"]
    snapshot = [dict(w) for w in words]
    with _words_cache_lock:
        _words_cache[jid] = snapshot
    d = Path(job["dir"])
    tmp = d / "words.json.tmp"
    tmp.write_text(json.dumps(snapshot), encoding="utf-8")
    tmp.replace(d / "words.json")


def _set(job, **kw):
    with _lock:
        job.update(kw)
    visual_only = set(kw) <= {"progress", "stage", "word_count"}
    if visual_only:
        ctx.bump(job.get("user"))
        with _lock:
            if job.get("user"):
                _changes.setdefault(job["user"], {})[job["id"]] = ctx.version(job["user"])
        return
    save(job)


def _remap(path, folder: Path):
    """Paths saved before data moved into per-user folders (or on another machine): find the file by name."""
    if path and not Path(path).exists():
        cand = folder / Path(str(path).replace("\\", "/")).name
        if cand.exists():
            return str(cand)
    return path


def _load_file(f: Path, uid: str):
    try:
        job = Job.model_validate(json.loads(f.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return
    job.update(dir=str(f.parent), user=uid)
    job.setdefault("kind", "video")
    job.setdefault("creator", None)
    job.setdefault("options", {}).setdefault("captions", True)
    if job.get("source_path"):
        job["source_path"] = _remap(job["source_path"], f.parent)
    if job.get("source", {}).get("path"):
        job["source"]["path"] = _remap(job["source"]["path"], f.parent)
    if job.get("status") in ("queued", "running") and job.get("kind") != "live":
        # Picked up again as soon as the server is back (jobs.start), from the last finished part of the transcript.
        job.update(status="queued", stage="Picking up where it stopped", error=None, resume=True)
    if job.get("status") == "live":
        job.update(status="done", stage="Recording paused by a server update", was_live=True)
    buffered = job.get("kind") == "live" and any((f.parent / "buffer").glob("*.ts"))
    for c in job.get("clips", []):
        c.setdefault("captions", job["options"]["captions"])
        if c.get("src"):
            c["src"] = _remap(c["src"], f.parent / "clips")
        if c.get("status") in ("rendering", "queued"):
            if buffered and not c.get("src"):
                c["status"] = "queued"  # the stream is still on disk: live.recover() finishes these
                job["recover"] = True
            elif job.get("resume"):
                c["status"] = "queued"  # the video is being picked up again: this clip is made then
            elif job.get("kind") != "live" and (f.parent / "words.json").is_file():
                c["status"] = "queued"  # a single clip being re-made: jobs.start() makes it again
                job.setdefault("remake", []).append(c["id"])
            else:
                c.update(status="error", error="Interrupted by a restart. Open it and press Save and re-make.")
    _jobs[job["id"]] = job
    _by_user.setdefault(uid, {})[job["id"]] = job


def load_user(uid: str):
    d = config.USERS_DIR / uid
    for f in list((d / "jobs").glob("*/job.json")) + list((d / "creators").glob("*/*/*/job.json")):
        _load_file(f, uid)


def load_all():
    for d in config.USERS_DIR.iterdir():
        if d.is_dir():
            load_user(d.name)


def all_jobs() -> list[Job]:
    """Every user's jobs. Only for background work; never send this to a browser."""
    with _lock:
        return list(_jobs.values())


def list_jobs() -> list[Job]:
    u = ctx.uid()
    with _lock:
        return sorted((copy.deepcopy(j) for j in _by_user.get(u, {}).values()), key=lambda j: -j["created"])


def struct_version() -> int:
    """Goes up when this user adds or removes a video. The page sends back the one it last saw, so it knows
    whether what it is holding is still the whole list."""
    with _lock:
        return _struct.get(ctx.uid(), 0)


def changed_since(since, full: bool = False) -> list[Job]:
    """The videos to send this user: all of them when `full` (or when the page's place is unknown), otherwise only
    those that changed after `since`. Newest first, like list_jobs."""
    u = ctx.uid()
    with _lock:
        mine = _by_user.get(u, {})
        if full or since is None:
            return sorted((copy.deepcopy(j) for j in mine.values()), key=lambda j: -j["created"])
        seen = _changes.get(u, {})
        return sorted((copy.deepcopy(j) for jid, j in mine.items() if seen.get(jid, 0) > since), key=lambda j: -j["created"])


def get(jid: str) -> Job | None:
    """The job, only if it belongs to whoever this code is running for."""
    job = _jobs.get(jid)
    return job if job and job["user"] == ctx.uid() else None


def busy_count() -> int:
    u = ctx.uid()
    return sum(1 for j in list(_by_user.get(u, {}).values()) if j["kind"] == "video" and j["status"] in ("queued", "running"))


def waiting_total() -> int:
    return sum(1 for j in list(_jobs.values()) if j["kind"] == "video" and j["status"] == "queued")


def queue_epoch() -> int:
    return _q.epoch


def running_now() -> int:
    return _q.running


def queue_closed() -> bool:
    """Whether the queue has stopped accepting new work (during shutdown)."""
    return _q.closed()


def create(source: dict, options: dict, enqueue=True, creator=None, kind="video") -> Job:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:10]
    c = creators.get(creator) if creator else None
    if c:
        d = creators.root(c["id"]) / ("live" if kind == "live" else "videos") / jid
        base = {**DEFAULTS, **{k: c[k] for k in ("max_clips", "min_len", "max_len", "style", "layout", "captions")}}
    else:
        d, base, creator = config.user_dir(ctx.uid()) / "jobs" / jid, dict(DEFAULTS), None
    opts = {**base, **clean_options(options)}
    if opts["min_len"] >= opts["max_len"]:
        opts["min_len"] = max(5, opts["max_len"] - 5)
    job = Job(
        id=jid,
        user=ctx.uid(),
        kind=kind,
        creator=creator,
        dir=str(d),
        created=time.time(),
        source=source,
        options=opts,
        status="queued",
        stage="Waiting",
        progress=0.0,
        title=source.get("name", ""),
        uploader="",
        duration=0,
        clips=[],
        error=None,
        ai={},
    )
    d.mkdir(parents=True)
    with _lock:
        _jobs[jid] = job
        _by_user.setdefault(job["user"], {})[jid] = job
        _struct[job["user"]] = _struct.get(job["user"], 0) + 1  # a new video: the page needs the whole list
    save(job)
    if enqueue:
        _q.put(("job", jid, None), user=job["user"])
    return job


def enqueue(job: Job):
    save(job)
    _q.put(("job", job["id"], None), user=job["user"])


def submit(fn):
    """Run any heavy task (a render, a cleanup) on the single worker, in order."""
    if not _q.put(("fn", ctx.bind(fn), None)):
        logs.emit("worker.skipped", "warning", msg="A queued task was skipped because RONB Clips is closing.")


def submit_live(fn):
    """Live-stream renders and cleanups: their own worker, so a long video can never hold them up."""
    global _live_pending
    with _live_submit_lock:
        if _stopping:
            logs.emit("worker.skipped", "warning", msg="A live render was skipped because RONB Clips is closing.")
            return False
        with _live_lock:
            _live_pending += 1
        _live_q.put(ctx.bind(fn))
        return True


def _live_worker():
    global _live_pending
    while True:
        fn = _live_q.get()
        if fn is None:
            return  # the only way to stop: everything queued before this marker has been taken first
        try:
            with ctx.as_context(ctx.uid_or_none(), ctx.request_id() or logs.background_id("live", "render")):
                fn()
        except Exception:  # noqa: BLE001
            logs.emit("worker.error", "error", msg="A live render failed.", error=traceback.format_exc()[-1000:])
        finally:
            with _live_lock:
                _live_pending = max(0, _live_pending - 1)


def delete(jid):
    with _lock:
        job = _jobs.pop(jid, None)
        if job:
            _by_user.get(job["user"], {}).pop(jid, None)
            _changes.get(job["user"], {}).pop(jid, None)
            _struct[job["user"]] = _struct.get(job["user"], 0) + 1  # gone: the page needs the whole list again
    if job:
        ctx.bump(job["user"])
        with _writing_lock:
            _writing.pop(jid, None)
        with _words_cache_lock:
            _words_cache.pop(jid, None)
        shutil.rmtree(job["dir"], ignore_errors=True)
    return bool(job)


def request_render(jid, cid, changes: dict):
    job = _jobs[jid]
    clip = next(c for c in job["clips"] if c["id"] == cid)
    new = dict(clip)
    if "captions" in changes:
        new.update(clean_options({"captions": changes["captions"]}))
    for k in ("start", "end"):
        if k in changes:
            new[k] = round(float(changes[k]), 2)
    if "src_offset" in clip:  # live clip: can only move inside the saved piece of stream
        new["start"] = max(new["start"], clip["src_offset"])
        new["end"] = min(new["end"], clip["src_end"])
    if not (math.isfinite(new["start"]) and math.isfinite(new["end"])) or new["start"] < 0:
        raise ValueError("Those times aren't valid.")
    if not 3 <= new["end"] - new["start"] <= 180:
        raise ValueError("A clip can be 3 to 180 seconds long.")
    for k in ("hook", "title", "description", "style", "layout"):
        if k in changes:
            new[k] = str(changes[k])[: {"hook": 60, "title": 100, "description": 2000}.get(k, 20)]
    if "hashtags" in changes:
        new["hashtags"] = clean_hashtags(changes["hashtags"])
    if new.get("style") not in render.STYLES:
        new["style"] = DEFAULTS["style"]
    if new.get("layout") not in render.LAYOUTS:
        new["layout"] = DEFAULTS["layout"]
    clip.update(new, status="queued")
    save(job)
    _q.put(("clip", jid, cid))


def clean_hashtags(v) -> list:
    """Convert typed hashtags or a list from the page into plain words, with a safe limit."""
    if isinstance(v, str):
        v = v.split()
    if not isinstance(v, list):
        raise ValueError("Hashtags must be words separated by spaces.")
    tags = [re.sub(r"\W", "", str(h))[:30] for h in v[:50] if isinstance(h, (str, int))]
    return [t for t in tags if t][:8]


def save_post_text(job, clip, changes: dict):
    """Save title, description and hashtags without re-rendering the video."""
    text: dict[str, str | list[str]] = {
        k: str(changes[k] or "")[: {"title": 100, "description": 2000}[k]] for k in ("title", "description") if k in changes
    }
    if "hashtags" in changes:
        text["hashtags"] = clean_hashtags(changes["hashtags"])
    if clip.get("hashtags_in_description") and not clip.get("fixed_description") and text:
        desc = hashtags._TAG_IN_TEXT.sub("", str(text.get("description", clip.get("description") or ""))).strip()
        line = " ".join("#" + t for t in text.get("hashtags") or clip.get("hashtags") or [])
        text["description"] = ((desc + "\n\n" + line).strip() if desc else line)[:2000]
    with _lock:
        clip.update(text)
    save(job)
    side = Path(job["dir"]) / "clips" / f"{clip['id']}.json"
    try:
        if side.is_file():
            side.write_text(json.dumps({**json.loads(side.read_text(encoding="utf-8")), **text}, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def spoken_in(job: Job, clip: Clip) -> str:
    """The words spoken inside one clip, for rewriting post text."""
    words = read_words(job)
    return " ".join(w.get("w", "") for w in words if clip["start"] <= w.get("s", -1) <= clip["end"])


def render_one(job, clip, words):
    d = Path(job["dir"])
    clip["status"] = "rendering"
    save(job)
    try:
        if not clip.get("src") and not job.get("source_path"):
            _piece_again(job, clip)
        off = clip.get("src_offset", 0.0)
        src = Path(clip["src"]) if clip.get("src") else Path(job["source_path"])
        if not clip.get("src"):
            clip["end"] = min(clip["end"], job["duration"])
        local = dict(
            clip, start=clip["start"] - off, end=clip["end"] - off, hook=roman.to_roman(clip.get("hook") or "", job.get("lang") or "")
        )
        near = _caption_words(job, clip, words, off) if clip.get("captions", True) else []
        out = render.render_clip(src, d / "clips", clip["id"], local, near, job["width"], job["height"])
        clip.update(out, status="done", version=clip.get("version", 0) + 1, error=None)
        post = {k: clip[k] for k in ("title", "description", "hashtags", "hook", "start", "end", "score")}
        (d / "clips" / f"{clip['id']}.json").write_text(json.dumps(post, indent=1), encoding="utf-8")
        if clip["version"] == 1:
            clip.setdefault("made_at", time.time())
            creators.log(job["creator"], f"Clip ready (score {clip['score']}): {clip['title'] or clip['hook']}")
            save(job)
            for fn in on_clip_ready:
                try:
                    fn(job, clip)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=safe.friendly(e, 400))
        creators.log(job["creator"], f"Clip failed: {safe.friendly(e, 200)}")
    save(job)


def _caption_words(job, clip, words, off):
    span = [round(clip["start"], 2), round(clip["end"], 2), "en2"]  # "en2": captions made always-English
    if (
        clip.get("fixed_for") == span
        and clip.get("fixed_words")
        and not any(roman.needs_roman(w.get("w", "")) for w in clip["fixed_words"])
    ):
        raw = clip["fixed_words"]  # already tidied for this exact span: no second AI call
    else:
        raw = [w for w in words if clip["start"] - 1 <= w["s"] <= clip["end"] + 1]
        try:
            usage.set_context(job.get("creator"), job.get("title") or "")
            raw = brain.english_captions(
                raw, job.get("lang") or "", job.get("title") or "", youtube_english=lambda: _english_part(job, clip)
            )
            clip.update(fixed_words=raw, fixed_for=span)
        except Exception:  # noqa: BLE001 - captions still render with the words as heard
            pass
    # the last word on it: whatever was said, nothing is drawn in Hindi, Urdu or Nepali script
    return [dict(w, s=w["s"] - off, e=w["e"] - off) for w in roman.words(raw, job.get("lang") or "")]


CLIP_MINUTES = float(config.env("RONBCLIPS_CLIP_MINUTES", "15"))  # the longest one clip may take to make


def _wait_for_clips(job, futures, began, workers):
    """Wait for a video's clips, but not for ever. A clip that hangs (a download that stalls, an encoder that
    freezes) used to keep the whole video "in progress", and no other clip could be re-made. Now it is marked as
    failed after CLIP_MINUTES and the video finishes with the clips that did work."""
    left, given_up = list(futures), 0
    late = "This clip took too long and was stopped. Open it and press Save and re-make to try again."
    while left:
        still = []
        for f, clip in left:
            if f.done():
                f.result()
                continue
            t = began.get(clip["id"])
            if (t and time.time() - t > CLIP_MINUTES * 60) or (not t and given_up >= workers):
                given_up += 1
                clip.update(status="error", error=late)
                creators.log(
                    job["creator"], f"Clip stopped after {CLIP_MINUTES:g} minutes: {clip.get('title') or clip.get('hook') or clip['id']}"
                )
                save(job)
                continue
            still.append((f, clip))
        left = still
        if left:
            time.sleep(1)


def _english_part(job, clip):
    """YouTube's English captions for just this clip, for a video in another language. Fetched once per video."""
    if (job.get("source") or {}).get("type") != "url":
        return None
    f = Path(job["dir"]) / "words_en.json"
    if f.is_file():
        en = json.loads(f.read_text(encoding="utf-8"))
    else:
        try:
            en = media.youtube_english_words(job["source"]["url"]) or []
        except Exception:  # noqa: BLE001 - YouTube often refuses its translations: the AI translates instead
            en = []
        f.write_text(json.dumps(en), encoding="utf-8")  # asked once per video, refused or not
    return [w for w in en if clip["start"] - 0.5 <= w["s"] <= clip["end"] + 0.5]


LONG_VIDEO = 40 * 60  # above this, fetch only the pieces that become clips
PAD = 8  # seconds kept either side of a fetched piece, so clips can be re-trimmed


def _piece_again(job, clip):
    """Remaking a clip whose own piece of the video was never fetched (its first try failed) and whose whole video
    is not here: fetch the piece now, cut exactly at the asked time, so no sound matching is needed."""
    src = job.get("source") or {}
    if src.get("type") != "url" or not src.get("url"):
        raise RuntimeError("The original video is no longer here, so this clip can't be made again. Add the video again.")
    d = Path(job["dir"])
    want = max(0.0, clip["start"] - PAD)
    until = clip["end"] + PAD if not job.get("duration") else min(job["duration"], clip["end"] + PAD)
    try:
        piece = media.download_section(
            src["url"], d / "clips", f"{clip['id']}.src", want, until, int(job["options"]["quality"]), exact=True
        )
        meta = media.probe(piece)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Could not fetch this part of the video: {safe.friendly(e, 250)}") from e
    clip.update(src=str(piece), src_offset=round(want, 2), src_end=round(want + meta["duration"], 2))
    clip["end"] = min(clip["end"], clip["src_end"])
    job.setdefault("width", meta["width"])
    job.setdefault("height", meta["height"])


def _fetch_piece(job, clip, full_audio):
    """Long videos: download just this clip's stretch and pin down exactly where it starts."""
    d = Path(job["dir"])
    o = job["options"]
    try:
        want = max(0.0, clip["start"] - PAD)
        piece = media.download_section(
            job["source"]["url"], d / "clips", f"{clip['id']}.src", want, min(job["duration"], clip["end"] + PAD), int(o["quality"])
        )
        wav = d / "clips" / f"{clip['id']}.wav"
        media.extract_audio(piece, wav)
        part = media.load_audio(wav)
        wav.unlink(missing_ok=True)
        meta = media.probe(piece)
        off = media.find_offset(full_audio, part, want)
        clip.update(src=str(piece), src_offset=round(off, 2), src_end=round(off + meta["duration"], 2))
        clip["start"], clip["end"] = max(clip["start"], clip["src_offset"]), min(clip["end"], clip["src_end"])
        job.setdefault("width", meta["width"])
        job.setdefault("height", meta["height"])
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=f"Could not fetch this part of the video: {safe.friendly(e, 250)}")


BLOCK = 10 * 60  # long videos are transcribed and clipped one block at a time
PROGRESSIVE_FROM = 20 * 60


def _covers(words: list, start: float, end: float) -> bool:
    """Is a borrowed transcript good enough to find clips with? It has to reach the end of the part being clipped
    and have a normal amount of talking in it; otherwise our own model writes the transcript."""
    inside = [w for w in words if start <= w["s"] <= end]
    minutes = max(0.5, (end - start) / 60)
    return len(inside) >= 25 * minutes and inside[-1]["s"] >= start + 0.85 * (end - start)


def _facecam(job) -> list | None:
    """Where the webcam is in the source: once for the whole video, for every clip of it.

    Only a sure answer is used. A box that is a scoreboard drawn over the game would
    put a picture of a scoreboard in the corner of the clip. One frame is also shown to
    the AI, which tells a person from a game character better than any measurement can;
    what it says is measured again on the picture, so it cannot do any harm."""
    path = job.get("source_path")
    if not path or not Path(path).is_file():
        return None
    try:
        box = facecam.detect(path, ask=brain.look_for_facecam)
    except Exception:  # noqa: BLE001 - a missing facecam is not a failure
        return None
    if not box or box.get("conf") != "high":
        return None
    job["facecam"] = [box["x"], box["y"], box["w"], box["h"]]
    return job["facecam"]


def _run_job(job):
    import math
    from concurrent.futures import ThreadPoolExecutor

    d = Path(job["dir"])
    o = job["options"]
    src = job["source"]
    # Which game this is: what the creator set, or what the title says. It decides the extra rules the AI
    # reads and the tags every post carries.
    game = job.get("game") or ""
    if not game and job.get("creator"):
        try:
            game = (creators.get(job["creator"]) or {}).get("game") or ""
        except Exception:  # noqa: BLE001
            game = ""
    if not game:
        game = games.detect(job["title"], src.get("name"))[0] or ""
    if game and job.get("game") != game:
        job["game"] = game
    t0 = time.time()
    notes = []
    face = False  # where the webcam is in the source, found once; False means not looked for yet
    usage.set_context(job["creator"], job["title"] or src.get("name"))
    resuming = bool(job.pop("resume", False)) and (d / "words.json").is_file()
    if resuming:  # after a restart: keep the transcript parts, clips and AI answers already made, and carry on
        _set(job, status="running", stage="Picking up where it stopped", error=None)
    else:
        _set(job, status="running", stage="Getting the audio", progress=0.03, error=None, clips=[], ai={}, done_blocks=0)
    if not transcribe.model_ready() and not transcribe.REMOTE:
        from . import fetch_model

        _set(job, stage="Getting the speech model (one time, about 480 MB)")
        fetch_model.ensure()
        _set(job, stage="Getting the audio")
    fetch = ThreadPoolExecutor(1)
    video = None
    if src["type"] == "url":
        meta = media.info(src["url"])
        if meta["kind"] != "video":
            raise RuntimeError("That link is a channel or playlist, not a single video. Paste the link of one video.")
        if ((o["range_end"] or meta["duration"]) - (o["range_start"] or 0)) > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That is longer than {config.MAX_VIDEO_HOURS:g} hours. Choose a shorter part of it under Options.")
        if meta["is_live"]:
            raise RuntimeError(
                "This link is a live stream, so it would never finish downloading. Open the creator, "
                "put this link under Setup, and press Start recording instead."
            )
        _set(job, title=meta["title"], uploader=meta["uploader"], duration=meta["duration"])
        if not job.get("thumb") and media.save_thumb(d, url=src["url"]):
            _set(job, thumb=True)
        usage.set_context(job["creator"], meta["title"])
        span = (o["range_end"] or meta["duration"]) - (o["range_start"] or 0)
        pieces = span > LONG_VIDEO or meta["duration"] <= 0
        if not pieces:  # shorter video: fetch all of it in the background while the audio is worked on
            video = fetch.submit(media.download, src["url"], d, int(o["quality"]))
        audio_file = media.download_audio(src["url"], d, lambda p: _set(job, progress=0.03 + 0.1 * p))
    else:
        pieces, audio_file = False, Path(src["path"])
        info = media.probe(audio_file)
        if info["duration"] > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That video is longer than {config.MAX_VIDEO_HOURS:g} hours, which is the limit.")
        _set(job, source_path=str(audio_file), width=info["width"], height=info["height"], duration=info["duration"])
        if not job.get("thumb") and media.save_thumb(d, video=audio_file):
            _set(job, thumb=True)
    wav = d / "audio.wav"
    media.extract_audio(audio_file, wav)
    audio = media.load_audio(wav)
    wav.unlink(missing_ok=True)
    if src["type"] == "url":
        audio_file.unlink(missing_ok=True)
    if not job["duration"]:
        job["duration"] = len(audio) / 16000
    lo = int((o["range_start"] or 0) * 16000)
    hi = min(len(audio), int(o["range_end"] * 16000)) if o["range_end"] else len(audio)
    span = (hi - lo) / 16000
    db = media.loudness_per_second(audio[lo:hi])

    vlock, vstate = threading.Lock(), {}

    def whole_video():
        """Wait for the background download once; retry it, or give up and use pieces."""
        with vlock:
            if "path" not in vstate:
                path = None
                try:
                    path = video.result() if video else None
                except Exception as e:  # noqa: BLE001 - sites sometimes refuse a download
                    notes.append(f"Video download refused once ({str(e)[-80:]}), retrying")
                    try:
                        path = media.download(src["url"], d, int(o["quality"]))
                    except Exception as e2:  # noqa: BLE001
                        notes.append(f"Whole-video download failed ({str(e2)[-80:]}), fetching clip parts instead")
                if path:
                    info = media.probe(path)
                    job.update(source_path=str(path), width=info["width"], height=info["height"])
                vstate["path"] = path
            return vstate["path"]

    # The ad breaks and static screens of the whole video, found once, from the file when it is here already (an
    # uploaded video, or one downloaded before a restart). A video fetched in pieces has no whole file, and for a
    # video still downloading this would stall the transcript: those clips are made without the map.
    statics = []
    if config.SKIP_STATIC and job.get("source_path") and Path(job["source_path"]).exists():
        try:
            statics = quality.static_ranges(job["source_path"])
        except Exception:  # noqa: BLE001 - no static map is not a failure
            statics = []

    exact = {}  # clip id -> (from, to, words): our own model's words for just that clip

    began = {}  # clip id -> when work on it started
    audio = []  # filled after extraction; initial value keeps nested clip work type-safe

    def make(clip, words_now):
        nonlocal audio
        began[clip["id"]] = time.time()
        if src["type"] == "url" and (pieces or not whole_video()):
            _fetch_piece(job, clip, audio)
        if clip["status"] != "error" and clip.get("captions", True) and yt and yt[1].lower().startswith("en"):
            # The clip was found with YouTube's transcript. What is shown on screen comes from our own model,
            # run on just this clip: full punctuation, nothing blanked out, timings to the word.
            a0, b0 = max(lo, int((clip["start"] - 1.5) * 16000)), min(hi, int((clip["end"] + 1.5) * 16000))
            try:
                tw = [
                    dict(w, s=round(w["s"] + a0 / 16000, 2), e=round(w["e"] + a0 / 16000, 2))
                    for w in transcribe.transcribe(np.asarray(audio[a0:b0]))
                ]
            except Exception:  # noqa: BLE001 - the clip is still made, with YouTube's words
                tw = []
            if len(tw) >= 3:
                exact[clip["id"]] = (a0 / 16000, b0 / 16000, tw)
                words_now = tw
        if clip["status"] != "error":
            render_one(job, clip, words_now)

    cpus = os.cpu_count() or 4
    renders = ThreadPoolExecutor(2 if cpus < 8 else min(6, cpus // 6))  # more clips made at once on a bigger machine
    futures = []
    step = BLOCK * 16000 if span > PROGRESSIVE_FROM else hi - lo
    blocks = [(x, min(hi, x + step)) for x in range(lo, hi, step)] or [(lo, hi)]
    words, tokens, model = [], 0, None
    done_blocks = 0
    yt = None
    if src["type"] == "url" and config.YT_TRANSCRIPT and media.is_youtube(src["url"]):
        try:
            yt = media.youtube_words(src["url"])
        except Exception:  # noqa: BLE001 - no transcript to borrow: ours is written as usual
            yt = None
        if yt and not _covers(yt[0], lo / 16000, hi / 16000):
            yt = None
        job["transcript_from"] = "youtube" if yt else "own"
        job["lang"] = yt[1] if yt else ""  # the language spoken, when YouTube says (decides the captions)
    if resuming:
        words = read_words(job)
        done_blocks = min(int(job.get("done_blocks", 0)), len(blocks)) if words else 0
        tokens, model = int((job.get("ai") or {}).get("tokens", 0)), (job.get("ai") or {}).get("model")
        if done_blocks < len(blocks):  # words past the last finished part are transcribed again
            cut = blocks[done_blocks][0] / 16000
            words = [w for w in words if w["s"] < cut]
        for c in job["clips"]:  # clips found before the restart that were not finished yet
            if c.get("status") not in ("done", "error"):
                c["status"] = "queued"
                futures.append((renders.submit(ctx.bind(make), c, list(words)), c))
        save(job)
    # The transcript of the next part is written while the AI is still reading this one (the AI is a wait on the
    # network, the transcript is work on this machine), so neither waits for the other.
    ahead = ThreadPoolExecutor(2, thread_name_prefix="ahead")
    heard, now_on = {}, [done_blocks]

    def hear(i):
        nonlocal audio
        if i < len(blocks) and i >= done_blocks and i not in heard:
            a_, b_ = blocks[i]
            last = [0.0]

            def tp(p, i=i, a_=a_, b_=b_):
                if now_on[0] == i and p - last[0] > 0.05:  # only the part being shown moves the bar
                    last[0] = p
                    _set(job, progress=max(job["progress"], 0.14 + 0.78 * ((a_ - lo) + p * (b_ - a_)) / max(1, hi - lo)))

            heard[i] = ahead.submit(ctx.bind(transcribe.transcribe), audio[a_:b_], tp)

    for bi, (a, b) in enumerate(blocks):
        if bi < done_blocks:
            continue  # transcribed and checked by the AI before the restart
        shift = a / 16000
        where = f" (minute {round((a - lo) / 16000 / 60)} of {round(span / 60)})" if len(blocks) > 1 else ""
        if yt:
            _set(job, stage="Reading the transcript" + where)
            bw = [dict(w, s=round(w["s"] - shift, 2), e=round(w["e"] - shift, 2)) for w in yt[0] if a / 16000 <= w["s"] < b / 16000]
        else:
            _set(job, stage="Writing the transcript" + where)
            now_on[0] = bi
            hear(bi)
            hear(bi + 1)
            bw = heard.pop(bi).result()
        _set(job, progress=max(job["progress"], 0.14 + 0.78 * (b - lo) / max(1, hi - lo)))
        words += [dict(w, s=round(w["s"] + shift, 2), e=round(w["e"] + shift, 2)) for w in bw]
        write_words(job, words)
        _set(job, stage="Finding the best moments" + where, word_count=len(words))
        left = int(o["max_clips"]) - len(job["clips"])
        if left <= 0 or len(bw) < 8:
            job["done_blocks"] = bi + 1
            save(job)
            continue
        quota = max(1, math.ceil(left / (len(blocks) - bi)))
        s0 = int((a - lo) / 16000)
        found, ai = brain.find_clips(
            bw,
            db[s0 : s0 + int((b - a) / 16000) + 1],
            (b - a) / 16000,
            job["title"],
            job["uploader"],
            quota,
            int(o["min_len"]),
            int(o["max_len"]),
            notes.append,
            game=game,
        )
        tokens += ai.get("tokens", 0)
        model = ai.get("model") or model
        if ai.get("note"):
            notes.append(ai["note"])
        if found and statics:
            # A commercial or a "be right back" slate is not a clip. Any clip that sits mostly inside one
            # is dropped.
            keep = []
            for c in found:
                a_s, b_s = c["start"] + shift, c["end"] + shift
                inside = quality.static_overlap(a_s, b_s, statics)
                if inside > 0.5 * max(1.0, b_s - a_s):
                    notes.append(f"A clip from {round(a_s)}s was left out: it sat in an ad break or a static screen.")
                else:
                    keep.append(c)
            found = keep
        for c in found:
            c = Clip.model_validate(c)  # keep the same object that is stored, so renders update the job
            c.update(
                id=f"clip{len(job['clips']) + 1:02d}",
                start=round(c["start"] + shift, 2),
                end=round(c["end"] + shift, 2),
                style=o["style"],
                layout=o["layout"],
                captions=o.get("captions", True),
                status="queued",
                version=0,
            )
            hashtags.apply(c, words, job["title"], job["uploader"], game)
            # Only the gameplay layout uses a webcam, so it is only looked for then, and only once: a
            # stream with no webcam is not looked for again on every clip of it.
            if o["layout"] == "gameplay" and face is False:
                face = _facecam(job)
            if face:
                c["facecam"] = face
            job["clips"].append(c)
            futures.append((renders.submit(ctx.bind(make), c, list(words)), c))
        job["ai"] = {"model": model, "tokens": tokens, "note": "", "log": notes[-5:]}
        job["done_blocks"] = bi + 1  # a restart from here on carries on with the next part
        save(job)
    if futures:
        _set(job, stage="Finishing the clips", progress=max(job["progress"], 0.92))
    _wait_for_clips(job, futures, began, renders._max_workers)
    if exact:  # keep our own words for the clips in the saved transcript, so editing a clip later uses them
        for a_s, b_s, tw in exact.values():
            words = [w for w in words if not a_s <= w["s"] < b_s] + tw
        words.sort(key=lambda w: w["s"])
        write_words(job, words)
    renders.shutdown(wait=False)
    ahead.shutdown(wait=False)
    fetch.shutdown(wait=False)
    del audio
    clips = job["clips"]
    job["ai"] = {
        "model": model,
        "tokens": tokens,
        "log": notes[-5:],
        "note": "The AI was unavailable, so clips were picked by loudness only." if clips and not model else "",
    }
    stage = "Done" if clips else "Done, but nothing clip-worthy was found"
    _set(job, status="done", stage=stage, progress=1.0, took=round(time.time() - t0))
    try:
        from . import notices

        name = job.get("title") or job["source"].get("name") or "your video"
        notices.add(
            job.get("user"),
            "clips",
            f"{len(clips)} clip{'s' if len(clips) != 1 else ''} ready" if clips else "Nothing clip-worthy found",
            name,
            f"/creators/{job['creator']}" if job.get("creator") else "/videos",
        )
    except Exception:  # noqa: BLE001
        pass
    creators.log(job["creator"], f"Video finished with {len(clips)} clips in {round(time.time() - t0)}s: {job['title']}")


def _worker(quick_only=False):
    while True:
        kind, a, cid = _q.get(quick_only)
        if kind == "stop":
            return
        if kind == "fn" and a:
            try:
                a()
            except Exception:  # noqa: BLE001
                logs.emit("worker.error", "error", msg="A queued task failed.", error=traceback.format_exc()[-1000:])
            continue
        job = _jobs.get(a)
        if not job:
            if kind == "job":
                _q.done()
            continue
        # Work already on the queue must finish during shutdown; only new submissions are refused.
        rid = job.get("request_id") or logs.background_id("job" if kind == "job" else "clip", str(a))
        token = ctx._uid.set(job["user"])  # everything this job does runs as its owner
        rid_token = ctx._request_id.set(rid)
        try:
            logs.emit("job.started", msg="Started working.", job_id=a, kind=kind, clip_id=cid or "")
            if kind == "job":
                _run_job(job)
            else:
                clip = next(c for c in job["clips"] if c["id"] == cid)
                words = []
                if clip.get("captions", True):
                    words = read_words(job)
                render_one(job, clip, words)
            logs.emit("job.finished", msg="Finished working.", job_id=a, kind=kind, clip_id=cid or "")
        except Exception as e:  # noqa: BLE001
            (Path(job["dir"]) / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            logs.emit("job.failed", "error", msg="Work failed.", job_id=a, kind=kind, error=safe.friendly(e)[:200])
            if kind == "job":
                for c in job["clips"]:
                    if c.get("status") in ("queued", "rendering"):
                        c.update(status="error", error="The video stopped before this clip was made.")
                _set(job, status="error", error=safe.friendly(e), stage="Stopped")
                creators.log(job["creator"], f"Video failed: {safe.friendly(e)[:200]}")
        finally:
            ctx._uid.reset(token)
            ctx._request_id.reset(rid_token)
            if kind == "job":
                _q.done()


def _pick_up_after_restart():
    """Videos and single clips that were being made when the server stopped: queued again, oldest first."""
    waiting = sorted((j for j in all_jobs() if j.get("status") == "queued" and j.get("resume")), key=lambda j: j["created"])
    for j in waiting:
        _q.put(("job", j["id"], None), user=j["user"])
    for j in all_jobs():
        for cid in j.pop("remake", []):
            _q.put(("clip", j["id"], cid))
        if j.get("status") == "done":
            save(j)


def reload():
    """Forget the in-memory copy and read the saved state again, exactly as a fresh process does on start."""
    global _stopping, _live_pending
    with _live_submit_lock:
        kept = []
        while True:  # a stop marker left by an earlier stop must not kill the new live worker
            try:
                item = _live_q.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                kept.append(item)
        for item in kept:
            _live_q.put(item)
        with _live_lock:
            _live_pending = len(kept)
        _stopping = False
    with _lock:
        _jobs.clear()
        _by_user.clear()
        _changes.clear()
        _struct.clear()
    with _words_cache_lock:
        _words_cache.clear()
    _q.reopen()
    load_all()
    _pick_up_after_restart()


def start():
    load.start()
    reload()
    _workers.clear()
    for _ in range(max(1, config.JOB_WORKERS)):
        _workers.append(_spawn(_worker))
    _workers.append(_spawn(_worker, True))  # re-making a single clip never waits behind whole videos
    _workers.append(_spawn(_live_worker))


def _spawn(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


_stopping = False
DRAIN_SECONDS = float(config.env("RONBCLIPS_DRAIN_SECONDS", "30") or 30)  # longest a shutdown waits for a job to finish


def _mark_resumable():
    """Anything unfinished when the deadline ran out is put back on the queue at the next start."""
    with _lock:
        jobs_left = list(_jobs.values())
    for job in jobs_left:
        if job.get("status") in ("queued", "running", "live"):
            job["resume"] = True
            if job.get("status") == "running":
                job["status"] = "queued"
            save(job)


def drain(timeout: float | None = None, join: bool = True) -> dict:
    """Finish everything already queued, then stop the workers.

    New work is refused as soon as this starts. The fair queue keeps serving items that were already waiting, and the
    live queue gets its stop marker *after* those items, so a late clip render is not silently dropped. A single
    deadline bounds the whole wait; anything left when it expires is marked for recovery at the next start.
    """
    global _stopping
    seconds = DRAIN_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + max(0.0, seconds)
    with _live_submit_lock:
        _stopping = True
        waiting = _q.pending() + _live_pending
        _q.close(drain=join and seconds > 0, deadline=deadline if join else time.monotonic())
        _live_q.put(None)
    if join:
        for worker in list(_workers):  # a clip already rendering is allowed to finish before the process exits
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)
        _workers.clear()
    left = _q.pending() + _live_pending
    _mark_resumable()
    report = {"drained": max(0, waiting - left), "abandoned": left, "seconds": round(seconds, 2)}
    logs.emit(
        "worker.drain",
        msg="Finished the queued work before closing.",
        drained=report["drained"],
        abandoned=report["abandoned"],
    )
    return report


def stop(wait: bool = True) -> dict:
    """Backwards-compatible name for drain(). Shutdown waits; callers in a hurry can use wait=False."""
    if wait:
        return drain()
    return drain(timeout=0, join=False)
